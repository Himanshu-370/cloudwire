from __future__ import annotations

from typing import Any, Dict, List

from botocore.exceptions import BotoCoreError, ClientError

from ._utils import logger


class RDSScannerMixin:
    def _scan_rds(self, session: "boto3.session.Session") -> None:
        client = self._client(session, "rds")
        cluster_nodes: Dict[str, str] = {}  # DBClusterIdentifier -> node_id
        instance_cluster_map: List[tuple[str, str]] = []  # (instance_node_id, cluster_identifier)

        # Instances
        paginator = client.get_paginator("describe_db_instances")
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("rds", "describe_db_instances")
            for db in page.get("DBInstances", []):
                self._ensure_not_cancelled()
                arn = db.get("DBInstanceArn", "")
                node_id = self._add_arn_node(arn, label=db.get("DBInstanceIdentifier"), node_type="instance")
                self._node(
                    node_id,
                    service="rds",
                    engine=db.get("Engine"),
                    instance_class=db.get("DBInstanceClass"),
                    state=db.get("DBInstanceStatus"),
                    multi_az=db.get("MultiAZ"),
                )
                # RDS VPC topology edges
                subnet_group = db.get("DBSubnetGroup", {})
                rds_vpc_id = subnet_group.get("VpcId")
                if rds_vpc_id:
                    rds_vpc_node = self._make_node_id("vpc", f"vpc/{rds_vpc_id}")
                    self._node(rds_vpc_node, label=rds_vpc_id, service="vpc", type="vpc")
                    self.store.add_edge(rds_vpc_node, node_id, relationship="contains", via="rds_vpc_membership")
                    for rds_subnet in subnet_group.get("Subnets", []):
                        rds_subnet_id = rds_subnet.get("SubnetIdentifier")
                        if rds_subnet_id:
                            rds_subnet_node = self._make_node_id("vpc", f"subnet/{rds_subnet_id}")
                            self._node(rds_subnet_node, label=rds_subnet_id, service="vpc", type="subnet")
                            self.store.add_edge(rds_subnet_node, node_id, relationship="contains", via="rds_subnet_placement")
                for vsg in db.get("VpcSecurityGroups", []):
                    rds_sg_id = vsg.get("VpcSecurityGroupId")
                    if rds_sg_id:
                        rds_sg_node = self._make_node_id("vpc", f"sg/{rds_sg_id}")
                        self._node(rds_sg_node, label=rds_sg_id, service="vpc", type="security_group")
                        self.store.add_edge(rds_sg_node, node_id, relationship="protects", via="rds_security_group")

                cluster_id = db.get("DBClusterIdentifier")
                if cluster_id:
                    instance_cluster_map.append((node_id, cluster_id))

        # Aurora clusters
        try:
            cluster_paginator = client.get_paginator("describe_db_clusters")
            for page in cluster_paginator.paginate():
                self._ensure_not_cancelled()
                self._increment_api_call("rds", "describe_db_clusters")
                for cluster in page.get("DBClusters", []):
                    self._ensure_not_cancelled()
                    arn = cluster.get("DBClusterArn", "")
                    cluster_id = cluster.get("DBClusterIdentifier", "")
                    node_id = self._add_arn_node(arn, label=cluster_id, node_type="cluster")
                    self._node(
                        node_id,
                        service="rds",
                        engine=cluster.get("Engine"),
                        state=cluster.get("Status"),
                    )
                    cluster_nodes[cluster_id] = node_id
        except (ClientError, BotoCoreError) as exc:
            logger.debug("RDS cluster scan skipped: %s", exc)

        # RDS cluster → instance edges
        for instance_node, cluster_id in instance_cluster_map:
            cluster_node = cluster_nodes.get(cluster_id)
            if cluster_node:
                self.store.add_edge(
                    cluster_node, instance_node, relationship="contains", via="rds_cluster_member"
                )
