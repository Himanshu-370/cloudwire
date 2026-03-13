from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Dict

from botocore.exceptions import BotoCoreError, ClientError

from ._utils import logger


class S3ScannerMixin:
    def _scan_s3(self, session: "boto3.session.Session") -> None:
        client = self._client(session, "s3")
        self._increment_api_call("s3", "list_buckets")
        response = client.list_buckets()
        bucket_nodes: Dict[str, str] = {}  # bucket_name -> node_id

        for bucket in response.get("Buckets", []):
            self._ensure_not_cancelled()
            name = bucket.get("Name", "")
            arn = f"arn:aws:s3:::{name}"
            node_id = self._make_node_id("s3", name)
            self._node(
                node_id,
                label=name,
                service="s3",
                type="bucket",
                arn=arn,
                creation_date=str(bucket.get("CreationDate", "")),
            )
            bucket_nodes[name] = node_id

        # S3 → Lambda / SQS / SNS (bucket event notifications)
        if bucket_nodes:
            workers = max(1, min(16, len(bucket_nodes)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._fetch_s3_notifications, client, name): node_id
                    for name, node_id in bucket_nodes.items()
                }
                self._drain_futures(futures, self._apply_s3_notifications)

    def _fetch_s3_notifications(self, client: Any, bucket_name: str) -> Dict[str, Any]:
        try:
            self._increment_api_call("s3", "get_bucket_notification_configuration")
            return client.get_bucket_notification_configuration(Bucket=bucket_name)
        except (ClientError, BotoCoreError) as exc:
            logger.debug("S3 notification fetch failed for %s: %s", bucket_name, exc)
            return {}

    def _apply_s3_notifications(self, future: Future[Any], bucket_node: str) -> None:
        try:
            config = future.result()
        except Exception:
            return
        self._ensure_not_cancelled()
        # Lambda notifications
        for notif in config.get("LambdaFunctionConfigurations", []):
            target_arn = notif.get("LambdaFunctionArn", "")
            if target_arn.startswith("arn:aws:"):
                target_node = self._add_arn_node(target_arn)
                self.store.add_edge(
                    bucket_node, target_node, relationship="triggers", via="s3_notification"
                )
        # SQS notifications
        for notif in config.get("QueueConfigurations", []):
            target_arn = notif.get("QueueArn", "")
            if target_arn.startswith("arn:aws:"):
                target_node = self._add_arn_node(target_arn)
                self.store.add_edge(
                    bucket_node, target_node, relationship="triggers", via="s3_notification"
                )
        # SNS notifications
        for notif in config.get("TopicConfigurations", []):
            target_arn = notif.get("TopicArn", "")
            if target_arn.startswith("arn:aws:"):
                target_node = self._add_arn_node(target_arn)
                self.store.add_edge(
                    bucket_node, target_node, relationship="triggers", via="s3_notification"
                )
