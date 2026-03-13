from __future__ import annotations

from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ._utils import logger


class RedshiftScannerMixin:

    def _scan_redshift(self, session: boto3.session.Session) -> None:
        client = self._client(session, "redshift")
        try:
            paginator = client.get_paginator("describe_clusters")
            for page in paginator.paginate():
                self._ensure_not_cancelled()
                self._increment_api_call("redshift", "describe_clusters")
                for cluster in page.get("Clusters", []):
                    self._ensure_not_cancelled()
                    cluster_id = cluster.get("ClusterIdentifier", "")
                    arn = f"arn:aws:redshift:{self._region}:{self._account_id}:cluster:{cluster_id}"
                    node_id = self._make_node_id("redshift", cluster_id)
                    self._node(
                        node_id,
                        label=cluster_id,
                        service="redshift",
                        type="cluster",
                        arn=arn,
                        state=cluster.get("ClusterStatus"),
                        node_type=cluster.get("NodeType"),
                        num_nodes=cluster.get("NumberOfNodes"),
                        db_name=cluster.get("DBName"),
                        vpc_id=cluster.get("VpcId"),
                    )
                    # Redshift VPC topology edges
                    rs_vpc_id = cluster.get("VpcId")
                    if rs_vpc_id:
                        rs_vpc_node = self._make_node_id("vpc", f"vpc/{rs_vpc_id}")
                        self._node(rs_vpc_node, label=rs_vpc_id, service="vpc", type="vpc")
                        self.store.add_edge(rs_vpc_node, node_id, relationship="contains", via="redshift_vpc_membership")
                    for vsg in cluster.get("VpcSecurityGroups", []):
                        rs_sg_id = vsg.get("VpcSecurityGroupId")
                        if rs_sg_id:
                            rs_sg_node = self._make_node_id("vpc", f"sg/{rs_sg_id}")
                            self._node(rs_sg_node, label=rs_sg_id, service="vpc", type="security_group")
                            self.store.add_edge(rs_sg_node, node_id, relationship="protects", via="redshift_security_group")
        except (ClientError, BotoCoreError) as exc:
            logger.warning("Redshift scan failed: %s", exc)
