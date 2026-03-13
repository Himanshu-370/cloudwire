from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    CredentialRetrievalError,
    EndpointConnectionError,
    NoCredentialsError,
    PartialCredentialsError,
    ReadTimeoutError,
)
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .models import (
    APIErrorResponse,
    GraphResponse,
    ResourceResponse,
    ScanJobCreateResponse,
    ScanJobStatusResponse,
    ScanRequest,
    TraceDetailResponse,
    TraceListResponse,
    XRayJobCreateResponse,
    XRayJobStatusResponse,
    XRayScanRequest,
    normalize_service_name,
)
from .scan_jobs import ScanJobStore
from .scanner import AWSGraphScanner, ScanCancelledError, ScanExecutionOptions
from .xray_scanner import XRayFlowScanner, XRayScanCancelledError, XRayScanOptions, fetch_trace_detail

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static-file directory (cloudwire/static/ relative to this package)
# ---------------------------------------------------------------------------
_STATIC_DIR = Path(__file__).parent.parent / "static"


class APIError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def _error_payload(code: str, message: str, details: Optional[Any] = None) -> Dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
        }
    }


def _normalize_services(services: List[str]) -> List[str]:
    normalized = []
    for service in services:
        key = normalize_service_name(service)
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


def _friendly_exception_message(exc: Exception) -> str:
    if isinstance(exc, (NoCredentialsError, PartialCredentialsError, CredentialRetrievalError)):
        return "AWS credentials were not found. Set AWS credentials or run saml2aws login before scanning."
    if isinstance(exc, (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError)):
        return "Unable to reach the AWS API endpoint for the selected region."
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"ExpiredToken", "ExpiredTokenException", "RequestExpired"}:
            return "Your AWS session has expired. Refresh credentials and try again."
        if code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}:
            return "AWS access was denied for this operation. Verify the assumed role permissions."
        message = exc.response.get("Error", {}).get("Message")
        return message or f"AWS API request failed with {code or 'ClientError'}."
    if isinstance(exc, BotoCoreError):
        return "The AWS SDK failed to complete the request."
    return str(exc) or "Unexpected server error."


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
    except (NoCredentialsError, PartialCredentialsError, CredentialRetrievalError) as exc:
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="aws_credentials_missing",
            message=_friendly_exception_message(exc),
        ) from exc
    except ClientError as exc:
        aws_code = exc.response.get("Error", {}).get("Code", "")
        status_code = (
            status.HTTP_403_FORBIDDEN
            if aws_code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}
            else status.HTTP_401_UNAUTHORIZED
            if aws_code in {"ExpiredToken", "ExpiredTokenException", "RequestExpired"}
            else status.HTTP_502_BAD_GATEWAY
        )
        raise APIError(
            status_code=status_code,
            code="aws_account_lookup_failed",
            message=_friendly_exception_message(exc),
            details={"aws_error_code": aws_code or None, "region": region},
        ) from exc
    except (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError) as exc:
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="aws_endpoint_unreachable",
            message=_friendly_exception_message(exc),
            details={"region": region},
        ) from exc
    except BotoCoreError as exc:
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="aws_client_error",
            message=_friendly_exception_message(exc),
            details={"region": region},
        ) from exc


job_store = ScanJobStore(max_workers=4)
xray_job_store = ScanJobStore(max_workers=2)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    job_store.shutdown()
    xray_job_store.shutdown()


app = FastAPI(title="CloudWire API", version="0.1.0", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(APIError)
async def api_error_handler(_: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(exc.code, exc.message, exc.details),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        payload = detail
    elif isinstance(detail, str):
        payload = _error_payload("http_error", detail)
    else:
        payload = _error_payload("http_error", "Request failed.", detail)
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_payload(
            "validation_error",
            "Request validation failed.",
            exc.errors(),
        ),
    )


@app.exception_handler(Exception)
async def unexpected_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled API exception", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_payload("internal_error", "Unexpected server error."),
    )


# ---------------------------------------------------------------------------
# Scan runner (background thread)
# ---------------------------------------------------------------------------

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

    def on_progress(event: str, service: str, services_done: int, services_total: int) -> None:
        job_store.update_progress(
            job_id,
            event=event,
            current_service=service,
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
        logger.exception("Scan job %s failed with unhandled exception", job_id)
        message = _friendly_exception_message(exc)
        job.graph_store.add_warning(f"scan failed: {message}")
        job_store.mark_failed(job_id, message)


# ---------------------------------------------------------------------------
# X-Ray scan runner (background thread)
# ---------------------------------------------------------------------------

def _run_xray_scan_job(
    *,
    job_id: str,
    region: str,
    account_id: str,
    options: XRayScanOptions,
) -> None:
    xray_job_store.mark_running(job_id)
    if xray_job_store.is_cancel_requested(job_id):
        xray_job_store.mark_cancelled(job_id)
        return
    job = xray_job_store.get_job(job_id)
    scanner = XRayFlowScanner(job.graph_store, options=options)

    def on_progress(phase: str, done: int, total: int) -> None:
        xray_job_store.update_progress(
            job_id,
            event="start" if done < total else "finish",
            current_service=phase,
            services_done=done,
            services_total=total,
        )

    try:
        scanner.scan(
            region=region,
            account_id=account_id,
            progress_callback=on_progress,
            should_cancel=lambda: xray_job_store.is_cancel_requested(job_id),
        )
        if xray_job_store.is_cancel_requested(job_id):
            xray_job_store.mark_cancelled(job_id)
            return
        xray_job_store.mark_completed(job_id, ttl_seconds=300)
    except XRayScanCancelledError:
        job.graph_store.add_warning("X-Ray scan cancelled by user request.")
        xray_job_store.mark_cancelled(job_id)
    except Exception as exc:
        logger.exception("X-Ray scan job %s failed", job_id)
        message = _friendly_exception_message(exc)
        job.graph_store.add_warning(f"X-Ray scan failed: {message}")
        xray_job_store.mark_failed(job_id, message)


# ---------------------------------------------------------------------------
# API routes (all under /api prefix)
# ---------------------------------------------------------------------------

api = APIRouter(prefix="/api")


@api.get("/health")
def health() -> Dict[str, Any]:
    return {"service": "cloudwire", "status": "ok"}


@api.get("/graph", response_model=GraphResponse, responses={500: {"model": APIErrorResponse}})
def get_graph() -> Dict[str, Any]:
    return job_store.get_latest_graph_payload()


@api.get(
    "/resource/{resource_id}",
    response_model=ResourceResponse,
    responses={404: {"model": APIErrorResponse}, 500: {"model": APIErrorResponse}},
)
def get_resource(resource_id: str, job_id: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    try:
        return job_store.get_resource_payload(resource_id, job_id=job_id)
    except KeyError as exc:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="resource_not_found",
            message=f"Resource '{resource_id}' was not found in the selected graph.",
            details={"resource_id": resource_id, "job_id": job_id},
        ) from exc


@api.post(
    "/scan",
    response_model=ScanJobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        401: {"model": APIErrorResponse},
        403: {"model": APIErrorResponse},
        422: {"model": APIErrorResponse},
        502: {"model": APIErrorResponse},
        500: {"model": APIErrorResponse},
    },
)
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
            "status_url": f"/api/scan/{reusable_job_id}",
            "graph_url": f"/api/scan/{reusable_job_id}/graph",
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
        "status_url": f"/api/scan/{job.id}",
        "graph_url": f"/api/scan/{job.id}/graph",
    }


@api.get(
    "/scan/{job_id}",
    response_model=ScanJobStatusResponse,
    responses={404: {"model": APIErrorResponse}, 500: {"model": APIErrorResponse}},
)
def get_scan_job(job_id: str) -> Dict[str, Any]:
    try:
        return job_store.get_status_payload(job_id)
    except KeyError as exc:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="job_not_found",
            message=f"Scan job '{job_id}' was not found.",
            details={"job_id": job_id},
        ) from exc


@api.get(
    "/scan/{job_id}/graph",
    response_model=GraphResponse,
    responses={404: {"model": APIErrorResponse}, 500: {"model": APIErrorResponse}},
)
def get_scan_job_graph(job_id: str) -> Dict[str, Any]:
    try:
        return job_store.get_graph_payload(job_id)
    except KeyError as exc:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="job_not_found",
            message=f"Scan job '{job_id}' was not found.",
            details={"job_id": job_id},
        ) from exc


@api.post(
    "/scan/{job_id}/stop",
    response_model=ScanJobStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={404: {"model": APIErrorResponse}, 500: {"model": APIErrorResponse}},
)
def stop_scan_job(job_id: str) -> Dict[str, Any]:
    try:
        job_store.request_cancel(job_id)
        return job_store.get_status_payload(job_id)
    except KeyError as exc:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="job_not_found",
            message=f"Scan job '{job_id}' was not found.",
            details={"job_id": job_id},
        ) from exc


# ---------------------------------------------------------------------------
# X-Ray API routes
# ---------------------------------------------------------------------------

def _build_xray_cache_key(
    account_id: str,
    region: str,
    options: XRayScanOptions,
) -> str:
    parts = [
        "xray",
        account_id,
        region,
        str(options.time_range_minutes),
        options.filter_expression or "",
        options.group_name or "",
    ]
    return "|".join(parts)


@api.post(
    "/xray/scan",
    response_model=XRayJobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        401: {"model": APIErrorResponse},
        403: {"model": APIErrorResponse},
        422: {"model": APIErrorResponse},
        502: {"model": APIErrorResponse},
    },
)
def create_xray_scan_job(payload: XRayScanRequest) -> Dict[str, Any]:
    account_id = _resolve_account_id(payload.region)

    # Parse optional datetime strings
    start_time = None
    end_time = None
    if payload.start_time:
        try:
            start_time = datetime.fromisoformat(payload.start_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise APIError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="invalid_start_time",
                message=f"Invalid start_time format: {exc}",
            ) from exc
    if payload.end_time:
        try:
            end_time = datetime.fromisoformat(payload.end_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise APIError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="invalid_end_time",
                message=f"Invalid end_time format: {exc}",
            ) from exc

    options = XRayScanOptions(
        time_range_minutes=payload.time_range_minutes,
        start_time=start_time,
        end_time=end_time,
        filter_expression=payload.filter_expression,
        group_name=payload.group_name,
    )

    cache_key = _build_xray_cache_key(account_id, payload.region, options)
    reusable_job_id, cached = xray_job_store.find_reusable_job(
        cache_key=cache_key,
        force_refresh=payload.force_refresh,
    )
    if reusable_job_id:
        status_payload = xray_job_store.get_status_payload(reusable_job_id)
        return {
            "job_id": reusable_job_id,
            "status": status_payload["status"],
            "cached": cached,
            "status_url": f"/api/xray/scan/{reusable_job_id}",
            "graph_url": f"/api/xray/scan/{reusable_job_id}/graph",
        }

    job = xray_job_store.create_job(
        cache_key=cache_key,
        account_id=account_id,
        region=payload.region,
        services=["xray"],
        mode="quick",
        include_iam_inference=False,
        include_resource_describes=False,
    )
    xray_job_store.submit_job(
        job.id,
        lambda: _run_xray_scan_job(
            job_id=job.id,
            region=payload.region,
            account_id=account_id,
            options=options,
        ),
    )
    return {
        "job_id": job.id,
        "status": job.status,
        "cached": False,
        "status_url": f"/api/xray/scan/{job.id}",
        "graph_url": f"/api/xray/scan/{job.id}/graph",
    }


@api.get(
    "/xray/scan/{job_id}",
    response_model=XRayJobStatusResponse,
    responses={404: {"model": APIErrorResponse}},
)
def get_xray_scan_job(job_id: str) -> Dict[str, Any]:
    try:
        payload = xray_job_store.get_status_payload(job_id)
        # Add X-Ray-specific fields
        job = xray_job_store.get_job(job_id)
        graph_payload = job.graph_store.get_graph_payload()
        metadata = graph_payload.get("metadata", {})
        payload["trace_count"] = metadata.get("trace_count", 0)
        payload["time_range"] = metadata.get("time_range", {})
        return payload
    except KeyError as exc:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="job_not_found",
            message=f"X-Ray scan job '{job_id}' was not found.",
            details={"job_id": job_id},
        ) from exc


@api.get(
    "/xray/scan/{job_id}/graph",
    response_model=GraphResponse,
    responses={404: {"model": APIErrorResponse}},
)
def get_xray_scan_job_graph(job_id: str) -> Dict[str, Any]:
    try:
        return xray_job_store.get_graph_payload(job_id)
    except KeyError as exc:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="job_not_found",
            message=f"X-Ray scan job '{job_id}' was not found.",
            details={"job_id": job_id},
        ) from exc


@api.post(
    "/xray/scan/{job_id}/stop",
    status_code=status.HTTP_202_ACCEPTED,
    responses={404: {"model": APIErrorResponse}},
)
def stop_xray_scan_job(job_id: str) -> Dict[str, Any]:
    try:
        xray_job_store.request_cancel(job_id)
        return xray_job_store.get_status_payload(job_id)
    except KeyError as exc:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="job_not_found",
            message=f"X-Ray scan job '{job_id}' was not found.",
            details={"job_id": job_id},
        ) from exc


@api.get(
    "/xray/traces",
    response_model=TraceListResponse,
    responses={404: {"model": APIErrorResponse}},
)
def get_xray_traces(job_id: str = Query(..., description="X-Ray scan job ID")) -> Dict[str, Any]:
    try:
        job = xray_job_store.get_job(job_id)
        metadata = job.graph_store.metadata
        summaries = metadata.get("trace_summaries", [])
        return {"traces": summaries, "count": len(summaries)}
    except KeyError as exc:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="job_not_found",
            message=f"X-Ray scan job '{job_id}' was not found.",
            details={"job_id": job_id},
        ) from exc


@api.get(
    "/xray/traces/{trace_id}",
    response_model=TraceDetailResponse,
    responses={404: {"model": APIErrorResponse}, 502: {"model": APIErrorResponse}},
)
def get_xray_trace_detail(
    trace_id: str,
    region: str = Query(default="us-east-1"),
) -> Dict[str, Any]:
    try:
        traces = fetch_trace_detail(region=region, trace_ids=[trace_id])
        if not traces:
            raise APIError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="trace_not_found",
                message=f"Trace '{trace_id}' was not found or has expired.",
                details={"trace_id": trace_id},
            )
        return traces[0]
    except APIError:
        raise
    except (ClientError, BotoCoreError) as exc:
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="xray_error",
            message=_friendly_exception_message(exc),
        ) from exc


# ---------------------------------------------------------------------------
# X-Ray filter discovery endpoints
# ---------------------------------------------------------------------------

def _xray_client(region: str) -> Any:
    session = boto3.session.Session(region_name=region)
    return session.client(
        "xray",
        config=Config(
            retries={"mode": "adaptive", "max_attempts": 3},
            connect_timeout=5,
            read_timeout=15,
        ),
    )


@api.get("/xray/groups")
def get_xray_groups(region: str = Query(default="us-east-1")) -> Dict[str, Any]:
    """List X-Ray groups (saved filter expressions)."""
    try:
        client = _xray_client(region)
        groups: List[Dict[str, Any]] = []
        next_token: Optional[str] = None

        while True:
            kwargs: Dict[str, Any] = {}
            if next_token:
                kwargs["NextToken"] = next_token
            response = client.get_groups(**kwargs)
            for group in response.get("Groups", []):
                groups.append({
                    "name": group.get("GroupName", ""),
                    "arn": group.get("GroupARN", ""),
                    "filter_expression": group.get("FilterExpression", ""),
                })
            next_token = response.get("NextToken")
            if not next_token:
                break

        return {"groups": groups}

    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("AccessDenied", "AccessDeniedException"):
            raise APIError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="xray_access_denied",
                message="Access denied for xray:GetGroups. Add this permission to use X-Ray groups.",
            ) from exc
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="xray_error",
            message=_friendly_exception_message(exc),
        ) from exc
    except BotoCoreError as exc:
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="xray_error",
            message=_friendly_exception_message(exc),
        ) from exc


@api.get("/xray/annotations")
def get_xray_annotations(
    region: str = Query(default="us-east-1"),
    minutes: int = Query(default=60, ge=1, le=1440),
) -> Dict[str, Any]:
    """Discover annotation keys and values from recent traces.

    Does a quick trace summary fetch and extracts unique annotation
    key-value pairs for use in filter dropdowns.
    """
    try:
        client = _xray_client(region)
        now = datetime.now(timezone.utc)
        start = now - timedelta(minutes=minutes)

        annotations: Dict[str, set] = {}
        next_token: Optional[str] = None
        pages_fetched = 0
        max_pages = 5  # Limit to avoid slow responses

        while pages_fetched < max_pages:
            kwargs: Dict[str, Any] = {
                "StartTime": start,
                "EndTime": now,
                "Sampling": True,
            }
            if next_token:
                kwargs["NextToken"] = next_token

            response = client.get_trace_summaries(**kwargs)
            pages_fetched += 1

            for trace in response.get("TraceSummaries", []):
                trace_annotations = trace.get("Annotations", {})
                if not isinstance(trace_annotations, dict):
                    continue
                for key, values_list in trace_annotations.items():
                    if key not in annotations:
                        annotations[key] = set()
                    # Annotations can be a list of {AnnotationValue: {Type, Value}}
                    if isinstance(values_list, list):
                        for entry in values_list:
                            val = entry.get("AnnotationValue", {})
                            # Value can be string, number, or boolean
                            actual = val.get("StringValue") or val.get("NumberValue") or val.get("BooleanValue")
                            if actual is not None:
                                annotations[key].add(str(actual))
                    elif isinstance(values_list, str):
                        annotations[key].add(values_list)

            next_token = response.get("NextToken")
            if not next_token:
                break

        # Convert sets to sorted lists
        result = {
            key: sorted(list(values))
            for key, values in sorted(annotations.items())
        }

        return {"annotations": result, "time_range_minutes": minutes}

    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("AccessDenied", "AccessDeniedException"):
            raise APIError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="xray_access_denied",
                message="Access denied for xray:GetTraceSummaries. Add this permission to discover annotations.",
            ) from exc
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="xray_error",
            message=_friendly_exception_message(exc),
        ) from exc
    except BotoCoreError as exc:
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="xray_error",
            message=_friendly_exception_message(exc),
        ) from exc


app.include_router(api)

# ---------------------------------------------------------------------------
# Static file serving — must be registered AFTER all API routes
# ---------------------------------------------------------------------------

if _STATIC_DIR.is_dir() and ((_STATIC_DIR / "assets").is_dir()):
    app.mount("/assets", StaticFiles(directory=str(_STATIC_DIR / "assets")), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str) -> FileResponse:
    index = _STATIC_DIR / "index.html"
    if not index.is_file():
        return JSONResponse(
            status_code=503,
            content=_error_payload(
                "frontend_not_built",
                "Frontend assets not found. Run `make build` to compile the UI.",
            ),
        )
    return FileResponse(str(index))
