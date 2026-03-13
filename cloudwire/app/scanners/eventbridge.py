from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from ._utils import logger


class EventBridgeScannerMixin:
    def _scan_eventbridge(self, session: "boto3.session.Session") -> None:
        client = self._client(session, "events")
        paginator = client.get_paginator("list_rules")
        rules: List[Dict[str, Any]] = []
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("eventbridge", "list_rules")
            rules.extend(page.get("Rules", []))

        for rule in rules:
            self._ensure_not_cancelled()
            rule_arn = rule.get("Arn") or f"rule:{rule.get('Name')}"
            rule_node = self._add_arn_node(rule_arn, label=rule.get("Name"), node_type="rule")
            self._node(
                rule_node,
                service="eventbridge",
                event_pattern=rule.get("EventPattern"),
                state=rule.get("State"),
                schedule_expression=rule.get("ScheduleExpression"),
            )

        workers = max(1, min(self.options.eventbridge_target_workers, len(rules) or 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._fetch_eventbridge_targets, client, rule): rule for rule in rules}
            self._drain_futures(futures, self._apply_eventbridge_targets)

    def _fetch_eventbridge_targets(self, client: Any, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
        targets: List[Dict[str, Any]] = []
        next_token: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {"Rule": rule["Name"]}
            if rule.get("EventBusName"):
                kwargs["EventBusName"] = rule["EventBusName"]
            if next_token:
                kwargs["NextToken"] = next_token
            self._increment_api_call("eventbridge", "list_targets_by_rule")
            page = client.list_targets_by_rule(**kwargs)
            targets.extend(page.get("Targets", []))
            next_token = page.get("NextToken")
            if not next_token:
                break
        return targets

    def _apply_eventbridge_targets(self, future: Future[Any], rule: Dict[str, Any]) -> None:
        try:
            targets = future.result()
        except Exception as exc:
            logger.debug("Failed to fetch EventBridge targets: %s", exc)
            return
        self._ensure_not_cancelled()
        rule_arn = rule.get("Arn") or f"rule:{rule.get('Name')}"
        # Mirror the same node ID construction used in _scan_eventbridge
        rule_node = self._add_arn_node(rule_arn, label=rule.get("Name"), node_type="rule")
        for target in targets:
            self._ensure_not_cancelled()
            target_arn = target.get("Arn")
            if not target_arn:
                continue
            target_node = self._add_arn_node(target_arn)
            self.store.add_edge(
                rule_node,
                target_node,
                relationship="triggers",
                via="eventbridge_rule_target",
                target_id=target.get("Id"),
            )
