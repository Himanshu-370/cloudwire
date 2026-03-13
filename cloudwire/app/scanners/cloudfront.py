from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

import boto3

from ._utils import _ARN_PATTERN, logger


class CloudFrontScannerMixin:
    def _scan_cloudfront(self, session: boto3.session.Session) -> None:
        # CloudFront is a global service — always query us-east-1
        client = session.client("cloudfront", config=self._client_config)
        paginator = client.get_paginator("list_distributions")
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("cloudfront", "list_distributions")
            dist_list = page.get("DistributionList", {})
            for dist in dist_list.get("Items", []):
                self._ensure_not_cancelled()
                arn = dist.get("ARN", "")
                domain = dist.get("DomainName", "")
                node_id = self._add_arn_node(arn, label=domain or dist.get("Id"), node_type="distribution")
                self._node(
                    node_id,
                    service="cloudfront",
                    state=dist.get("Status"),
                    domain=domain,
                )
                # CloudFront → S3 / API Gateway / ALB origins
                for origin in (dist.get("Origins") or {}).get("Items", []):
                    origin_domain = origin.get("DomainName", "")
                    # S3 origins: bucket.s3.amazonaws.com or bucket.s3.region.amazonaws.com
                    if ".s3." in origin_domain or origin_domain.endswith(".s3.amazonaws.com"):
                        bucket_name = origin_domain.split(".s3.")[0]
                        s3_node = self._make_node_id("s3", bucket_name)
                        self._node(
                            s3_node,
                            label=bucket_name,
                            service="s3",
                            type="bucket",
                            arn=f"arn:aws:s3:::{bucket_name}",
                        )
                        self.store.add_edge(
                            node_id, s3_node, relationship="serves_from", via="cloudfront_origin"
                        )
                    elif "execute-api" in origin_domain:
                        # API Gateway origin
                        api_id = origin_domain.split(".execute-api.")[0] if ".execute-api." in origin_domain else origin_domain
                        apigw_node = self._make_node_id("apigateway", api_id)
                        self._node(apigw_node, label=api_id, service="apigateway", type="api")
                        self.store.add_edge(node_id, apigw_node, relationship="serves_from",
                                            via="cloudfront_origin")
                    elif ".elb.amazonaws.com" in origin_domain or ".elasticloadbalancing." in origin_domain:
                        # ALB/ELB origin
                        elb_node = self._make_node_id("elb", origin_domain)
                        self._node(elb_node, label=origin_domain, service="elb", type="load_balancer")
                        self.store.add_edge(node_id, elb_node, relationship="serves_from",
                                            via="cloudfront_origin")

                # Phase 2, Item 7: CloudFront → Lambda@Edge (once per distribution)
                self._extract_cloudfront_lambda_edges(node_id, dist)

    def _extract_cloudfront_lambda_edges(self, cf_node: str, dist: Dict[str, Any]) -> None:
        """Extract Lambda@Edge associations from CloudFront cache behaviors."""
        behaviors: List[Dict[str, Any]] = []
        default_behavior = dist.get("DefaultCacheBehavior")
        if default_behavior:
            behaviors.append(default_behavior)
        for behavior in (dist.get("CacheBehaviors") or {}).get("Items", []):
            behaviors.append(behavior)

        seen_arns: Set[str] = set()
        for behavior in behaviors:
            for assoc in (behavior.get("LambdaFunctionAssociations") or {}).get("Items", []):
                fn_arn = assoc.get("LambdaFunctionARN", "")
                if not fn_arn or not fn_arn.startswith("arn:aws:lambda:") or fn_arn in seen_arns:
                    continue
                seen_arns.add(fn_arn)
                base_arn = self._base_lambda_arn(fn_arn)
                target = self._add_arn_node(base_arn, node_type="lambda")
                self.store.add_edge(cf_node, target, relationship="invokes",
                                    via="cloudfront_lambda_edge",
                                    event_type=assoc.get("EventType"))
