from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ._utils import logger


class CognitoScannerMixin:

    _COGNITO_LAMBDA_TRIGGERS = [
        "PreSignUp", "CustomMessage", "PostConfirmation", "PreAuthentication",
        "PostAuthentication", "DefineAuthChallenge", "CreateAuthChallenge",
        "VerifyAuthChallengeResponse", "PreTokenGeneration", "UserMigration",
        "CustomSMSSender", "CustomEmailSender",
    ]

    def _scan_cognito(self, session: boto3.session.Session) -> None:
        client = self._client(session, "cognito-idp")
        pool_nodes: List[tuple[str, str]] = []  # (pool_id, node_id)
        next_token: Optional[str] = None

        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {"MaxResults": 60}
            if next_token:
                kwargs["NextToken"] = next_token
            self._increment_api_call("cognito", "list_user_pools")
            page = client.list_user_pools(**kwargs)
            for pool in page.get("UserPools", []):
                self._ensure_not_cancelled()
                pool_id = pool.get("Id", "")
                arn = f"arn:aws:cognito-idp:{self._region}:{self._account_id}:userpool/{pool_id}"
                node_id = self._make_node_id("cognito", pool_id)
                self._node(node_id, label=pool.get("Name", pool_id), service="cognito", type="user_pool", arn=arn)
                pool_nodes.append((pool_id, node_id))
            next_token = page.get("NextToken")
            if not next_token:
                break

        # Cognito → Lambda (pre/post hooks)
        if pool_nodes:
            workers = max(1, min(8, len(pool_nodes)))
            with ThreadPoolExecutor(max_workers=workers) as pool_executor:
                futures = {
                    pool_executor.submit(self._fetch_cognito_lambda_config, client, pool_id): node_id
                    for pool_id, node_id in pool_nodes
                }
                self._drain_futures(futures, self._apply_cognito_lambda_edges)

    def _fetch_cognito_lambda_config(self, client: Any, pool_id: str) -> Dict[str, Any]:
        try:
            self._increment_api_call("cognito", "describe_user_pool")
            return client.describe_user_pool(UserPoolId=pool_id).get("UserPool", {}).get("LambdaConfig", {})
        except (ClientError, BotoCoreError) as exc:
            logger.debug("Cognito describe_user_pool failed for %s: %s", pool_id, exc)
            return {}

    def _apply_cognito_lambda_edges(self, future: Future[Any], pool_node: str) -> None:
        try:
            lambda_config = future.result()
        except Exception:
            return
        self._ensure_not_cancelled()
        for trigger in self._COGNITO_LAMBDA_TRIGGERS:
            fn_arn = lambda_config.get(trigger, "")
            if fn_arn and fn_arn.startswith("arn:aws:lambda:"):
                target_node = self._add_arn_node(fn_arn)
                self.store.add_edge(
                    pool_node, target_node, relationship="triggers", via=f"cognito_{trigger.lower()}"
                )
