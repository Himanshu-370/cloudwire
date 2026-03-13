"""X-Ray trace-based service flow scanner.

Fetches the AWS X-Ray service graph and trace summaries, then builds a
NetworkX directed graph that represents the *runtime* call flow between
services — as opposed to the infrastructure-level graph built by
`scanner.py`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from time import perf_counter
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .graph_store import GraphStore

logger = logging.getLogger(__name__)

# Maps X-Ray service type strings to CloudWire service names
_XRAY_TYPE_TO_SERVICE: Dict[str, str] = {
    "AWS::Lambda": "lambda",
    "AWS::Lambda::Function": "lambda",
    "AWS::DynamoDB": "dynamodb",
    "AWS::DynamoDB::Table": "dynamodb",
    "AWS::SQS": "sqs",
    "AWS::SQS::Queue": "sqs",
    "AWS::SNS": "sns",
    "AWS::SNS::Topic": "sns",
    "AWS::ApiGateway": "apigateway",
    "AWS::ApiGateway::Stage": "apigateway",
    "AWS::ApiGateway::RestApi": "apigateway",
    "AWS::S3": "s3",
    "AWS::S3::Bucket": "s3",
    "AWS::EC2": "ec2",
    "AWS::EC2::Instance": "ec2",
    "AWS::ECS": "ecs",
    "AWS::ECS::Container": "ecs",
    "AWS::StepFunctions": "stepfunctions",
    "AWS::StepFunctions::StateMachine": "stepfunctions",
    "AWS::Kinesis": "kinesis",
    "AWS::Kinesis::Stream": "kinesis",
    "AWS::ElastiCache": "elasticache",
    "AWS::ElastiCache::CacheCluster": "elasticache",
    "AWS::RDS": "rds",
    "AWS::RDS::DBInstance": "rds",
    "AWS::Cognito": "cognito",
    "AWS::CloudFront": "cloudfront",
    "AWS::CloudFront::Distribution": "cloudfront",
    "AWS::ElasticLoadBalancing": "elb",
    "AWS::ElasticLoadBalancingV2": "elb",
    "AWS::Events": "eventbridge",
    "AWS::Glue": "glue",
    "AWS::AppSync": "appsync",
    "AWS::Redshift": "redshift",
    "AWS::Route53": "route53",
    "AWS::SecretsManager": "secretsmanager",
    "AWS::KMS": "kms",
    "AWS::IAM": "iam",
}

# ARN templates per service for reconstructing full ARNs from X-Ray names
_ARN_TEMPLATES: Dict[str, str] = {
    "lambda": "arn:aws:lambda:{region}:{account}:function:{name}",
    "dynamodb": "arn:aws:dynamodb:{region}:{account}:table/{name}",
    "sqs": "arn:aws:sqs:{region}:{account}:{name}",
    "sns": "arn:aws:sns:{region}:{account}:{name}",
    "s3": "arn:aws:s3:::{name}",
    "kinesis": "arn:aws:kinesis:{region}:{account}:stream/{name}",
    "stepfunctions": "arn:aws:states:{region}:{account}:stateMachine:{name}",
    "rds": "arn:aws:rds:{region}:{account}:db:{name}",
    "elasticache": "arn:aws:elasticache:{region}:{account}:cluster:{name}",
    "redshift": "arn:aws:redshift:{region}:{account}:cluster:{name}",
}


class XRayScanCancelledError(Exception):
    """Raised when the X-Ray scan is cancelled by the user."""


@dataclass
class XRayScanOptions:
    """Configuration for an X-Ray scan."""
    time_range_minutes: int = 60
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    filter_expression: Optional[str] = None
    group_name: Optional[str] = None


def _extract_stats(summary_stats: Dict[str, Any]) -> Dict[str, Any]:
    """Extract edge/node statistics from an X-Ray service summary."""
    result = {}
    if not summary_stats:
        return result

    ok = summary_stats.get("OkCount", 0)
    error_stats = summary_stats.get("ErrorStatistics", {})
    fault_stats = summary_stats.get("FaultStatistics", {})
    throttle = error_stats.get("ThrottleCount", 0)
    other_error = error_stats.get("OtherCount", 0)
    total_faults = fault_stats.get("OtherCount", 0) + fault_stats.get("TotalCount", 0)
    total_requests = ok + other_error + throttle + total_faults

    result["requests"] = total_requests
    result["ok_count"] = ok
    result["errors"] = other_error
    result["faults"] = total_faults
    result["throttles"] = throttle

    resp_time = summary_stats.get("TotalResponseTime", 0)
    if total_requests > 0:
        result["avg_latency_ms"] = round((resp_time / total_requests) * 1000, 1)
        total_errors = other_error + total_faults
        result["error_rate"] = round((total_errors / total_requests) * 100, 2)
    else:
        result["avg_latency_ms"] = 0
        result["error_rate"] = 0

    return result


def _resolve_xray_service(
    xray_service: Dict[str, Any],
    region: str,
    account_id: str,
) -> Tuple[str, str, str, Optional[str]]:
    """Map an X-Ray service document to (node_id, label, service_name, arn).

    Returns a tuple suitable for adding to the graph.
    """
    name = xray_service.get("Name", "unknown")
    svc_type = xray_service.get("Type", "")
    account = xray_service.get("AccountId", account_id)

    # Map X-Ray type to CloudWire service name
    service = _XRAY_TYPE_TO_SERVICE.get(svc_type, "")

    # Client / user nodes are synthetic
    if svc_type == "client" or name.lower() in ("client", "unknown"):
        node_id = "xray:client"
        return node_id, "Client", "client", None

    # If we couldn't map the type, try to infer from name or fall back
    if not service:
        # Some services appear as just the type prefix
        for prefix, svc in _XRAY_TYPE_TO_SERVICE.items():
            if svc_type.startswith(prefix):
                service = svc
                break
        if not service:
            service = "xray"

    # Try to reconstruct ARN
    arn = None
    template = _ARN_TEMPLATES.get(service)
    if template:
        # Handle names that might contain slashes or be API Gateway stages
        clean_name = name.split("/")[0] if service == "apigateway" else name
        arn = template.format(region=region, account=account, name=clean_name)

    if arn:
        node_id = f"{service}:{arn}"
    else:
        node_id = f"xray:{service}:{name}"

    return node_id, name, service, arn


class XRayFlowScanner:
    """Scans AWS X-Ray service graph and builds a flow-based directed graph."""

    def __init__(self, store: GraphStore, *, options: XRayScanOptions) -> None:
        self.store = store
        self.options = options
        self._should_cancel: Optional[Callable[[], bool]] = None
        self._api_call_count = 0

    def _is_cancelled(self) -> bool:
        return self._should_cancel is not None and self._should_cancel()

    def _ensure_not_cancelled(self) -> None:
        if self._is_cancelled():
            raise XRayScanCancelledError("X-Ray scan cancelled")

    def _increment_api_call(self) -> None:
        self._api_call_count += 1

    def _resolve_time_range(self) -> Tuple[datetime, datetime]:
        """Resolve the effective time range for the X-Ray query."""
        now = datetime.now(timezone.utc)
        if self.options.start_time and self.options.end_time:
            return self.options.start_time, self.options.end_time
        if self.options.end_time:
            end = self.options.end_time
            start = end - timedelta(minutes=self.options.time_range_minutes)
            return start, end
        if self.options.start_time:
            return self.options.start_time, now
        # Default: last N minutes
        end = now
        start = end - timedelta(minutes=self.options.time_range_minutes)
        return start, end

    def scan(
        self,
        *,
        region: str,
        account_id: str,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Execute the X-Ray scan and populate the graph store.

        Args:
            region: AWS region to query
            account_id: AWS account ID
            progress_callback: Called with (phase_name, done_steps, total_steps)
            should_cancel: Callable returning True to request cancellation

        Returns:
            Dict with scan metadata (trace_count, time_range, etc.)
        """
        self._should_cancel = should_cancel
        self._api_call_count = 0
        started_at = perf_counter()

        start_time, end_time = self._resolve_time_range()

        self.store.reset(region=region, services=["xray"])
        self.store.update_metadata(
            account_id=account_id,
            scan_mode="xray",
            scan_type="xray_flow",
            time_range={
                "start": start_time.isoformat(),
                "end": end_time.isoformat(),
                "minutes": self.options.time_range_minutes,
            },
        )

        if self.options.filter_expression:
            self.store.update_metadata(filter_expression=self.options.filter_expression)

        session = boto3.session.Session(region_name=region)
        client = session.client(
            "xray",
            config=Config(
                retries={"mode": "adaptive", "max_attempts": 5},
                connect_timeout=5,
                read_timeout=30,
            ),
        )

        total_steps = 3  # service graph + trace summaries + finalize
        current_step = 0

        # ── Step 1: Fetch service graph ──
        if progress_callback:
            progress_callback("Fetching X-Ray service graph", current_step, total_steps)

        self._ensure_not_cancelled()
        service_nodes, service_edges, trace_count = self._fetch_service_graph(
            client, start_time, end_time, region, account_id
        )
        current_step += 1

        # ── Step 2: Fetch trace summaries (for metadata) ──
        if progress_callback:
            progress_callback("Fetching trace summaries", current_step, total_steps)

        self._ensure_not_cancelled()
        trace_summaries = self._fetch_trace_summaries(client, start_time, end_time)
        current_step += 1

        # ── Step 3: Finalize ──
        if progress_callback:
            progress_callback("Finalizing", current_step, total_steps)

        duration_ms = int((perf_counter() - started_at) * 1000)
        self.store.update_metadata(
            scan_duration_ms=duration_ms,
            api_call_count=self._api_call_count,
            trace_count=len(trace_summaries),
            service_node_count=service_nodes,
            service_edge_count=service_edges,
        )

        if service_nodes == 0:
            self.store.add_warning(
                "No X-Ray traces found for the selected time window. "
                "Ensure X-Ray tracing is enabled on your services."
            )

        if progress_callback:
            progress_callback("Done", total_steps, total_steps)

        return {
            "trace_count": len(trace_summaries),
            "node_count": service_nodes,
            "edge_count": service_edges,
            "duration_ms": duration_ms,
            "time_range": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat(),
            },
        }

    def _fetch_service_graph(
        self,
        client: Any,
        start_time: datetime,
        end_time: datetime,
        region: str,
        account_id: str,
    ) -> Tuple[int, int, int]:
        """Fetch the X-Ray service graph and populate the graph store.

        Returns (node_count, edge_count, trace_count).
        """
        node_count = 0
        edge_count = 0

        try:
            kwargs: Dict[str, Any] = {
                "StartTime": start_time,
                "EndTime": end_time,
            }
            if self.options.group_name:
                kwargs["GroupName"] = self.options.group_name

            # X-Ray GetServiceGraph is paginated via NextToken
            all_services: List[Dict[str, Any]] = []
            next_token: Optional[str] = None

            while True:
                self._ensure_not_cancelled()
                if next_token:
                    kwargs["NextToken"] = next_token

                self._increment_api_call()
                response = client.get_service_graph(**kwargs)
                all_services.extend(response.get("Services", []))
                next_token = response.get("NextToken")
                if not next_token:
                    break

            # Build a map from X-Ray reference ID to node_id for edge linking
            ref_to_node: Dict[int, str] = {}
            node_ids_added: Set[str] = set()

            for svc in all_services:
                self._ensure_not_cancelled()
                ref_id = svc.get("ReferenceId")
                node_id, label, service, arn = _resolve_xray_service(svc, region, account_id)

                if node_id not in node_ids_added:
                    node_attrs: Dict[str, Any] = {
                        "label": label,
                        "service": service,
                        "type": svc.get("Type", "unknown"),
                        "source": "xray",
                    }
                    if arn:
                        node_attrs["arn"] = arn

                    # Extract stats
                    summary = svc.get("SummaryStatistics", {})
                    stats = _extract_stats(summary)
                    if stats:
                        node_attrs["xray_stats"] = stats
                        node_attrs["requests"] = stats.get("requests", 0)
                        node_attrs["avg_latency_ms"] = stats.get("avg_latency_ms", 0)
                        node_attrs["error_rate"] = stats.get("error_rate", 0)

                    # Response time histogram
                    resp_histogram = svc.get("ResponseTimeHistogram", [])
                    if resp_histogram:
                        p99_entry = resp_histogram[-1] if resp_histogram else None
                        if p99_entry:
                            node_attrs["p99_latency_ms"] = round(p99_entry.get("Value", 0) * 1000, 1)

                    # State info
                    xray_state = svc.get("State")
                    if xray_state:
                        node_attrs["state"] = xray_state

                    self.store.add_node(node_id, region=region, **node_attrs)
                    node_ids_added.add(node_id)
                    node_count += 1

                if ref_id is not None:
                    ref_to_node[ref_id] = node_id

            # Now build edges from the service graph
            for svc in all_services:
                self._ensure_not_cancelled()
                source_ref = svc.get("ReferenceId")
                source_node = ref_to_node.get(source_ref) if source_ref is not None else None
                if not source_node:
                    continue

                for edge_entry in svc.get("Edges", []):
                    target_ref = edge_entry.get("ReferenceId")
                    target_node = ref_to_node.get(target_ref) if target_ref is not None else None
                    if not target_node or source_node == target_node:
                        continue

                    edge_stats = _extract_stats(edge_entry.get("SummaryStatistics", {}))
                    edge_attrs: Dict[str, Any] = {
                        "relationship": "calls",
                        "via": "xray_trace",
                        "source_attr": "xray",
                    }
                    if edge_stats:
                        edge_attrs["xray_stats"] = edge_stats
                        edge_attrs["requests"] = edge_stats.get("requests", 0)
                        edge_attrs["avg_latency_ms"] = edge_stats.get("avg_latency_ms", 0)
                        edge_attrs["error_rate"] = edge_stats.get("error_rate", 0)

                    # Aliases for edges
                    aliases = edge_entry.get("Aliases", [])
                    if aliases:
                        alias_names = [a.get("Name", "") for a in aliases if a.get("Name")]
                        if alias_names:
                            edge_attrs["aliases"] = alias_names

                    self.store.add_edge(source_node, target_node, **edge_attrs)
                    edge_count += 1

            return node_count, edge_count, len(all_services)

        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("AccessDenied", "AccessDeniedException"):
                self.store.add_warning(
                    "[permission] X-Ray: access denied — add xray:GetServiceGraph "
                    "permission to use trace flow visualization"
                )
            elif error_code == "InvalidRequestException":
                message = exc.response.get("Error", {}).get("Message", "")
                self.store.add_warning(f"X-Ray invalid request: {message}")
            elif error_code == "ThrottledException":
                self.store.add_warning(
                    "X-Ray API rate limit exceeded. Try a shorter time window or wait a moment."
                )
            else:
                self.store.add_warning(f"X-Ray service graph failed: {error_code} - {exc}")
            logger.warning("X-Ray GetServiceGraph failed: %s", exc)
            return 0, 0, 0

        except BotoCoreError as exc:
            self.store.add_warning(f"X-Ray service graph failed: {type(exc).__name__} - {exc}")
            logger.warning("X-Ray GetServiceGraph failed: %s", exc)
            return 0, 0, 0

    def _fetch_trace_summaries(
        self,
        client: Any,
        start_time: datetime,
        end_time: datetime,
        max_traces: int = 200,
    ) -> List[Dict[str, Any]]:
        """Fetch trace summaries for the time window.

        Returns a list of trace summary dicts (trace_id, duration, has_error, etc.).
        """
        summaries: List[Dict[str, Any]] = []

        try:
            kwargs: Dict[str, Any] = {
                "StartTime": start_time,
                "EndTime": end_time,
                "Sampling": True,
            }
            if self.options.filter_expression:
                kwargs["FilterExpression"] = self.options.filter_expression

            next_token: Optional[str] = None

            while len(summaries) < max_traces:
                self._ensure_not_cancelled()
                if next_token:
                    kwargs["NextToken"] = next_token

                self._increment_api_call()
                response = client.get_trace_summaries(**kwargs)

                for trace in response.get("TraceSummaries", []):
                    summaries.append({
                        "trace_id": trace.get("Id", ""),
                        "duration": trace.get("Duration"),
                        "response_time": trace.get("ResponseTime"),
                        "has_fault": trace.get("HasFault", False),
                        "has_error": trace.get("HasError", False),
                        "has_throttle": trace.get("HasThrottle", False),
                        "http_status": (trace.get("Http", {}) or {}).get("HttpStatus"),
                        "http_method": (trace.get("Http", {}) or {}).get("HttpMethod"),
                        "http_url": (trace.get("Http", {}) or {}).get("HttpURL"),
                        "entry_point": self._extract_entry_point(trace),
                        "annotations": trace.get("Annotations", {}),
                        "users": [u.get("UserName", "") for u in trace.get("Users", [])],
                        "service_ids": [
                            {"name": s.get("Name", ""), "type": s.get("Type", "")}
                            for s in trace.get("ServiceIds", [])
                        ],
                    })
                    if len(summaries) >= max_traces:
                        break

                next_token = response.get("NextToken")
                if not next_token:
                    break

            # Store trace summaries in metadata for frontend access
            self.store.update_metadata(trace_summaries=summaries)
            return summaries

        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("AccessDenied", "AccessDeniedException"):
                self.store.add_warning(
                    "[permission] X-Ray: access denied for GetTraceSummaries — "
                    "add xray:GetTraceSummaries permission"
                )
            elif error_code == "InvalidRequestException":
                message = exc.response.get("Error", {}).get("Message", "")
                self.store.add_warning(f"X-Ray trace filter invalid: {message}")
            else:
                self.store.add_warning(f"X-Ray trace summaries failed: {error_code} - {exc}")
            logger.warning("X-Ray GetTraceSummaries failed: %s", exc)
            return []

        except BotoCoreError as exc:
            self.store.add_warning(f"X-Ray trace summaries failed: {type(exc).__name__} - {exc}")
            logger.warning("X-Ray GetTraceSummaries failed: %s", exc)
            return []

    @staticmethod
    def _extract_entry_point(trace: Dict[str, Any]) -> Optional[str]:
        """Extract the entry point service name from a trace summary."""
        service_ids = trace.get("ServiceIds", [])
        if service_ids:
            return service_ids[0].get("Name")
        return None


def fetch_trace_detail(
    *,
    region: str,
    trace_ids: List[str],
) -> List[Dict[str, Any]]:
    """Fetch detailed trace data for specific trace IDs.

    This is a standalone function (not part of the scanner class) because
    it's used for on-demand drill-down, not during the main scan.

    Args:
        region: AWS region
        trace_ids: List of X-Ray trace IDs to fetch

    Returns:
        List of trace detail dicts with segments
    """
    if not trace_ids:
        return []

    session = boto3.session.Session(region_name=region)
    client = session.client(
        "xray",
        config=Config(
            retries={"mode": "adaptive", "max_attempts": 3},
            connect_timeout=5,
            read_timeout=15,
        ),
    )

    traces: List[Dict[str, Any]] = []

    # BatchGetTraces accepts max 5 trace IDs per call
    batch_size = 5
    for i in range(0, len(trace_ids), batch_size):
        batch = trace_ids[i : i + batch_size]
        try:
            response = client.batch_get_traces(TraceIds=batch)
            for trace in response.get("Traces", []):
                segments = []
                for segment in trace.get("Segments", []):
                    import json as _json
                    try:
                        doc = _json.loads(segment.get("Document", "{}"))
                    except (ValueError, TypeError):
                        doc = {}
                    segments.append({
                        "id": segment.get("Id", ""),
                        "name": doc.get("name", ""),
                        "start_time": doc.get("start_time"),
                        "end_time": doc.get("end_time"),
                        "duration": (
                            round((doc.get("end_time", 0) - doc.get("start_time", 0)) * 1000, 1)
                            if doc.get("start_time") and doc.get("end_time")
                            else None
                        ),
                        "http": doc.get("http", {}),
                        "aws": doc.get("aws", {}),
                        "error": doc.get("error", False),
                        "fault": doc.get("fault", False),
                        "throttle": doc.get("throttle", False),
                        "annotations": doc.get("annotations", {}),
                        "metadata": doc.get("metadata", {}),
                        "subsegments": _flatten_subsegments(doc.get("subsegments", [])),
                        "origin": doc.get("origin", ""),
                        "namespace": doc.get("namespace", ""),
                    })
                traces.append({
                    "trace_id": trace.get("Id", ""),
                    "duration": trace.get("Duration"),
                    "segments": segments,
                })
        except (ClientError, BotoCoreError) as exc:
            logger.warning("BatchGetTraces failed for batch starting at %d: %s", i, exc)

    return traces


def _flatten_subsegments(subsegments: List[Dict[str, Any]], depth: int = 0) -> List[Dict[str, Any]]:
    """Recursively flatten subsegment tree for waterfall display."""
    result: List[Dict[str, Any]] = []
    for sub in subsegments:
        result.append({
            "name": sub.get("name", ""),
            "start_time": sub.get("start_time"),
            "end_time": sub.get("end_time"),
            "duration": (
                round((sub.get("end_time", 0) - sub.get("start_time", 0)) * 1000, 1)
                if sub.get("start_time") and sub.get("end_time")
                else None
            ),
            "namespace": sub.get("namespace", ""),
            "http": sub.get("http", {}),
            "aws": sub.get("aws", {}),
            "error": sub.get("error", False),
            "fault": sub.get("fault", False),
            "depth": depth,
        })
        result.extend(_flatten_subsegments(sub.get("subsegments", []), depth + 1))
    return result
