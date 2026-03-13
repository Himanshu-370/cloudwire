"""Lambda scanner mixin for AWSGraphScanner."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import boto3
from botocore.exceptions import ClientError

from ._utils import logger, _ARN_PATTERN, _ENV_VAR_CONVENTIONS, _safe_list


class LambdaScannerMixin:
    """Mixin that adds Lambda scanning capabilities."""

    def _scan_lambda(self, session: boto3.session.Session) -> None:
        client = self._client(session, "lambda")
        paginator = client.get_paginator("list_functions")
        functions: List[Dict[str, Any]] = []
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("lambda", "list_functions")
            functions.extend(page.get("Functions", []))

        function_node_ids: Dict[str, str] = {}
        role_to_function_nodes: Dict[str, List[str]] = {}

        for fn in functions:
            self._ensure_not_cancelled()
            arn = fn["FunctionArn"]
            node_id = self._add_arn_node(arn, label=fn.get("FunctionName"), node_type="lambda")
            self._node(
                node_id,
                runtime=fn.get("Runtime"),
                handler=fn.get("Handler"),
                role=fn.get("Role"),
                memory_size=fn.get("MemorySize"),
                timeout=fn.get("Timeout"),
                last_modified=fn.get("LastModified"),
                state=fn.get("State"),
            )
            function_node_ids[arn] = node_id
            function_node_ids[self._base_lambda_arn(arn)] = node_id

            role_arn = fn.get("Role")
            if role_arn:
                role_name = role_arn.split("/")[-1]
                role_to_function_nodes.setdefault(role_name, []).append(node_id)
                # Phase 2, Item 4: IAM Role → Lambda edge
                if _ARN_PATTERN.match(role_arn):
                    role_node = self._add_arn_node(role_arn, label=role_name, node_type="role")
                    self._node(role_node, service="iam")
                    self.store.add_edge(role_node, node_id, relationship="assumed_by", via="lambda_execution_role")

            # Lambda VPC topology edges
            vpc_config = fn.get("VpcConfig", {})
            fn_vpc_id = vpc_config.get("VpcId")
            if fn_vpc_id:
                fn_vpc_node = self._make_node_id("vpc", f"vpc/{fn_vpc_id}")
                self._node(fn_vpc_node, label=fn_vpc_id, service="vpc", type="vpc")
                for fn_subnet_id in vpc_config.get("SubnetIds", []):
                    fn_subnet_node = self._make_node_id("vpc", f"subnet/{fn_subnet_id}")
                    self._node(fn_subnet_node, label=fn_subnet_id, service="vpc", type="subnet")
                    self.store.add_edge(fn_subnet_node, node_id, relationship="contains", via="lambda_vpc_placement")
                for fn_sg_id in vpc_config.get("SecurityGroupIds", []):
                    fn_sg_node = self._make_node_id("vpc", f"sg/{fn_sg_id}")
                    self._node(fn_sg_node, label=fn_sg_id, service="vpc", type="security_group")
                    self.store.add_edge(fn_sg_node, node_id, relationship="protects", via="lambda_security_group")

            # Phase 1, Item 1: Lambda env var edges
            self._extract_lambda_env_edges(fn, node_id)

        self._scan_lambda_event_sources_global(client, function_node_ids)
        if self.options.include_iam_inference:
            self._scan_lambda_iam_dependencies_parallel(session, role_to_function_nodes)

    def _extract_lambda_env_edges(self, fn: Dict[str, Any], function_node_id: str) -> None:
        """Extract edges from Lambda environment variables to referenced resources.

        Recognises explicit ARNs and well-known naming conventions (e.g. *_TABLE_NAME).
        Environment variable *values* are never logged to avoid leaking secrets.
        """
        env_vars = fn.get("Environment", {}).get("Variables", {})
        if not env_vars or not isinstance(env_vars, dict):
            return

        seen_targets: Set[str] = set()
        for key, value in env_vars.items():
            try:
                if not isinstance(value, str) or not value.strip():
                    continue
                value = value.strip()

                # 1. Explicit ARN reference
                if _ARN_PATTERN.match(value):
                    target = self._add_arn_node(value)
                    if target not in seen_targets:
                        seen_targets.add(target)
                        self.store.add_edge(
                            function_node_id, target,
                            relationship="references", via="lambda_env_var",
                        )
                    continue

                # 2. Naming convention fallback
                upper_key = key.upper()
                for suffix, (service, node_type) in _ENV_VAR_CONVENTIONS.items():
                    if not upper_key.endswith(suffix):
                        continue
                    # Reject values that look like config flags rather than resource names
                    if len(value) < 2 or len(value) > 256:
                        break
                    if service == "s3":
                        node_id = self._make_node_id("s3", value)
                        self._node(node_id, label=value, service="s3", type="bucket",
                                   arn=f"arn:aws:s3:::{value}")
                    else:
                        node_id = self._make_node_id(service, value)
                        self._node(node_id, label=value, service=service, type=node_type)
                    if node_id not in seen_targets:
                        seen_targets.add(node_id)
                        self.store.add_edge(
                            function_node_id, node_id,
                            relationship="references", via="lambda_env_var_convention",
                        )
                    break  # match at most one convention per variable
            except Exception:
                # Never let a single env var parsing error abort the scan.
                # Intentionally do not log the value (may contain secrets).
                logger.debug("Lambda env var edge extraction failed for key %s", key)

    def _scan_lambda_event_sources_global(self, client: Any, function_node_ids: Dict[str, str]) -> None:
        marker: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {}
            if marker:
                kwargs["Marker"] = marker
            self._increment_api_call("lambda", "list_event_source_mappings")
            page = client.list_event_source_mappings(**kwargs)
            for mapping in page.get("EventSourceMappings", []):
                self._ensure_not_cancelled()
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
            self._drain_futures(
                future_to_role,
                lambda future, role_name: self._apply_role_policy_dependencies(
                    role_name,
                    future,
                    role_to_function_nodes,
                ),
            )

    def _apply_role_policy_dependencies(
        self,
        role_name: str,
        future: Future[Any],
        role_to_function_nodes: Dict[str, List[str]],
    ) -> None:
        try:
            policy_details = future.result()
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("AccessDenied", "AccessDeniedException"):
                self.store.add_warning(f"[permission] iam: access denied reading policies for role {role_name}")
            else:
                self.store.add_warning(f"iam policy lookup failed for {role_name}: {error_code} - {exc}")
            return
        except Exception as exc:
            logger.warning("IAM policy lookup failed for role %s: %s", role_name, exc)
            self.store.add_warning(f"iam policy lookup failed for {role_name}: {type(exc).__name__}")
            return

        self._ensure_not_cancelled()
        for function_node_id in role_to_function_nodes.get(role_name, []):
            self._apply_policy_dependencies(function_node_id, policy_details)

    def _apply_policy_dependencies(self, function_node_id: str, statements: List[Dict[str, Any]]) -> None:
        for statement in statements:
            self._ensure_not_cancelled()
            effect = str(statement.get("Effect", "Allow")).lower()
            if effect != "allow":
                continue
            actions = [str(action).lower() for action in _safe_list(statement.get("Action"))]
            resources = [str(resource) for resource in _safe_list(statement.get("Resource"))]

            service_hits = self._services_from_actions(actions)
            for service in service_hits:
                for resource in resources or ["*"]:
                    if resource == "*":
                        continue  # wildcard would create meaningless *:* phantom nodes
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
            normalized = self._IAM_PREFIX_TO_SERVICE.get(prefix)
            if normalized:
                service_actions.setdefault(normalized, set()).add(verb)
        return service_actions

    def _target_from_service_resource(self, service: str, resource: str) -> str:
        self._ensure_not_cancelled()
        if resource.startswith("arn:aws:"):
            target = self._add_arn_node(resource, node_type="resource")
            self._node(target, service=service)
            return target
        node_id = self._make_node_id(service, resource)
        self._node(node_id, label=resource, service=service, type="resource", arn=resource)
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

        inline_policy_names: List[str] = []
        inline_marker: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            inline_kwargs: Dict[str, Any] = {"RoleName": role_name}
            if inline_marker:
                inline_kwargs["Marker"] = inline_marker
            self._increment_api_call("iam", "list_role_policies")
            inline_page = iam.list_role_policies(**inline_kwargs)
            inline_policy_names.extend(inline_page.get("PolicyNames", []))
            inline_marker = inline_page.get("Marker") if inline_page.get("IsTruncated") else None
            if not inline_marker:
                break
        for policy_name in inline_policy_names:
            self._ensure_not_cancelled()
            self._increment_api_call("iam", "get_role_policy")
            raw = iam.get_role_policy(RoleName=role_name, PolicyName=policy_name)
            policy_docs.append(raw.get("PolicyDocument", {}))

        attached_policies: List[Dict[str, Any]] = []
        marker: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
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
            self._ensure_not_cancelled()
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
