from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ._utils import _ARN_PATTERN, logger


class ECSScannerMixin:
    def _scan_ecs(self, session: boto3.session.Session) -> None:
        client = self._client(session, "ecs")
        cluster_arns: List[str] = []
        paginator = client.get_paginator("list_clusters")
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("ecs", "list_clusters")
            cluster_arns.extend(page.get("clusterArns", []))

        for arn in cluster_arns:
            self._ensure_not_cancelled()
            cluster_name = arn.split("/")[-1]
            cluster_node = self._add_arn_node(arn, label=cluster_name, node_type="cluster")
            self._node(cluster_node, service="ecs")

            # List services in this cluster
            svc_arns: List[str] = []
            svc_paginator = client.get_paginator("list_services")
            for svc_page in svc_paginator.paginate(cluster=arn):
                self._ensure_not_cancelled()
                self._increment_api_call("ecs", "list_services")
                svc_arns.extend(svc_page.get("serviceArns", []))

            for svc_arn in svc_arns:
                self._ensure_not_cancelled()
                svc_name = svc_arn.split("/")[-1]
                svc_node = self._add_arn_node(svc_arn, label=svc_name, node_type="service")
                self._node(svc_node, service="ecs")
                self.store.add_edge(cluster_node, svc_node, relationship="hosts")

            # Phase 2, Item 6: ECS describe_services for task def, LB, and role edges
            if svc_arns and self.options.include_resource_describes:
                self._describe_ecs_service_edges(client, arn, svc_arns)

    def _describe_ecs_service_edges(self, client: Any, cluster_arn: str, service_arns: List[str]) -> None:
        """Enrich ECS services with task definition, load balancer, and role edges."""
        # describe_services accepts max 10 at a time
        for batch_start in range(0, len(service_arns), 10):
            self._ensure_not_cancelled()
            batch = service_arns[batch_start:batch_start + 10]
            try:
                self._increment_api_call("ecs", "describe_services")
                response = client.describe_services(cluster=cluster_arn, services=batch)
            except (ClientError, BotoCoreError) as exc:
                logger.debug("ECS describe_services failed for cluster %s: %s", cluster_arn.split("/")[-1], exc)
                continue

            for svc in response.get("services", []):
                svc_arn = svc.get("serviceArn", "")
                svc_node = self._make_node_id(self._service_from_arn(svc_arn), svc_arn) if svc_arn else None
                if not svc_node:
                    continue

                # Task definition edge
                task_def_arn = svc.get("taskDefinition", "")
                if task_def_arn and _ARN_PATTERN.match(task_def_arn):
                    td_node = self._add_arn_node(task_def_arn, label=task_def_arn.split("/")[-1],
                                                 node_type="task_definition")
                    self._node(td_node, service="ecs")
                    self.store.add_edge(svc_node, td_node, relationship="uses", via="ecs_task_definition")

                # Load balancer / target group edges
                for lb in svc.get("loadBalancers", []):
                    tg_arn = lb.get("targetGroupArn", "")
                    if tg_arn and _ARN_PATTERN.match(tg_arn):
                        tg_node = self._add_arn_node(tg_arn, label=tg_arn.split("/")[-1],
                                                     node_type="target_group")
                        self._node(tg_node, service="elb")
                        self.store.add_edge(svc_node, tg_node, relationship="registered_with",
                                            via="ecs_load_balancer")

                # Service role edge
                role_arn = svc.get("roleArn", "")
                if role_arn and _ARN_PATTERN.match(role_arn):
                    role_node = self._add_arn_node(role_arn, label=role_arn.split("/")[-1], node_type="role")
                    self._node(role_node, service="iam")
                    self.store.add_edge(role_node, svc_node, relationship="assumed_by",
                                        via="ecs_service_role")

                # ECS VPC topology edges
                net_config = svc.get("networkConfiguration", {}).get("awsvpcConfiguration", {})
                for ecs_subnet_id in net_config.get("subnets", []):
                    ecs_subnet_node = self._make_node_id("vpc", f"subnet/{ecs_subnet_id}")
                    self._node(ecs_subnet_node, label=ecs_subnet_id, service="vpc", type="subnet")
                    self.store.add_edge(ecs_subnet_node, svc_node, relationship="contains", via="ecs_vpc_placement")
                for ecs_sg_id in net_config.get("securityGroups", []):
                    ecs_sg_node = self._make_node_id("vpc", f"sg/{ecs_sg_id}")
                    self._node(ecs_sg_node, label=ecs_sg_id, service="vpc", type="security_group")
                    self.store.add_edge(ecs_sg_node, svc_node, relationship="protects", via="ecs_security_group")
