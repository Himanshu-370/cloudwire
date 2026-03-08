from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from .graph_store import GraphStore
from .models import JobStatus, ScanMode


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _progress_percent(done: int, total: int) -> int:
    if total <= 0:
        return 0
    return max(0, min(100, int((done / total) * 100)))


@dataclass
class CacheEntry:
    job_id: str
    expires_at: datetime


@dataclass
class ScanJob:
    id: str
    cache_key: str
    account_id: str
    region: str
    services: List[str]
    mode: ScanMode
    include_iam_inference: bool
    include_resource_describes: bool
    status: JobStatus = "queued"
    progress_percent: int = 0
    current_service: Optional[str] = None
    services_done: int = 0
    services_total: int = 0
    created_at: str = field(default_factory=_utc_now_iso)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    cancellation_requested: bool = False
    graph_store: GraphStore = field(default_factory=GraphStore)


class ScanJobStore:
    def __init__(self, *, max_workers: int = 4) -> None:
        self._jobs: Dict[str, ScanJob] = {}
        self._in_flight: Dict[str, str] = {}
        self._cache: Dict[str, CacheEntry] = {}
        self._latest_graph_id: Optional[str] = None
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="scan-job")

    def _prune_expired_cache_locked(self) -> None:
        now = datetime.now(timezone.utc)
        expired_keys = [key for key, value in self._cache.items() if value.expires_at <= now]
        for key in expired_keys:
            self._cache.pop(key, None)

    def find_reusable_job(self, *, cache_key: str, force_refresh: bool) -> tuple[Optional[str], bool]:
        if force_refresh:
            return None, False

        with self._lock:
            self._prune_expired_cache_locked()

            in_flight_id = self._in_flight.get(cache_key)
            if in_flight_id:
                job = self._jobs.get(in_flight_id)
                if job and job.status in {"queued", "running"}:
                    return in_flight_id, False
                self._in_flight.pop(cache_key, None)

            cached = self._cache.get(cache_key)
            if cached and cached.job_id in self._jobs:
                return cached.job_id, True

            return None, False

    def create_job(
        self,
        *,
        cache_key: str,
        account_id: str,
        region: str,
        services: List[str],
        mode: ScanMode,
        include_iam_inference: bool,
        include_resource_describes: bool,
    ) -> ScanJob:
        job_id = str(uuid4())
        job = ScanJob(
            id=job_id,
            cache_key=cache_key,
            account_id=account_id,
            region=region,
            services=services,
            mode=mode,
            include_iam_inference=include_iam_inference,
            include_resource_describes=include_resource_describes,
            services_total=len(services),
        )
        with self._lock:
            self._jobs[job_id] = job
            self._in_flight[cache_key] = job_id
        return job

    def submit_job(self, job_id: str, runner: Callable[[], None]) -> None:
        self._executor.submit(self._run_job_wrapper, job_id, runner)

    def _run_job_wrapper(self, job_id: str, runner: Callable[[], None]) -> None:
        try:
            runner()
        except Exception as exc:
            self.mark_failed(job_id, f"Unhandled scan failure: {type(exc).__name__} - {exc}")

    def mark_running(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.status != "queued":
                return
            job.status = "running"
            job.started_at = _utc_now_iso()

    def update_progress(
        self,
        job_id: str,
        *,
        current_service: Optional[str],
        services_done: int,
        services_total: int,
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.status not in {"queued", "running"}:
                return
            job.current_service = current_service
            job.services_done = services_done
            job.services_total = services_total
            job.progress_percent = _progress_percent(services_done, services_total)
            if job.status == "queued":
                job.status = "running"
                job.started_at = _utc_now_iso()

    def mark_completed(self, job_id: str, *, ttl_seconds: int) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.status not in {"queued", "running"}:
                return
            job.status = "completed"
            job.progress_percent = 100
            job.current_service = None
            job.services_done = job.services_total
            job.finished_at = _utc_now_iso()
            self._latest_graph_id = job_id

            self._in_flight.pop(job.cache_key, None)
            self._cache[job.cache_key] = CacheEntry(
                job_id=job_id,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
            )

    def mark_failed(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.status not in {"queued", "running"}:
                return
            job.status = "failed"
            job.error = error
            job.finished_at = _utc_now_iso()
            self._in_flight.pop(job.cache_key, None)

    def request_cancel(self, job_id: str) -> bool:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            job = self._jobs[job_id]
            if job.status not in {"queued", "running"}:
                return False
            job.cancellation_requested = True
            return True

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            if job_id not in self._jobs:
                return False
            return self._jobs[job_id].cancellation_requested

    def mark_cancelled(self, job_id: str, reason: str = "Cancelled by user") -> None:
        with self._lock:
            if job_id not in self._jobs:
                return
            job = self._jobs[job_id]
            if job.status not in {"queued", "running", "cancelled"}:
                return
            job.cancellation_requested = True
            job.status = "cancelled"
            job.error = reason
            job.current_service = None
            job.finished_at = _utc_now_iso()
            self._in_flight.pop(job.cache_key, None)

    def get_job(self, job_id: str) -> ScanJob:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            return self._jobs[job_id]

    def get_status_payload(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            job = self._jobs[job_id]
            snapshot = {
                "job_id": job.id,
                "status": job.status,
                "mode": job.mode,
                "region": job.region,
                "services": list(job.services),
                "progress_percent": job.progress_percent,
                "current_service": job.current_service,
                "services_done": job.services_done,
                "services_total": job.services_total,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
                "error": job.error,
                "graph_store": job.graph_store,
            }
        graph_payload = snapshot["graph_store"].get_graph_payload()
        metadata = graph_payload.get("metadata", {})
        return {
            "job_id": snapshot["job_id"],
            "status": snapshot["status"],
            "mode": snapshot["mode"],
            "region": snapshot["region"],
            "services": snapshot["services"],
            "progress_percent": snapshot["progress_percent"],
            "current_service": snapshot["current_service"],
            "services_done": snapshot["services_done"],
            "services_total": snapshot["services_total"],
            "node_count": metadata.get("node_count", 0),
            "edge_count": metadata.get("edge_count", 0),
            "warnings": metadata.get("warnings", []),
            "created_at": snapshot["created_at"],
            "started_at": snapshot["started_at"],
            "finished_at": snapshot["finished_at"],
            "error": snapshot["error"],
        }

    def get_graph_payload(self, job_id: str) -> Dict[str, Any]:
        job = self.get_job(job_id)
        return job.graph_store.get_graph_payload()

    def get_latest_graph_payload(self) -> Dict[str, Any]:
        with self._lock:
            latest_id = self._latest_graph_id
        if not latest_id:
            return GraphStore().get_graph_payload()
        return self.get_graph_payload(latest_id)

    def get_resource_payload(self, resource_id: str, job_id: Optional[str] = None) -> Dict[str, Any]:
        if job_id:
            job = self.get_job(job_id)
            return job.graph_store.get_resource_payload(resource_id)

        with self._lock:
            latest_id = self._latest_graph_id
        if not latest_id:
            raise KeyError(resource_id)
        job = self.get_job(latest_id)
        return job.graph_store.get_resource_payload(resource_id)

    @staticmethod
    def build_cache_key(
        *,
        account_id: str,
        region: str,
        services: List[str],
        mode: ScanMode,
        include_iam_inference: bool,
        include_resource_describes: bool,
    ) -> str:
        ordered_services = ",".join(sorted(services))
        return "|".join(
            [
                account_id,
                region,
                ordered_services,
                mode,
                f"iam={int(include_iam_inference)}",
                f"describe={int(include_resource_describes)}",
            ]
        )
