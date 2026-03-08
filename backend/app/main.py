from __future__ import annotations

from typing import Any, Dict, List, Optional

import boto3
from botocore.config import Config
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware

from .models import (
    GraphResponse,
    ResourceResponse,
    ScanJobCreateResponse,
    ScanJobStatusResponse,
    ScanRequest,
)
from .scan_jobs import ScanJobStore
from .scanner import AWSGraphScanner, ScanCancelledError, ScanExecutionOptions


def _normalize_services(services: List[str]) -> List[str]:
    aliases = {
        "api-gateway": "apigateway",
        "apigw": "apigateway",
        "event-bridge": "eventbridge",
        "events": "eventbridge",
    }
    normalized = []
    for service in services:
        key = aliases.get(service.lower().strip(), service.lower().strip())
        if key and key not in normalized:
            normalized.append(key)
    return normalized


def _resolve_option(value: Optional[bool], default: bool) -> bool:
    return default if value is None else value


def _resolve_scan_options(payload: ScanRequest) -> ScanExecutionOptions:
    default_iam = payload.mode == "deep"
    default_describes = payload.mode == "deep"
    return ScanExecutionOptions(
        mode=payload.mode,
        include_iam_inference=_resolve_option(payload.include_iam_inference, default_iam),
        include_resource_describes=_resolve_option(payload.include_resource_describes, default_describes),
    )


def _cache_ttl_seconds(mode: str) -> int:
    return 300 if mode == "quick" else 1800


def _resolve_account_id(region: str) -> str:
    session = boto3.session.Session(region_name=region)
    client = session.client(
        "sts",
        config=Config(
            retries={"mode": "adaptive", "max_attempts": 10},
            max_pool_connections=8,
            connect_timeout=3,
            read_timeout=10,
        ),
    )
    try:
        identity = client.get_caller_identity()
        return str(identity.get("Account", "unknown"))
    except Exception:
        return "unknown"


app = FastAPI(title="AWS Flow Visualizer API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

job_store = ScanJobStore(max_workers=4)


def _run_scan_job(
    *,
    job_id: str,
    region: str,
    services: List[str],
    account_id: str,
    options: ScanExecutionOptions,
) -> None:
    job_store.mark_running(job_id)
    if job_store.is_cancel_requested(job_id):
        job_store.mark_cancelled(job_id)
        return
    job = job_store.get_job(job_id)
    scanner = AWSGraphScanner(job.graph_store, options=options)

    def on_progress(_event: str, service: str, services_done: int, services_total: int) -> None:
        current = None if services_done >= services_total else service
        job_store.update_progress(
            job_id,
            current_service=current,
            services_done=services_done,
            services_total=services_total,
        )

    try:
        scanner.scan(
            region=region,
            services=services,
            account_id=account_id,
            progress_callback=on_progress,
            should_cancel=lambda: job_store.is_cancel_requested(job_id),
        )
        if job_store.is_cancel_requested(job_id):
            job_store.mark_cancelled(job_id)
            return
        job_store.mark_completed(job_id, ttl_seconds=_cache_ttl_seconds(options.mode))
    except ScanCancelledError:
        job.graph_store.add_warning("Scan cancelled by user request.")
        job_store.mark_cancelled(job_id)
    except Exception as exc:
        job.graph_store.add_warning(f"scan failed: {type(exc).__name__} - {exc}")
        job_store.mark_failed(job_id, f"{type(exc).__name__} - {exc}")


@app.get("/")
def health() -> Dict[str, Any]:
    return {"service": "aws-flow-visualizer", "status": "ok"}


@app.get("/graph", response_model=GraphResponse)
def get_graph() -> Dict[str, Any]:
    return job_store.get_latest_graph_payload()


@app.get("/resource/{resource_id}", response_model=ResourceResponse)
def get_resource(resource_id: str, job_id: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    try:
        return job_store.get_resource_payload(resource_id, job_id=job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Resource '{resource_id}' not found") from exc


@app.post("/scan", response_model=ScanJobCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def create_scan_job(payload: ScanRequest) -> Dict[str, Any]:
    services = _normalize_services(payload.services)
    options = _resolve_scan_options(payload)
    account_id = _resolve_account_id(payload.region)

    cache_key = ScanJobStore.build_cache_key(
        account_id=account_id,
        region=payload.region,
        services=services,
        mode=options.mode,
        include_iam_inference=options.include_iam_inference,
        include_resource_describes=options.include_resource_describes,
    )
    reusable_job_id, cached = job_store.find_reusable_job(
        cache_key=cache_key,
        force_refresh=payload.force_refresh,
    )
    if reusable_job_id:
        status_payload = job_store.get_status_payload(reusable_job_id)
        return {
            "job_id": reusable_job_id,
            "status": status_payload["status"],
            "cached": cached,
            "status_url": f"/scan/{reusable_job_id}",
            "graph_url": f"/scan/{reusable_job_id}/graph",
        }

    job = job_store.create_job(
        cache_key=cache_key,
        account_id=account_id,
        region=payload.region,
        services=services,
        mode=options.mode,
        include_iam_inference=options.include_iam_inference,
        include_resource_describes=options.include_resource_describes,
    )
    job_store.submit_job(
        job.id,
        lambda: _run_scan_job(
            job_id=job.id,
            region=payload.region,
            services=services,
            account_id=account_id,
            options=options,
        ),
    )
    return {
        "job_id": job.id,
        "status": job.status,
        "cached": False,
        "status_url": f"/scan/{job.id}",
        "graph_url": f"/scan/{job.id}/graph",
    }


@app.get("/scan/{job_id}", response_model=ScanJobStatusResponse)
def get_scan_job(job_id: str) -> Dict[str, Any]:
    try:
        return job_store.get_status_payload(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found") from exc


@app.get("/scan/{job_id}/graph", response_model=GraphResponse)
def get_scan_job_graph(job_id: str) -> Dict[str, Any]:
    try:
        return job_store.get_graph_payload(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found") from exc


@app.post("/scan/{job_id}/stop", response_model=ScanJobStatusResponse, status_code=status.HTTP_202_ACCEPTED)
def stop_scan_job(job_id: str) -> Dict[str, Any]:
    try:
        requested = job_store.request_cancel(job_id)
        if requested:
            job_store.mark_cancelled(job_id)
        return job_store.get_status_payload(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found") from exc
