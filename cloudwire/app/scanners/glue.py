from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Set

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ._utils import _safe_list, logger


class GlueScannerMixin:

    def _scan_glue(self, session: boto3.session.Session) -> None:
        client = self._client(session, "glue")
        job_names: List[str] = []

        # Jobs
        next_token: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {}
            if next_token:
                kwargs["NextToken"] = next_token
            self._increment_api_call("glue", "list_jobs")
            page = client.list_jobs(**kwargs)
            for job_name in page.get("JobNames", []):
                self._ensure_not_cancelled()
                arn = f"arn:aws:glue:{self._region}:{self._account_id}:job/{job_name}"
                node_id = self._make_node_id("glue", job_name)
                self._node(node_id, label=job_name, service="glue", type="job", arn=arn)
                job_names.append(job_name)
            next_token = page.get("NextToken")
            if not next_token:
                break

        # Glue → S3 (source/target buckets from job arguments) and Glue → RDS (connections)
        if job_names:
            workers = max(1, min(8, len(job_names)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._fetch_glue_job_detail, client, name): name
                    for name in job_names
                }
                self._drain_futures(futures, self._apply_glue_job_edges)

        # Phase 3, Item 11: Glue crawlers and triggers
        self._scan_glue_crawlers(client)
        self._scan_glue_triggers(client)

    def _fetch_glue_job_detail(self, client: Any, job_name: str) -> Dict[str, Any]:
        try:
            self._increment_api_call("glue", "get_job")
            return client.get_job(JobName=job_name).get("Job", {})
        except (ClientError, BotoCoreError) as exc:
            logger.debug("Glue get_job failed for %s: %s", job_name, exc)
            return {}

    def _apply_glue_job_edges(self, future: Future[Any], job_name: str) -> None:
        try:
            job = future.result()
        except Exception:
            return
        self._ensure_not_cancelled()
        job_node = self._make_node_id("glue", job_name)

        # Extract S3 bucket references from job arguments
        args = job.get("DefaultArguments") or {}
        s3_buckets: Set[str] = set()
        for val in args.values():
            if isinstance(val, str) and val.startswith("s3://"):
                # s3://bucket-name/path/... → extract bucket-name
                parts = val[5:].split("/")
                if parts[0]:
                    s3_buckets.add(parts[0])
        for bucket_name in s3_buckets:
            s3_node = self._make_node_id("s3", bucket_name)
            self._node(s3_node, label=bucket_name, service="s3", type="bucket",
                        arn=f"arn:aws:s3:::{bucket_name}")
            self.store.add_edge(job_node, s3_node, relationship="reads_writes", via="glue_job_argument")

        # Glue connections (JDBC → RDS/Redshift)
        for conn_name in _safe_list(job.get("Connections", {}).get("Connections")):
            conn_node = self._make_node_id("glue", f"connection:{conn_name}")
            self._node(conn_node, label=conn_name, service="glue", type="connection",
                        arn=f"arn:aws:glue:{self._region}:{self._account_id}:connection/{conn_name}")
            self.store.add_edge(job_node, conn_node, relationship="uses", via="glue_connection")

    def _scan_glue_crawlers(self, client: Any) -> None:
        """Discover Glue crawlers and their S3/DynamoDB/database targets (Phase 3, Item 11)."""
        next_token: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {}
            if next_token:
                kwargs["NextToken"] = next_token
            try:
                self._increment_api_call("glue", "get_crawlers")
                page = client.get_crawlers(**kwargs)
            except (ClientError, BotoCoreError) as exc:
                logger.debug("Glue get_crawlers failed: %s", exc)
                self.store.add_warning(f"glue crawler scan failed: {type(exc).__name__}")
                return

            for crawler in page.get("Crawlers", []):
                self._ensure_not_cancelled()
                name = crawler.get("Name", "")
                if not name:
                    continue
                arn = f"arn:aws:glue:{self._region}:{self._account_id}:crawler/{name}"
                node_id = self._make_node_id("glue", f"crawler:{name}")
                self._node(node_id, label=name, service="glue", type="crawler", arn=arn,
                           state=crawler.get("State"))

                # Crawler → S3 targets
                for target in (crawler.get("Targets") or {}).get("S3Targets", []):
                    path = target.get("Path", "")
                    if path.startswith("s3://"):
                        bucket = path[5:].split("/")[0]
                        if bucket:
                            s3_node = self._make_node_id("s3", bucket)
                            self._node(s3_node, label=bucket, service="s3", type="bucket",
                                       arn=f"arn:aws:s3:::{bucket}")
                            self.store.add_edge(node_id, s3_node, relationship="crawls",
                                                via="glue_crawler_target")

                # Crawler → DynamoDB targets
                for target in (crawler.get("Targets") or {}).get("DynamoDBTargets", []):
                    table = target.get("Path", "")
                    if table:
                        ddb_node = self._make_node_id("dynamodb", table)
                        self._node(ddb_node, label=table, service="dynamodb", type="table")
                        self.store.add_edge(node_id, ddb_node, relationship="crawls",
                                            via="glue_crawler_target")

                # Crawler → output database
                db_name = crawler.get("DatabaseName", "")
                if db_name:
                    db_node = self._make_node_id("glue", f"database:{db_name}")
                    self._node(db_node, label=db_name, service="glue", type="database",
                               arn=f"arn:aws:glue:{self._region}:{self._account_id}:database/{db_name}")
                    self.store.add_edge(node_id, db_node, relationship="populates",
                                        via="glue_crawler_output")

            next_token = page.get("NextToken")
            if not next_token:
                break

    def _scan_glue_triggers(self, client: Any) -> None:
        """Discover Glue triggers and their job/crawler action edges (Phase 3, Item 11)."""
        next_token: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {}
            if next_token:
                kwargs["NextToken"] = next_token
            try:
                self._increment_api_call("glue", "get_triggers")
                page = client.get_triggers(**kwargs)
            except (ClientError, BotoCoreError) as exc:
                logger.debug("Glue get_triggers failed: %s", exc)
                self.store.add_warning(f"glue trigger scan failed: {type(exc).__name__}")
                return

            for trigger in page.get("Triggers", []):
                self._ensure_not_cancelled()
                name = trigger.get("Name", "")
                if not name:
                    continue
                node_id = self._make_node_id("glue", f"trigger:{name}")
                self._node(node_id, label=name, service="glue", type="trigger",
                           arn=f"arn:aws:glue:{self._region}:{self._account_id}:trigger/{name}",
                           trigger_type=trigger.get("Type"), state=trigger.get("State"))

                # Trigger → job/crawler actions
                for action in trigger.get("Actions", []):
                    job_name = action.get("JobName", "")
                    if job_name:
                        job_node = self._make_node_id("glue", job_name)
                        self.store.add_edge(node_id, job_node, relationship="triggers",
                                            via="glue_trigger")
                    crawler_name = action.get("CrawlerName", "")
                    if crawler_name:
                        crawler_node = self._make_node_id("glue", f"crawler:{crawler_name}")
                        self.store.add_edge(node_id, crawler_node, relationship="triggers",
                                            via="glue_trigger")

                # Predicate conditions: job completion → trigger
                for condition in (trigger.get("Predicate") or {}).get("Conditions", []):
                    pred_job = condition.get("JobName", "")
                    if pred_job:
                        pred_node = self._make_node_id("glue", pred_job)
                        self.store.add_edge(pred_node, node_id, relationship="triggers",
                                            via="glue_trigger_predicate")

            next_token = page.get("NextToken")
            if not next_token:
                break
