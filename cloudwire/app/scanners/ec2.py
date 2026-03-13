from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

import boto3

from ._utils import _ARN_PATTERN, logger


class EC2ScannerMixin:
    def _scan_ec2(self, session: boto3.session.Session) -> None:
        client = self._client(session, "ec2")
        paginator = client.get_paginator("describe_instances")
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("ec2", "describe_instances")
            for reservation in page.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    self._ensure_not_cancelled()
                    instance_id = instance.get("InstanceId", "")
                    owner_id = instance.get("OwnerId", "")
                    name_tag = next((t["Value"] for t in instance.get("Tags", []) if t.get("Key") == "Name"), None)
                    arn = f"arn:aws:ec2:{self._region}:{owner_id}:instance/{instance_id}"
                    node_id = self._make_node_id("ec2", instance_id)
                    self._node(
                        node_id,
                        label=name_tag or instance_id,
                        service="ec2",
                        type="instance",
                        arn=arn,
                        instance_type=instance.get("InstanceType"),
                        state=instance.get("State", {}).get("Name"),
                        vpc_id=instance.get("VpcId"),
                        subnet_id=instance.get("SubnetId"),
                    )

                    # Phase 2, Item 5: EC2 → VPC / Subnet / Security Group edges
                    vpc_id = instance.get("VpcId")
                    if vpc_id:
                        vpc_node = self._make_node_id("vpc", f"vpc/{vpc_id}")
                        self._node(vpc_node, label=vpc_id, service="vpc", type="vpc",
                                   arn=f"arn:aws:ec2:{self._region}:{owner_id}:vpc/{vpc_id}")
                        self.store.add_edge(vpc_node, node_id, relationship="contains", via="ec2_vpc_membership")

                    subnet_id = instance.get("SubnetId")
                    if subnet_id:
                        subnet_node = self._make_node_id("vpc", f"subnet/{subnet_id}")
                        self._node(subnet_node, label=subnet_id, service="vpc", type="subnet",
                                   arn=f"arn:aws:ec2:{self._region}:{owner_id}:subnet/{subnet_id}")
                        self.store.add_edge(subnet_node, node_id, relationship="contains", via="ec2_subnet_membership")
                        if vpc_id:
                            self.store.add_edge(vpc_node, subnet_node, relationship="contains", via="ec2_vpc_subnet")

                    for sg in instance.get("SecurityGroups", []):
                        sg_id = sg.get("GroupId", "")
                        if sg_id:
                            sg_node = self._make_node_id("vpc", f"sg/{sg_id}")
                            self._node(sg_node, label=sg.get("GroupName", sg_id), service="vpc",
                                       type="security_group",
                                       arn=f"arn:aws:ec2:{self._region}:{owner_id}:security-group/{sg_id}")
                            self.store.add_edge(sg_node, node_id, relationship="protects", via="ec2_security_group")

                    # EC2 → IAM Instance Profile
                    iam_profile = instance.get("IamInstanceProfile", {})
                    profile_arn = iam_profile.get("Arn", "")
                    if profile_arn and _ARN_PATTERN.match(profile_arn):
                        profile_node = self._add_arn_node(profile_arn, label=profile_arn.split("/")[-1],
                                                          node_type="instance_profile")
                        self._node(profile_node, service="iam")
                        self.store.add_edge(profile_node, node_id, relationship="assumed_by",
                                            via="ec2_instance_profile")
