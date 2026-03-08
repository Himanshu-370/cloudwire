from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from threading import Lock
from time import perf_counter
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .graph_store import GraphStore
from .models import ScanMode


def _normalize_service_name(service: str) -> str:
    key = service.lower().strip()
    aliases = {
        "api-gateway": "apigateway",
        "apigw": "apigateway",
        "event-bridge": "eventbridge",
        "events": "eventbridge",
    }
    return aliases.get(key, key)


def _safe_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


@dataclass
class ScanExecutionOptions:
    mode: ScanMode = "quick"
    include_iam_inference: bool = False
    include_resource_describes: bool = False
    max_service_workers: int = 5
    apigw_integration_workers: int = 16
    eventbridge_target_workers: int = 8
    dynamodb_describe_workers: int = 16
    sqs_attribute_workers: int = 16
    iam_workers: int = 8


class ScanCancelledError(Exception):
    pass


class AWSGraphScanner:
    def __init__(self, store: GraphStore, *, options: ScanExecutionOptions) -> None:
        self.store = store
        self.options = options
        self.service_scanners: Dict[str, Callable[[boto3.session.Session], None]] = {
            "apigateway": self._scan_apigateway,
            "lambda": self._scan_lambda,
            "sqs": self._scan_sqs,
            "eventbridge": self._scan_eventbridge,
            "dynamodb": self._scan_dynamodb,
        }
        self._iam_role_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._iam_cache_lock = Lock()
        self._metrics_lock = Lock()
        self._api_call_counts: Dict[str, int] = {}
        self._service_durations_ms: Dict[str, int] = {}
        self._should_cancel: Optional[Callable[[], bool]] = None
        self._client_config = Config(
            retries={"mode": "adaptive", "max_attempts": 10},
            max_pool_connections=64,
            connect_timeout=3,
            read_timeout=20,
        )

    def scan(
        self,
        *,
        region: str,
        services: List[str],
        account_id: str = "unknown",
        progress_callback: Optional[Callable[[str, str, int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        normalized_services = list(dict.fromkeys(_normalize_service_name(service) for service in services))
        self.store.reset(region=region, services=normalized_services)
        self._iam_role_cache = {}
        self._api_call_counts = {}
        self._service_durations_ms = {}
        self._should_cancel = should_cancel

        if not self.options.include_iam_inference:
            self.store.add_warning("IAM policy dependency inference skipped for faster quick scan mode.")
        if not self.options.include_resource_describes:
            self.store.add_warning("Resource describe enrichment skipped for faster quick scan mode.")

        session = boto3.session.Session(region_name=region)
        total_services = len(normalized_services)
        completed = 0
        started_at = perf_counter()

        workers = max(1, min(self.options.max_service_workers, total_services or 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_service: Dict[Any, str] = {}
            for service in normalized_services:
                if self._is_cancelled():
                    break
                if progress_callback:
                    progress_callback("start", service, completed, total_services)
                future_to_service[pool.submit(self._scan_service, session, service)] = service

            for future in as_completed(future_to_service):
                if self._is_cancelled():
                    break
                service = future_to_service[future]
                try:
                    duration_ms = future.result()
                    with self._metrics_lock:
                        self._service_durations_ms[service] = duration_ms
                except Exception as exc:
                    self.store.add_warning(f"{service} scan failed: {type(exc).__name__} - {exc}")
                completed += 1
                if progress_callback:
                    progress_callback("finish", service, completed, total_services)

        duration_ms = int((perf_counter() - started_at) * 1000)
        self.store.update_metadata(
            account_id=account_id,
            scan_mode=self.options.mode,
            include_iam_inference=self.options.include_iam_inference,
            include_resource_describes=self.options.include_resource_describes,
            total_scan_ms=duration_ms,
            service_durations_ms=self._service_durations_ms,
            aws_api_call_counts=self._api_call_counts,
        )
        return self.store.get_graph_payload()

    def _scan_service(self, session: boto3.session.Session, service: str) -> int:
        start = perf_counter()
        if self._is_cancelled():
            return 0
        scanner = self.service_scanners.get(service)
        try:
            if scanner:
                scanner(session)
            else:
                self._scan_generic_service(session, service)
        except ScanCancelledError:
            return int((perf_counter() - start) * 1000)
        except (ClientError, BotoCoreError, Exception) as exc:
            self.store.add_warning(f"{service} scan failed: {type(exc).__name__} - {exc}")
        return int((perf_counter() - start) * 1000)

    def _client(self, session: boto3.session.Session, service_name: str) -> Any:
        return session.client(service_name, config=self._client_config)

    def _increment_api_call(self, service: str, operation: str) -> None:
        if self._is_cancelled():
            raise ScanCancelledError()
        key = f"{service}.{operation}"
        with self._metrics_lock:
            self._api_call_counts[key] = self._api_call_counts.get(key, 0) + 1

    def _is_cancelled(self) -> bool:
        if not self._should_cancel:
            return False
        return bool(self._should_cancel())

    def _service_from_arn(self, arn: str) -> str:
        parts = arn.split(":")
        return parts[2] if len(parts) > 2 else "unknown"

    def _make_node_id(self, service: str, resource: str) -> str:
        return f"{service}:{resource}"

    def _add_arn_node(self, arn: str, *, label: Optional[str] = None, node_type: str = "resource") -> str:
        service = self._service_from_arn(arn)
        node_id = self._make_node_id(service, arn)
        self.store.add_node(
            node_id,
            label=label or arn.split(":")[-1],
            arn=arn,
            service=service,
            type=node_type,
        )
        return node_id

    def _parse_lambda_arn(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        if ":function:" in value:
            clean = value.split("/invocations")[0]
            idx = clean.find("arn:aws:lambda:")
            if idx >= 0:
                return clean[idx:]
        return None

    def _base_lambda_arn(self, function_arn: str) -> str:
        if ":function:" not in function_arn:
            return function_arn
        prefix, suffix = function_arn.split(":function:", 1)
        function_name = suffix.split(":", 1)[0]
        return f"{prefix}:function:{function_name}"

    def _scan_apigateway(self, session: boto3.session.Session) -> None:
        self._scan_apigateway_v2(session)
        self._scan_apigateway_rest(session)

    def _scan_apigateway_v2(self, session: boto3.session.Session) -> None:
        client = self._client(session, "apigatewayv2")
        next_token: Optional[str] = None
        while True:
            kwargs: Dict[str, Any] = {}
            if next_token:
                kwargs["NextToken"] = next_token
            self._increment_api_call("apigateway", "get_apis")
            page = client.get_apis(**kwargs)
            for api in page.get("Items", []):
                api_id = api["ApiId"]
                api_name = api.get("Name") or api_id
                node_id = self._make_node_id("apigateway", api_id)
                self.store.add_node(
                    node_id,
                    label=api_name,
                    service="apigateway",
                    type="api",
                    api_protocol=api.get("ProtocolType"),
                    api_endpoint=api.get("ApiEndpoint"),
                )

                integration_token: Optional[str] = None
                while True:
                    integration_kwargs: Dict[str, Any] = {"ApiId": api_id}
                    if integration_token:
                        integration_kwargs["NextToken"] = integration_token
                    self._increment_api_call("apigateway", "get_integrations")
                    integrations = client.get_integrations(**integration_kwargs)
                    for integration in integrations.get("Items", []):
                        lambda_arn = self._parse_lambda_arn(integration.get("IntegrationUri"))
                        if not lambda_arn:
                            continue
                        lambda_node = self._add_arn_node(lambda_arn, node_type="lambda")
                        self.store.add_edge(
                            node_id,
                            lambda_node,
                            relationship="invokes",
                            via="apigatewayv2_integration",
                        )
                    integration_token = integrations.get("NextToken")
                    if not integration_token:
                        break

            next_token = page.get("NextToken")
            if not next_token:
                break

    def _scan_apigateway_rest(self, session: boto3.session.Session) -> None:
        client = self._client(session, "apigateway")
        position: Optional[str] = None

        while True:
            kwargs: Dict[str, Any] = {"limit": 500}
            if position:
                kwargs["position"] = position
            self._increment_api_call("apigateway", "get_rest_apis")
            page = client.get_rest_apis(**kwargs)
            for api in page.get("items", []):
                rest_api_id = api["id"]
                api_node = self._make_node_id("apigateway", rest_api_id)
                self.store.add_node(
                    api_node,
                    label=api.get("name") or rest_api_id,
                    service="apigateway",
                    type="api",
                    endpoint_configuration=api.get("endpointConfiguration", {}),
                )

                tasks: List[tuple[str, str, str, str]] = []
                res_position: Optional[str] = None
                while True:
                    res_kwargs: Dict[str, Any] = {"restApiId": rest_api_id, "limit": 500}
                    if res_position:
                        res_kwargs["position"] = res_position
                    self._increment_api_call("apigateway", "get_resources")
                    resources_page = client.get_resources(**res_kwargs)
                    for resource in resources_page.get("items", []):
                        methods = resource.get("resourceMethods", {})
                        for http_method in methods.keys():
                            tasks.append((rest_api_id, resource["id"], http_method, api_node))
                    res_position = resources_page.get("position")
                    if not res_position:
                        break

                if tasks:
                    workers = max(1, min(self.options.apigw_integration_workers, len(tasks)))
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futures = {
                            pool.submit(
                                self._fetch_apigw_rest_integration_lambda,
                                client,
                                rest_api_id,
                                resource_id,
                                http_method,
                            ): api_node
                            for rest_api_id, resource_id, http_method, api_node in tasks
                        }
                        for future in as_completed(futures):
                            lambda_arn = future.result()
                            if not lambda_arn:
                                continue
                            lambda_node = self._add_arn_node(lambda_arn, node_type="lambda")
                            self.store.add_edge(
                                futures[future],
                                lambda_node,
                                relationship="invokes",
                                via="apigateway_rest_integration",
                            )

            position = page.get("position")
            if not position:
                break

    def _fetch_apigw_rest_integration_lambda(
        self,
        client: Any,
        rest_api_id: str,
        resource_id: str,
        http_method: str,
    ) -> Optional[str]:
        try:
            self._increment_api_call("apigateway", "get_integration")
            integration = client.get_integration(
                restApiId=rest_api_id,
                resourceId=resource_id,
                httpMethod=http_method,
            )
        except ClientError:
            return None
        return self._parse_lambda_arn(integration.get("uri"))

    def _scan_lambda(self, session: boto3.session.Session) -> None:
        client = self._client(session, "lambda")
        paginator = client.get_paginator("list_functions")
        functions: List[Dict[str, Any]] = []
        for page in paginator.paginate():
            self._increment_api_call("lambda", "list_functions")
            functions.extend(page.get("Functions", []))

        function_node_ids: Dict[str, str] = {}
        role_to_function_nodes: Dict[str, List[str]] = {}

        for fn in functions:
            arn = fn["FunctionArn"]
            node_id = self._add_arn_node(arn, label=fn.get("FunctionName"), node_type="lambda")
            self.store.add_node(
                node_id,
                runtime=fn.get("Runtime"),
                handler=fn.get("Handler"),
                role=fn.get("Role"),
                memory_size=fn.get("MemorySize"),
                timeout=fn.get("Timeout"),
                last_modified=fn.get("LastModified"),
            )
            function_node_ids[arn] = node_id
            function_node_ids[self._base_lambda_arn(arn)] = node_id

            role_arn = fn.get("Role")
            if role_arn:
                role_name = role_arn.split("/")[-1]
                role_to_function_nodes.setdefault(role_name, []).append(node_id)

        self._scan_lambda_event_sources_global(client, function_node_ids)
        if self.options.include_iam_inference:
            self._scan_lambda_iam_dependencies_parallel(session, role_to_function_nodes)

    def _scan_lambda_event_sources_global(self, client: Any, function_node_ids: Dict[str, str]) -> None:
        marker: Optional[str] = None
        while True:
            kwargs: Dict[str, Any] = {}
            if marker:
                kwargs["Marker"] = marker
            self._increment_api_call("lambda", "list_event_source_mappings")
            page = client.list_event_source_mappings(**kwargs)
            for mapping in page.get("EventSourceMappings", []):
                event_source_arn = mapping.get("EventSourceArn")
                function_arn = mapping.get("FunctionArn")
                if not event_source_arn or not function_arn:
                    continue

                function_node_id = function_node_ids.get(function_arn) or function_node_ids.get(
                    self._base_lambda_arn(function_arn)
                )
                if not function_node_id:
                    continue

                source_node = self._add_arn_node(event_source_arn)
                self.store.add_edge(
                    source_node,
                    function_node_id,
                    relationship="triggers",
                    via="lambda_event_source_mapping",
                    state=mapping.get("State"),
                )

            marker = page.get("NextMarker")
            if not marker:
                break

    def _scan_lambda_iam_dependencies_parallel(
        self,
        session: boto3.session.Session,
        role_to_function_nodes: Dict[str, List[str]],
    ) -> None:
        roles = list(role_to_function_nodes.keys())
        if not roles:
            return

        workers = max(1, min(self.options.iam_workers, len(roles)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_role = {
                pool.submit(self._get_role_policy_documents, session, role_name): role_name
                for role_name in roles
            }
            for future in as_completed(future_to_role):
                role_name = future_to_role[future]
                try:
                    policy_details = future.result()
                except Exception as exc:
                    self.store.add_warning(
                        f"iam policy lookup failed for {role_name}: {type(exc).__name__} - {exc}"
                    )
                    continue

                for function_node_id in role_to_function_nodes.get(role_name, []):
                    self._apply_policy_dependencies(function_node_id, policy_details)

    def _apply_policy_dependencies(self, function_node_id: str, statements: List[Dict[str, Any]]) -> None:
        for statement in statements:
            effect = str(statement.get("Effect", "Allow")).lower()
            if effect != "allow":
                continue
            actions = [str(action).lower() for action in _safe_list(statement.get("Action"))]
            resources = [str(resource) for resource in _safe_list(statement.get("Resource"))]

            service_hits = self._services_from_actions(actions)
            for service in service_hits:
                for resource in resources or ["*"]:
                    target = self._target_from_service_resource(service, resource)
                    self.store.add_edge(
                        function_node_id,
                        target,
                        relationship="calls",
                        via="lambda_role_policy",
                        actions=sorted(service_hits[service]),
                    )

    def _services_from_actions(self, actions: Iterable[str]) -> Dict[str, Set[str]]:
        service_actions: Dict[str, Set[str]] = {}
        for action in actions:
            if ":" not in action:
                continue
            prefix, verb = action.split(":", 1)
            if prefix in {"dynamodb", "sqs", "events", "lambda"}:
                normalized = "eventbridge" if prefix == "events" else prefix
                service_actions.setdefault(normalized, set()).add(verb)
        return service_actions

    def _target_from_service_resource(self, service: str, resource: str) -> str:
        if resource.startswith("arn:aws:"):
            target = self._add_arn_node(resource, node_type="resource")
            self.store.add_node(target, service=service)
            return target
        node_id = self._make_node_id(service, resource)
        self.store.add_node(node_id, label=resource, service=service, type="resource", arn=resource)
        return node_id

    def _get_role_policy_documents(
        self,
        session: boto3.session.Session,
        role_name: str,
    ) -> List[Dict[str, Any]]:
        with self._iam_cache_lock:
            cached = self._iam_role_cache.get(role_name)
        if cached is not None:
            return cached

        iam = self._client(session, "iam")
        policy_docs: List[Dict[str, Any]] = []

        self._increment_api_call("iam", "list_role_policies")
        inline_policy_names = iam.list_role_policies(RoleName=role_name).get("PolicyNames", [])
        for policy_name in inline_policy_names:
            self._increment_api_call("iam", "get_role_policy")
            raw = iam.get_role_policy(RoleName=role_name, PolicyName=policy_name)
            policy_docs.append(raw.get("PolicyDocument", {}))

        attached_policies: List[Dict[str, Any]] = []
        marker: Optional[str] = None
        while True:
            kwargs: Dict[str, Any] = {"RoleName": role_name}
            if marker:
                kwargs["Marker"] = marker
            self._increment_api_call("iam", "list_attached_role_policies")
            page = iam.list_attached_role_policies(**kwargs)
            attached_policies.extend(page.get("AttachedPolicies", []))
            marker = page.get("Marker") if page.get("IsTruncated") else None
            if not marker:
                break

        for attached in attached_policies:
            self._increment_api_call("iam", "get_policy")
            policy = iam.get_policy(PolicyArn=attached["PolicyArn"]).get("Policy", {})
            default_version = policy.get("DefaultVersionId")
            if not default_version:
                continue
            self._increment_api_call("iam", "get_policy_version")
            version = iam.get_policy_version(
                PolicyArn=attached["PolicyArn"],
                VersionId=default_version,
            )
            policy_docs.append(version.get("PolicyVersion", {}).get("Document", {}))

        statements: List[Dict[str, Any]] = []
        for document in policy_docs:
            statements.extend(_safe_list(document.get("Statement")))

        with self._iam_cache_lock:
            self._iam_role_cache[role_name] = statements
        return statements

    def _scan_sqs(self, session: boto3.session.Session) -> None:
        client = self._client(session, "sqs")
        queue_urls: List[str] = []
        next_token: Optional[str] = None

        while True:
            kwargs: Dict[str, Any] = {}
            if next_token:
                kwargs["NextToken"] = next_token
            self._increment_api_call("sqs", "list_queues")
            page = client.list_queues(**kwargs)
            queue_urls.extend(page.get("QueueUrls", []))
            next_token = page.get("NextToken")
            if not next_token:
                break

        if not self.options.include_resource_describes:
            for queue_url in queue_urls:
                queue_name = queue_url.rstrip("/").split("/")[-1]
                node_id = self._make_node_id("sqs", queue_url)
                self.store.add_node(
                    node_id,
                    label=queue_name,
                    service="sqs",
                    type="queue",
                    queue_url=queue_url,
                    arn=queue_url,
                )
            return

        workers = max(1, min(self.options.sqs_attribute_workers, len(queue_urls) or 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._fetch_sqs_queue_attributes, client, queue_url): queue_url
                for queue_url in queue_urls
            }
            for future in as_completed(futures):
                queue_url = futures[future]
                attrs = future.result()
                queue_arn = attrs.get("QueueArn")
                queue_name = queue_url.rstrip("/").split("/")[-1]
                if queue_arn:
                    node_id = self._add_arn_node(queue_arn, label=queue_name, node_type="queue")
                else:
                    node_id = self._make_node_id("sqs", queue_url)
                    self.store.add_node(node_id, label=queue_name, service="sqs", type="queue", arn=queue_url)
                self.store.add_node(
                    node_id,
                    queue_url=queue_url,
                    visibility_timeout=attrs.get("VisibilityTimeout"),
                    created_timestamp=attrs.get("CreatedTimestamp"),
                )

    def _fetch_sqs_queue_attributes(self, client: Any, queue_url: str) -> Dict[str, Any]:
        self._increment_api_call("sqs", "get_queue_attributes")
        return client.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["QueueArn", "VisibilityTimeout", "CreatedTimestamp"],
        ).get("Attributes", {})

    def _scan_eventbridge(self, session: boto3.session.Session) -> None:
        client = self._client(session, "events")
        paginator = client.get_paginator("list_rules")
        rules: List[Dict[str, Any]] = []
        for page in paginator.paginate():
            self._increment_api_call("eventbridge", "list_rules")
            rules.extend(page.get("Rules", []))

        for rule in rules:
            rule_arn = rule.get("Arn") or f"rule:{rule.get('Name')}"
            rule_node = self._add_arn_node(rule_arn, label=rule.get("Name"), node_type="rule")
            self.store.add_node(
                rule_node,
                service="eventbridge",
                event_pattern=rule.get("EventPattern"),
                state=rule.get("State"),
                schedule_expression=rule.get("ScheduleExpression"),
            )

        workers = max(1, min(self.options.eventbridge_target_workers, len(rules) or 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._fetch_eventbridge_targets, client, rule): rule for rule in rules}
            for future in as_completed(futures):
                rule = futures[future]
                rule_arn = rule.get("Arn") or f"rule:{rule.get('Name')}"
                rule_node = self._make_node_id(self._service_from_arn(rule_arn), rule_arn)
                targets = future.result()
                for target in targets:
                    target_arn = target.get("Arn")
                    if not target_arn:
                        continue
                    target_node = self._add_arn_node(target_arn)
                    self.store.add_edge(
                        rule_node,
                        target_node,
                        relationship="triggers",
                        via="eventbridge_rule_target",
                        target_id=target.get("Id"),
                    )

    def _fetch_eventbridge_targets(self, client: Any, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
        targets: List[Dict[str, Any]] = []
        next_token: Optional[str] = None
        while True:
            kwargs: Dict[str, Any] = {"Rule": rule["Name"]}
            if rule.get("EventBusName"):
                kwargs["EventBusName"] = rule["EventBusName"]
            if next_token:
                kwargs["NextToken"] = next_token
            self._increment_api_call("eventbridge", "list_targets_by_rule")
            page = client.list_targets_by_rule(**kwargs)
            targets.extend(page.get("Targets", []))
            next_token = page.get("NextToken")
            if not next_token:
                break
        return targets

    def _scan_dynamodb(self, session: boto3.session.Session) -> None:
        client = self._client(session, "dynamodb")
        table_names: List[str] = []
        table_name: Optional[str] = None
        while True:
            kwargs: Dict[str, Any] = {}
            if table_name:
                kwargs["ExclusiveStartTableName"] = table_name
            self._increment_api_call("dynamodb", "list_tables")
            page = client.list_tables(**kwargs)
            table_names.extend(page.get("TableNames", []))
            table_name = page.get("LastEvaluatedTableName")
            if not table_name:
                break

        if not self.options.include_resource_describes:
            for name in table_names:
                node_id = self._make_node_id("dynamodb", name)
                self.store.add_node(node_id, label=name, service="dynamodb", type="table", arn=name)
            return

        workers = max(1, min(self.options.dynamodb_describe_workers, len(table_names) or 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._describe_table, client, name): name for name in table_names}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    table = future.result()
                except Exception as exc:
                    self.store.add_warning(f"dynamodb describe failed for {name}: {type(exc).__name__} - {exc}")
                    continue
                table_arn = table.get("TableArn", f"dynamodb:{name}")
                node_id = self._add_arn_node(table_arn, label=name, node_type="table")
                self.store.add_node(
                    node_id,
                    service="dynamodb",
                    item_count=table.get("ItemCount"),
                    table_size_bytes=table.get("TableSizeBytes"),
                    stream_arn=table.get("LatestStreamArn"),
                    billing_mode=(table.get("BillingModeSummary") or {}).get("BillingMode"),
                )

    def _describe_table(self, client: Any, table_name: str) -> Dict[str, Any]:
        self._increment_api_call("dynamodb", "describe_table")
        return client.describe_table(TableName=table_name).get("Table", {})

    def _scan_generic_service(self, session: boto3.session.Session, service_name: str) -> None:
        client = self._client(session, "resourcegroupstaggingapi")
        paginator = client.get_paginator("get_resources")

        discovered = 0
        try:
            page_iterator = paginator.paginate(ResourcesPerPage=100, ResourceTypeFilters=[service_name])
            for page in page_iterator:
                self._increment_api_call("resourcegroupstaggingapi", "get_resources")
                for entry in page.get("ResourceTagMappingList", []):
                    arn = entry.get("ResourceARN")
                    if not arn:
                        continue
                    discovered += 1
                    node_id = self._add_arn_node(arn)
                    tags = {item.get("Key"): item.get("Value") for item in entry.get("Tags", [])}
                    self.store.add_node(node_id, service=service_name, tags=tags)
        except (ClientError, BotoCoreError):
            discovered = 0

        if discovered == 0:
            self.store.add_warning(f"{service_name} scanner is not specialized yet; no resources discovered.")
