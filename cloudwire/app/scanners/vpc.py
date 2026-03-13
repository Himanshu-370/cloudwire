from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

import boto3

from ._utils import logger


class VPCScannerMixin:
    def _scan_vpc(self, session: boto3.session.Session, *, vpc_ids: Optional[Set[str]] = None) -> None:
        client = self._client(session, "ec2")
        vpc_id_list = list(vpc_ids) if vpc_ids else None

        # 1. VPCs
        vpc_kwargs = {"VpcIds": vpc_id_list} if vpc_id_list else {}
        for page in client.get_paginator("describe_vpcs").paginate(**vpc_kwargs):
            self._ensure_not_cancelled()
            self._increment_api_call("ec2", "describe_vpcs")
            for vpc in page.get("Vpcs", []):
                self._ensure_not_cancelled()
                vpc_id = vpc["VpcId"]
                owner_id = vpc.get("OwnerId", "")
                name_tag = next((t["Value"] for t in vpc.get("Tags", []) if t.get("Key") == "Name"), None)
                node_id = self._make_node_id("vpc", f"vpc/{vpc_id}")
                self._node(
                    node_id,
                    label=name_tag or vpc_id,
                    service="vpc",
                    type="vpc",
                    arn=f"arn:aws:ec2:{self._region}:{owner_id}:vpc/{vpc_id}",
                    cidr_block=vpc.get("CidrBlock"),
                    is_default=vpc.get("IsDefault", False),
                    state=vpc.get("State"),
                )

        # 2. Subnets
        subnet_kwargs = {"Filters": [{"Name": "vpc-id", "Values": vpc_id_list}]} if vpc_id_list else {}
        for page in client.get_paginator("describe_subnets").paginate(**subnet_kwargs):
            self._ensure_not_cancelled()
            self._increment_api_call("ec2", "describe_subnets")
            for subnet in page.get("Subnets", []):
                self._ensure_not_cancelled()
                subnet_id = subnet["SubnetId"]
                vpc_id = subnet.get("VpcId")
                owner_id = subnet.get("OwnerId", "")
                name_tag = next((t["Value"] for t in subnet.get("Tags", []) if t.get("Key") == "Name"), None)
                node_id = self._make_node_id("vpc", f"subnet/{subnet_id}")
                self._node(
                    node_id,
                    label=name_tag or subnet_id,
                    service="vpc",
                    type="subnet",
                    arn=f"arn:aws:ec2:{self._region}:{owner_id}:subnet/{subnet_id}",
                    cidr_block=subnet.get("CidrBlock"),
                    availability_zone=subnet.get("AvailabilityZone"),
                    map_public_ip_on_launch=subnet.get("MapPublicIpOnLaunch", False),
                    available_ip_count=subnet.get("AvailableIpAddressCount"),
                )
                if vpc_id:
                    vpc_node = self._make_node_id("vpc", f"vpc/{vpc_id}")
                    self.store.add_edge(vpc_node, node_id, relationship="contains", via="vpc_subnet")

        # 3. Security Groups
        # Accumulate SG rule edges to handle DiGraph single-edge constraint
        pending_sg_edges: Dict[tuple, List[str]] = {}  # (src, tgt) -> list of port labels
        sg_vpc_map: Dict[str, str] = {}  # sg_node_id -> vpc_id

        sg_kwargs = {"Filters": [{"Name": "vpc-id", "Values": vpc_id_list}]} if vpc_id_list else {}
        for page in client.get_paginator("describe_security_groups").paginate(**sg_kwargs):
            self._ensure_not_cancelled()
            self._increment_api_call("ec2", "describe_security_groups")
            for sg in page.get("SecurityGroups", []):
                self._ensure_not_cancelled()
                sg_id = sg["GroupId"]
                vpc_id = sg.get("VpcId")
                owner_id = sg.get("OwnerId", "")
                inbound_rules = sg.get("IpPermissions", [])
                outbound_rules = sg.get("IpPermissionsEgress", [])
                has_open_ingress = any(
                    any(r.get("CidrIp") == "0.0.0.0/0" for r in perm.get("IpRanges", [])) or
                    any(r.get("CidrIpv6") == "::/0" for r in perm.get("Ipv6Ranges", []))
                    for perm in inbound_rules
                )
                node_id = self._make_node_id("vpc", f"sg/{sg_id}")

                # Parse rules for tooltip display and edge creation
                parsed_in = self._parse_sg_rules(inbound_rules)
                parsed_out = self._parse_sg_rules(outbound_rules)

                self._node(
                    node_id,
                    label=sg.get("GroupName", sg_id),
                    service="vpc",
                    type="security_group",
                    arn=f"arn:aws:ec2:{self._region}:{owner_id}:security-group/{sg_id}",
                    group_name=sg.get("GroupName"),
                    description=sg.get("Description"),
                    inbound_rule_count=len(inbound_rules),
                    outbound_rule_count=len(outbound_rules),
                    has_open_ingress=has_open_ingress,
                    inbound_rules_parsed=parsed_in,
                    outbound_rules_parsed=parsed_out,
                )
                if vpc_id:
                    vpc_node = self._make_node_id("vpc", f"vpc/{vpc_id}")
                    self.store.add_edge(vpc_node, node_id, relationship="contains", via="vpc_security_group")
                    sg_vpc_map[node_id] = vpc_id

                # Accumulate SG rule edges from inbound rules
                for rule in parsed_in:
                    port_label = rule["port_range"]
                    if rule["source_type"] == "cidr" and rule["source"] in ("0.0.0.0/0", "::/0"):
                        # Will connect from Internet node later (after IGW scan)
                        if vpc_id:
                            internet_key = ("__internet__", vpc_id, node_id)
                            pending_sg_edges.setdefault(internet_key, []).append(port_label)
                    elif rule["source_type"] == "sg":
                        ref_sg_node = self._make_node_id("vpc", f"sg/{rule['source']}")
                        pending_sg_edges.setdefault((ref_sg_node, node_id), []).append(port_label)

        # 4. Internet Gateways
        vpc_to_igws: Dict[str, List[str]] = {}  # vpc_id -> list of igw node IDs

        igw_kwargs = {"Filters": [{"Name": "attachment.vpc-id", "Values": vpc_id_list}]} if vpc_id_list else {}
        for page in client.get_paginator("describe_internet_gateways").paginate(**igw_kwargs):
            self._ensure_not_cancelled()
            self._increment_api_call("ec2", "describe_internet_gateways")
            for igw in page.get("InternetGateways", []):
                self._ensure_not_cancelled()
                igw_id = igw["InternetGatewayId"]
                owner_id = igw.get("OwnerId", "")
                name_tag = next((t["Value"] for t in igw.get("Tags", []) if t.get("Key") == "Name"), None)
                node_id = self._make_node_id("vpc", f"igw/{igw_id}")
                self._node(
                    node_id,
                    label=name_tag or igw_id,
                    service="vpc",
                    type="internet_gateway",
                    arn=f"arn:aws:ec2:{self._region}:{owner_id}:internet-gateway/{igw_id}",
                )
                for attachment in igw.get("Attachments", []):
                    att_vpc_id = attachment.get("VpcId")
                    if att_vpc_id:
                        vpc_node = self._make_node_id("vpc", f"vpc/{att_vpc_id}")
                        self.store.add_edge(node_id, vpc_node, relationship="attached_to", via="igw_attachment")
                        vpc_to_igws.setdefault(att_vpc_id, []).append(node_id)

        # 4b. Internet Anchor Nodes — one per VPC with an IGW
        for vpc_id, igw_node_ids in vpc_to_igws.items():
            internet_node = self._make_node_id("vpc", f"internet/{vpc_id}")
            self._node(
                internet_node,
                label="Internet",
                service="vpc",
                type="internet",
            )
            for igw_node_id in igw_node_ids:
                self.store.add_edge(internet_node, igw_node_id, relationship="gateway", via="internet_igw")

        # 4c. Emit accumulated SG rule edges
        for key, port_labels in pending_sg_edges.items():
            if isinstance(key[0], str) and key[0] == "__internet__":
                # Internet -> SG edge: resolve to the Internet anchor node for this VPC
                _, vpc_id, sg_node = key
                internet_node = self._make_node_id("vpc", f"internet/{vpc_id}")
                self.store.add_edge(
                    internet_node, sg_node,
                    relationship="allows", via="sg_rule",
                    port_range=", ".join(sorted(set(port_labels))),
                )
            else:
                src_node, tgt_node = key
                self.store.add_edge(
                    src_node, tgt_node,
                    relationship="allows", via="sg_rule",
                    port_range=", ".join(sorted(set(port_labels))),
                )

        # 5. NAT Gateways
        nat_kwargs = {"Filters": [{"Name": "vpc-id", "Values": vpc_id_list}]} if vpc_id_list else {}
        for page in client.get_paginator("describe_nat_gateways").paginate(**nat_kwargs):
            self._ensure_not_cancelled()
            self._increment_api_call("ec2", "describe_nat_gateways")
            for nat in page.get("NatGateways", []):
                self._ensure_not_cancelled()
                nat_id = nat["NatGatewayId"]
                nat_subnet_id = nat.get("SubnetId")
                owner_id = nat.get("OwnerId", "")
                name_tag = next((t["Value"] for t in nat.get("Tags", []) if t.get("Key") == "Name"), None)
                node_id = self._make_node_id("vpc", f"nat/{nat_id}")
                self._node(
                    node_id,
                    label=name_tag or nat_id,
                    service="vpc",
                    type="nat_gateway",
                    arn=f"arn:aws:ec2:{self._region}:{owner_id}:natgateway/{nat_id}",
                    state=nat.get("State"),
                    connectivity_type=nat.get("ConnectivityType"),
                    subnet_id=nat_subnet_id,
                )
                if nat_subnet_id:
                    subnet_node = self._make_node_id("vpc", f"subnet/{nat_subnet_id}")
                    self.store.add_edge(subnet_node, node_id, relationship="contains", via="subnet_nat_gateway")

        # 6. Route Tables
        rtb_kwargs = {"Filters": [{"Name": "vpc-id", "Values": vpc_id_list}]} if vpc_id_list else {}
        for page in client.get_paginator("describe_route_tables").paginate(**rtb_kwargs):
            self._ensure_not_cancelled()
            self._increment_api_call("ec2", "describe_route_tables")
            for rtb in page.get("RouteTables", []):
                self._ensure_not_cancelled()
                rtb_id = rtb["RouteTableId"]
                rtb_vpc_id = rtb.get("VpcId")
                owner_id = rtb.get("OwnerId", "")
                name_tag = next((t["Value"] for t in rtb.get("Tags", []) if t.get("Key") == "Name"), None)
                is_main = any(a.get("Main", False) for a in rtb.get("Associations", []))
                node_id = self._make_node_id("vpc", f"rtb/{rtb_id}")
                self._node(
                    node_id,
                    label=name_tag or rtb_id,
                    service="vpc",
                    type="route_table",
                    arn=f"arn:aws:ec2:{self._region}:{owner_id}:route-table/{rtb_id}",
                    is_main=is_main,
                )
                if rtb_vpc_id:
                    vpc_node = self._make_node_id("vpc", f"vpc/{rtb_vpc_id}")
                    self.store.add_edge(vpc_node, node_id, relationship="contains", via="vpc_route_table")

                # Subnet associations
                for assoc in rtb.get("Associations", []):
                    assoc_subnet = assoc.get("SubnetId")
                    if assoc_subnet:
                        subnet_node = self._make_node_id("vpc", f"subnet/{assoc_subnet}")
                        self.store.add_edge(node_id, subnet_node, relationship="routes", via="rtb_subnet_association")

                # Route targets (IGW and NAT)
                for route in rtb.get("Routes", []):
                    gw_id = route.get("GatewayId", "")
                    if gw_id.startswith("igw-"):
                        igw_node = self._make_node_id("vpc", f"igw/{gw_id}")
                        self.store.add_edge(igw_node, node_id, relationship="routes_via", via="igw_route")
                    nat_gw_id = route.get("NatGatewayId", "")
                    if nat_gw_id:
                        nat_node = self._make_node_id("vpc", f"nat/{nat_gw_id}")
                        self.store.add_edge(nat_node, node_id, relationship="routes_via", via="nat_route")
