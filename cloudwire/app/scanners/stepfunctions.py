from __future__ import annotations

import json
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from botocore.exceptions import BotoCoreError, ClientError

from ._utils import logger


class StepFunctionsScannerMixin:
    def _scan_stepfunctions(self, session: "boto3.session.Session") -> None:
        client = self._client(session, "stepfunctions")
        sm_arns: List[tuple[str, str]] = []  # (arn, node_id)

        paginator = client.get_paginator("list_state_machines")
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("stepfunctions", "list_state_machines")
            for sm in page.get("stateMachines", []):
                self._ensure_not_cancelled()
                arn = sm.get("stateMachineArn", "")
                node_id = self._add_arn_node(arn, label=sm.get("name"), node_type="state_machine")
                self._node(
                    node_id,
                    service="stepfunctions",
                    sm_type=sm.get("type"),
                    creation_date=str(sm.get("creationDate", "")),
                )
                sm_arns.append((arn, node_id))

        # Step Functions → Lambda / ECS / DynamoDB / SQS / SNS (ASL task resources)
        if sm_arns:
            workers = max(1, min(8, len(sm_arns)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._fetch_sfn_definition, client, arn): node_id
                    for arn, node_id in sm_arns
                }
                self._drain_futures(futures, self._apply_sfn_edges)

    def _fetch_sfn_definition(self, client: Any, arn: str) -> Optional[str]:
        try:
            self._increment_api_call("stepfunctions", "describe_state_machine")
            return client.describe_state_machine(stateMachineArn=arn).get("definition")
        except (ClientError, BotoCoreError) as exc:
            logger.debug("Step Functions describe failed for %s: %s", arn, exc)
            return None

    def _apply_sfn_edges(self, future: Future[Any], sm_node: str) -> None:
        try:
            definition_str = future.result()
        except Exception:
            return
        if not definition_str:
            return
        self._ensure_not_cancelled()
        try:
            definition = json.loads(definition_str)
        except Exception:
            return

        # Walk all states and extract Task resource ARNs
        states = definition.get("States", {})
        self._extract_sfn_state_edges(sm_node, states)

    def _extract_sfn_state_edges(self, sm_node: str, states: Dict[str, Any]) -> None:
        """Recursively traverse Step Functions states to find Task resource ARNs."""
        for state_name, state in states.items():
            self._ensure_not_cancelled()
            state_type = state.get("Type", "")

            if state_type == "Task":
                resource = state.get("Resource", "")
                params = state.get("Parameters", {})
                self._apply_sfn_task_edge(sm_node, resource, params)

            # Recurse into Parallel branches
            for branch in state.get("Branches", []):
                self._extract_sfn_state_edges(sm_node, branch.get("States", {}))

            # Recurse into Map iterator
            iterator = state.get("Iterator") or state.get("ItemProcessor", {})
            if iterator:
                self._extract_sfn_state_edges(sm_node, iterator.get("States", {}))

    def _apply_sfn_task_edge(self, sm_node: str, resource: str, params: Dict[str, Any]) -> None:
        """Resolve a Step Functions Task resource to a target node and add an edge."""
        if not resource:
            return

        # Direct Lambda ARN: arn:aws:lambda:...
        if ":lambda:" in resource and ":function:" in resource:
            target = self._add_arn_node(resource.split(":$")[0])
            self.store.add_edge(sm_node, target, relationship="invokes", via="sfn_task")
            return

        # Optimised integrations: arn:aws:states:::lambda:invoke
        if "states:::lambda" in resource:
            fn_arn = (params.get("FunctionName") or params.get("FunctionName.$", "")).split(":$")[0]
            if fn_arn.startswith("arn:aws:lambda:"):
                target = self._add_arn_node(fn_arn)
                self.store.add_edge(sm_node, target, relationship="invokes", via="sfn_task")
            return

        if "states:::dynamodb" in resource:
            table_name = params.get("TableName") or params.get("TableName.$", "")
            if table_name and not table_name.startswith("$"):
                node_id = self._make_node_id("dynamodb", table_name)
                self._node(node_id, label=table_name, service="dynamodb", type="table", arn=table_name)
                self.store.add_edge(sm_node, node_id, relationship="reads_writes", via="sfn_task")
            return

        if "states:::sqs" in resource:
            queue_url = params.get("QueueUrl") or params.get("QueueUrl.$", "")
            if queue_url and not queue_url.startswith("$"):
                node_id = self._make_node_id("sqs", queue_url)
                self._node(node_id, label=queue_url.split("/")[-1], service="sqs", type="queue", arn=queue_url)
                self.store.add_edge(sm_node, node_id, relationship="sends_to", via="sfn_task")
            return

        if "states:::sns" in resource:
            topic_arn = params.get("TopicArn") or params.get("TopicArn.$", "")
            if topic_arn and topic_arn.startswith("arn:aws:sns:"):
                target = self._add_arn_node(topic_arn)
                self.store.add_edge(sm_node, target, relationship="publishes_to", via="sfn_task")
            return

        if "states:::ecs" in resource:
            task_def = (params.get("TaskDefinition") or "").split(":")[0]
            cluster_arn = params.get("Cluster", "")
            if cluster_arn.startswith("arn:aws:ecs:"):
                target = self._add_arn_node(cluster_arn)
                self.store.add_edge(sm_node, target, relationship="runs_task", via="sfn_task")
            return

        if "states:::glue" in resource:
            job_name = params.get("JobName") or params.get("JobName.$", "")
            if job_name and not job_name.startswith("$"):
                node_id = self._make_node_id("glue", job_name)
                self._node(node_id, label=job_name, service="glue", type="job",
                            arn=f"arn:aws:glue:{self._region}:*:job/{job_name}")
                self.store.add_edge(sm_node, node_id, relationship="runs_job", via="sfn_task")
            return

        if "states:::states:startExecution" in resource:
            child_arn = params.get("StateMachineArn") or params.get("StateMachineArn.$", "")
            if child_arn and child_arn.startswith("arn:aws:states:"):
                target = self._add_arn_node(child_arn)
                self.store.add_edge(sm_node, target, relationship="starts", via="sfn_task")
