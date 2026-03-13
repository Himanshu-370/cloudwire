from __future__ import annotations

from typing import Any, Dict, List, Optional

import boto3

from ._utils import logger


class IAMScannerMixin:

    def _scan_iam(self, session: boto3.session.Session) -> None:
        # Use us-east-1 since IAM is a global service
        client = session.client("iam", config=self._client_config)
        paginator = client.get_paginator("list_roles")
        count = 0
        for page in paginator.paginate(MaxItems=200):
            self._ensure_not_cancelled()
            self._increment_api_call("iam", "list_roles")
            for role in page.get("Roles", []):
                self._ensure_not_cancelled()
                arn = role.get("Arn", "")
                node_id = self._add_arn_node(arn, label=role.get("RoleName"), node_type="role")
                self._node(node_id, service="iam", created=str(role.get("CreateDate", "")))
                count += 1
            if count >= 200:
                self.store.add_warning("IAM: showing first 200 roles only.")
                return
