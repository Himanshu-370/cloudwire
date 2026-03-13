from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ._utils import logger


class AppSyncScannerMixin:

    def _scan_appsync(self, session: boto3.session.Session) -> None:
        client = self._client(session, "appsync")
        api_ids: List[tuple[str, str]] = []  # (api_id, node_id)
        next_token: Optional[str] = None

        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {}
            if next_token:
                kwargs["nextToken"] = next_token
            self._increment_api_call("appsync", "list_graphql_apis")
            page = client.list_graphql_apis(**kwargs)
            for api in page.get("graphqlApis", []):
                self._ensure_not_cancelled()
                arn = api.get("arn", "")
                api_id = api.get("apiId", "")
                node_id = self._add_arn_node(arn, label=api.get("name"), node_type="api")
                self._node(node_id, service="appsync", auth_type=api.get("authenticationType"))
                api_ids.append((api_id, node_id))
            next_token = page.get("nextToken")
            if not next_token:
                break

        # AppSync → Lambda / DynamoDB / RDS (data sources)
        if api_ids:
            workers = max(1, min(8, len(api_ids)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._fetch_appsync_data_sources, client, api_id): node_id
                    for api_id, node_id in api_ids
                }
                self._drain_futures(futures, self._apply_appsync_edges)

    def _fetch_appsync_data_sources(self, client: Any, api_id: str) -> List[Dict[str, Any]]:
        sources: List[Dict[str, Any]] = []
        next_token: Optional[str] = None
        while True:
            try:
                self._ensure_not_cancelled()
                kwargs: Dict[str, Any] = {"apiId": api_id}
                if next_token:
                    kwargs["nextToken"] = next_token
                self._increment_api_call("appsync", "list_data_sources")
                page = client.list_data_sources(**kwargs)
                sources.extend(page.get("dataSources", []))
                next_token = page.get("nextToken")
                if not next_token:
                    break
            except (ClientError, BotoCoreError) as exc:
                logger.debug("AppSync list_data_sources failed for %s: %s", api_id, exc)
                break
        return sources

    def _apply_appsync_edges(self, future: Future[Any], api_node: str) -> None:
        try:
            sources = future.result()
        except Exception:
            return
        self._ensure_not_cancelled()
        for source in sources:
            src_type = source.get("type", "")
            if src_type == "AWS_LAMBDA":
                fn_arn = (source.get("lambdaConfig") or {}).get("lambdaFunctionArn", "")
                if fn_arn.startswith("arn:aws:lambda:"):
                    target = self._add_arn_node(fn_arn)
                    self.store.add_edge(api_node, target, relationship="resolves_via", via="appsync_datasource")
            elif src_type == "AMAZON_DYNAMODB":
                table_name = (source.get("dynamodbConfig") or {}).get("tableName", "")
                if table_name:
                    node_id = self._make_node_id("dynamodb", table_name)
                    self._node(node_id, label=table_name, service="dynamodb", type="table", arn=table_name)
                    self.store.add_edge(api_node, node_id, relationship="resolves_via", via="appsync_datasource")
            elif src_type == "RELATIONAL_DATABASE":
                db_cluster_id = (source.get("relationalDatabaseConfig") or {}).get(
                    "rdsHttpEndpointConfig", {}
                ).get("dbClusterIdentifier", "")
                if db_cluster_id:
                    node_id = self._make_node_id("rds", db_cluster_id)
                    self._node(node_id, label=db_cluster_id, service="rds", type="cluster", arn=db_cluster_id)
                    self.store.add_edge(api_node, node_id, relationship="resolves_via", via="appsync_datasource")
