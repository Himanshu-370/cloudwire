from __future__ import annotations

from typing import Any, Dict, List, Optional

import boto3

from ._utils import logger


class KinesisScannerMixin:

    def _scan_kinesis(self, session: boto3.session.Session) -> None:
        client = self._client(session, "kinesis")
        stream_names: List[str] = []
        next_token: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {}
            if next_token:
                kwargs["NextToken"] = next_token
            self._increment_api_call("kinesis", "list_streams")
            page = client.list_streams(Limit=100, **kwargs)
            stream_names.extend(page.get("StreamNames", []))
            next_token = page.get("NextToken")
            if not next_token:
                break

        for name in stream_names:
            self._ensure_not_cancelled()
            arn = f"arn:aws:kinesis:{self._region}:{self._account_id}:stream/{name}"
            node_id = self._make_node_id("kinesis", name)
            self._node(node_id, label=name, service="kinesis", type="stream", arn=arn)
