from __future__ import annotations

from typing import Any, Dict, List, Optional

import boto3

from ._utils import logger


class ElastiCacheScannerMixin:

    def _scan_elasticache(self, session: boto3.session.Session) -> None:
        client = self._client(session, "elasticache")
        paginator = client.get_paginator("describe_cache_clusters")
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("elasticache", "describe_cache_clusters")
            for cluster in page.get("CacheClusters", []):
                self._ensure_not_cancelled()
                arn = cluster.get("ARN", "")
                cluster_id = cluster.get("CacheClusterId", "")
                node_id = self._add_arn_node(arn, label=cluster_id, node_type="cluster") if arn else self._make_node_id("elasticache", cluster_id)
                if not arn:
                    arn = f"arn:aws:elasticache:{self._region}:{self._account_id}:cluster/{cluster_id}"
                    self._node(node_id, label=cluster_id, service="elasticache", type="cluster", arn=arn)
                self._node(
                    node_id,
                    service="elasticache",
                    engine=cluster.get("Engine"),
                    engine_version=cluster.get("EngineVersion"),
                    node_type=cluster.get("CacheNodeType"),
                    state=cluster.get("CacheClusterStatus"),
                )
                # ElastiCache VPC topology edges
                for ec_sg in cluster.get("SecurityGroups", []):
                    ec_sg_id = ec_sg.get("SecurityGroupId")
                    if ec_sg_id:
                        ec_sg_node = self._make_node_id("vpc", f"sg/{ec_sg_id}")
                        self._node(ec_sg_node, label=ec_sg_id, service="vpc", type="security_group")
                        self.store.add_edge(ec_sg_node, node_id, relationship="protects", via="elasticache_security_group")
