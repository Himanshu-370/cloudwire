from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ._utils import logger


class Route53ScannerMixin:

    # Map Route 53 canonical hosted zone IDs to AWS service types for alias target detection
    _R53_ALIAS_ZONE_TO_SERVICE: Dict[str, str] = {
        "Z2FDTNDATAQYW2": "cloudfront",   # CloudFront global
        "Z35SXDOTRQ7X7K": "elb",          # us-east-1 ELB
        "Z368ELLRRE2KJ0": "elb",          # us-west-2 ELB
        "Z3DZXE0Q79N41H": "elb",          # us-west-1 ELB
        "Z1H1FL5HABSF5":  "elb",          # ap-southeast-1 ELB
        "Z3QFB96KE08076": "elb",          # ap-southeast-2 ELB
        "Z3AADJGX6KTTL2": "elb",          # ap-northeast-1 ELB
        "Z215JYRZR1TBD5": "elb",          # eu-west-1 ELB
    }

    def _scan_route53(self, session: boto3.session.Session) -> None:
        # Route 53 is global — use us-east-1
        client = session.client("route53", config=self._client_config)
        zone_nodes: List[tuple[str, str]] = []  # (zone_id, node_id)

        # Hosted zones
        marker: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {"MaxItems": "100"}
            if marker:
                kwargs["Marker"] = marker
            self._increment_api_call("route53", "list_hosted_zones")
            page = client.list_hosted_zones(**kwargs)
            for zone in page.get("HostedZones", []):
                self._ensure_not_cancelled()
                zone_id = zone["Id"].split("/")[-1]
                zone_name = zone.get("Name", zone_id).rstrip(".")
                arn = f"arn:aws:route53:::hostedzone/{zone_id}"
                node_id = self._make_node_id("route53", zone_id)
                self._node(
                    node_id,
                    label=zone_name,
                    service="route53",
                    type="hosted_zone",
                    arn=arn,
                    private_zone=zone.get("Config", {}).get("PrivateZone", False),
                    record_count=zone.get("ResourceRecordSetCount"),
                )
                zone_nodes.append((zone_id, node_id))
            if not page.get("IsTruncated"):
                break
            marker = page.get("NextMarker")

        # Route 53 → CloudFront / ELB (alias records)
        if zone_nodes:
            workers = max(1, min(8, len(zone_nodes)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._fetch_r53_alias_targets, client, zone_id): zone_node
                    for zone_id, zone_node in zone_nodes
                }
                self._drain_futures(futures, self._apply_r53_edges)

    def _fetch_r53_alias_targets(self, client: Any, zone_id: str) -> List[Dict[str, Any]]:
        aliases: List[Dict[str, Any]] = []
        next_id: Optional[str] = None
        next_type: Optional[str] = None
        while True:
            try:
                self._ensure_not_cancelled()
                kwargs: Dict[str, Any] = {"HostedZoneId": zone_id, "MaxItems": "300"}
                if next_id:
                    kwargs["StartRecordName"] = next_id
                    kwargs["StartRecordType"] = next_type
                self._increment_api_call("route53", "list_resource_record_sets")
                page = client.list_resource_record_sets(**kwargs)
                for record in page.get("ResourceRecordSets", []):
                    alias = record.get("AliasTarget")
                    if alias:
                        aliases.append({
                            "name": record.get("Name", "").rstrip("."),
                            "dns": alias.get("DNSName", "").rstrip("."),
                            "zone": alias.get("HostedZoneId", ""),
                        })
                if not page.get("IsTruncated"):
                    break
                next_id = page.get("NextRecordName")
                next_type = page.get("NextRecordType")
            except (ClientError, BotoCoreError) as exc:
                logger.debug("Route53 list_resource_record_sets failed for %s: %s", zone_id, exc)
                break
        return aliases

    def _apply_r53_edges(self, future: Future[Any], zone_node: str) -> None:
        try:
            aliases = future.result()
        except Exception:
            return
        self._ensure_not_cancelled()
        for alias in aliases:
            target_svc = self._R53_ALIAS_ZONE_TO_SERVICE.get(alias["zone"])
            dns = alias["dns"]
            if target_svc == "cloudfront" and ".cloudfront.net" in dns:
                # Look up existing CloudFront node by domain, or create a phantom
                existing = self._find_node_by_attr("cloudfront", "domain", dns)
                if existing:
                    cf_node = existing
                else:
                    cf_node = self._make_node_id("cloudfront", dns)
                    self._node(cf_node, label=dns, service="cloudfront", type="distribution", domain=dns, phantom=True)
                self.store.add_edge(zone_node, cf_node, relationship="routes_to", via="route53_alias")
            elif "execute-api" in dns:
                # Phase 3, Item 9: Route53 → API Gateway
                api_id = dns.split(".execute-api.")[0] if ".execute-api." in dns else dns
                apigw_node = self._make_node_id("apigateway", api_id)
                self._node(apigw_node, label=api_id, service="apigateway", type="api")
                self.store.add_edge(zone_node, apigw_node, relationship="routes_to", via="route53_alias")
            elif ".s3-website" in dns or dns.endswith(".s3.amazonaws.com"):
                # Phase 3, Item 9: Route53 → S3 website
                bucket_name = dns.split(".s3")[0]
                if bucket_name:
                    s3_node = self._make_node_id("s3", bucket_name)
                    self._node(s3_node, label=bucket_name, service="s3", type="bucket",
                               arn=f"arn:aws:s3:::{bucket_name}")
                    self.store.add_edge(zone_node, s3_node, relationship="routes_to", via="route53_alias")
            elif target_svc == "elb":
                # Look up existing ELB node by domain, or create a phantom
                existing = self._find_node_by_attr("elb", "label", dns)
                if existing:
                    elb_node = existing
                else:
                    elb_node = self._make_node_id("elb", dns)
                    self._node(elb_node, label=dns, service="elb", type="load_balancer", phantom=True)
                self.store.add_edge(zone_node, elb_node, relationship="routes_to", via="route53_alias")
