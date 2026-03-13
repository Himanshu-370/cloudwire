from __future__ import annotations

from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ._utils import logger


class SNSScannerMixin:

    def _scan_sns(self, session: boto3.session.Session) -> None:
        client = self._client(session, "sns")
        topic_nodes: Dict[str, str] = {}  # topic_arn -> node_id

        paginator = client.get_paginator("list_topics")
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("sns", "list_topics")
            for topic in page.get("Topics", []):
                self._ensure_not_cancelled()
                arn = topic.get("TopicArn", "")
                topic_name = arn.split(":")[-1]
                node_id = self._add_arn_node(arn, label=topic_name, node_type="topic")
                self._node(node_id, service="sns")
                topic_nodes[arn] = node_id

        # SNS → Lambda / SQS / SNS (subscriptions)
        try:
            sub_paginator = client.get_paginator("list_subscriptions")
            for page in sub_paginator.paginate():
                self._ensure_not_cancelled()
                self._increment_api_call("sns", "list_subscriptions")
                for sub in page.get("Subscriptions", []):
                    self._ensure_not_cancelled()
                    topic_arn = sub.get("TopicArn", "")
                    endpoint = sub.get("Endpoint", "")
                    protocol = sub.get("Protocol", "")
                    # Skip pending confirmations and non-ARN endpoints (email, http, sms)
                    if not topic_arn or not endpoint.startswith("arn:aws:"):
                        continue
                    topic_node = topic_nodes.get(topic_arn) or self._add_arn_node(
                        topic_arn, node_type="topic"
                    )
                    target_node = self._add_arn_node(endpoint)
                    self.store.add_edge(
                        topic_node,
                        target_node,
                        relationship="notifies",
                        via="sns_subscription",
                        protocol=protocol,
                    )
        except (ClientError, BotoCoreError) as exc:
            logger.debug("SNS subscription scan skipped: %s", exc)
