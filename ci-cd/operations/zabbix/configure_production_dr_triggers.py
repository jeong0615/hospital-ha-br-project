#!/usr/bin/env python3
"""Reconcile the production Zabbix DR escalation trigger contract.

The tool owns only two source-backed HTTP web scenarios, one DB TCP simple
check, and one named composite trigger with its path-suppression dependencies.
The retired persistent ChatOps test host is deliberately not part of this
reconciler.  It never deletes Zabbix objects and never sends Slack messages.
``--check`` is read-only; ``--apply`` writes only the planned API changes and
verifies them with a fresh readback.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "operations" / "zabbix"))

from configure_chatops import (  # noqa: E402
    ConfigurationError,
    ZabbixClient,
    _copy,
    _read_env_file,
    _redact,
    _text,
    _write_json_atomic,
    load_settings,
)
from production_dr_policy import (  # noqa: E402
    DB_HEALTH_ITEM_KEY,
    DOC_FAIL_ITEM_KEY,
    DOC_SCENARIO,
    FAILOVER_STABILITY_SECONDS,
    PRODUCTION_TRIGGER_DESCRIPTION,
    PRODUCTION_TRIGGER_TAGS,
    WEB_FAIL_ITEM_KEY,
    WEB_SCENARIO,
    ZABBIX_SERVER_HOST,
    failover_expression,
    production_trigger_tags,
)


TARGET_HOSTS = ("web_server", "DB_Server", "Doc_Server")
AGENT_UNAVAILABLE_DESCRIPTION = "Linux: Zabbix agent is not available"
DB_ITEM_NAME = "Hospital DR DB Core TCP health"
AGGREGATE_PRIORITY = "4"  # High; Action 3 still requires the explicit tag.


def _one(rows: list[dict[str, Any]], field: str, value: str, label: str) -> dict[str, Any] | None:
    matches = [row for row in rows if _text(row.get(field)) == value]
    if len(matches) > 1:
        raise ConfigurationError(f"Multiple {label} objects match {field}={value!r}")
    return matches[0] if matches else None


def _tag_pairs(tags: list[dict[str, Any]] | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((_text(tag.get("tag")), _text(tag.get("value"))) for tag in tags or []))


def _has_tag(tags: list[dict[str, Any]] | None, name: str, value: str) -> bool:
    return (name, value) in _tag_pairs(tags)


def _scenario_payload(hostid: str, spec: dict[str, Any]) -> dict[str, Any]:
    payload = _copy(spec)
    payload["hostid"] = hostid
    return payload


def _scenario_signature(scenario: dict[str, Any]) -> tuple[Any, ...]:
    steps = []
    for step in scenario.get("steps") or []:
        steps.append(
            (
                _text(step.get("name")),
                _text(step.get("url")),
                _text(step.get("status_codes")),
                _text(step.get("no")),
                _text(step.get("timeout")),
                _text(step.get("follow_redirects")),
            )
        )
    return (
        _text(scenario.get("hostid")),
        _text(scenario.get("name")),
        _text(scenario.get("delay")),
        _text(scenario.get("attempts")),
        tuple(steps),
        _tag_pairs(scenario.get("tags")),
    )


def _expected_scenario_signature(hostid: str, spec: dict[str, Any]) -> tuple[Any, ...]:
    return _scenario_signature(_scenario_payload(hostid, spec))


def _item_signature(item: dict[str, Any]) -> tuple[str, ...]:
    return (
        _text(item.get("key_")),
        _text(item.get("type")),
        _text(item.get("value_type")),
        _text(item.get("delay")),
        _text(item.get("status")),
    )


def _aggregate_matches_policy(trigger: dict[str, Any], items: list[dict[str, Any]]) -> bool:
    """Compare trigger functions, not Zabbix's generated function IDs.

    Zabbix 6 returns expressions such as ``{functionid}=1``.  The function
    IDs are generated and are not item IDs, so comparing the raw expression
    would report permanent drift.  ``selectFunctions`` exposes the stable
    item/function/parameter relationship used here.
    """

    by_key = {_text(item.get("key_")): item for item in items}
    required = (WEB_FAIL_ITEM_KEY, DOC_FAIL_ITEM_KEY, DB_HEALTH_ITEM_KEY)
    if not all(key in by_key and _text(by_key[key].get("itemid")) for key in required):
        return False
    expected = {
        (_text(by_key[WEB_FAIL_ITEM_KEY]["itemid"]), "min", "$,5m"),
        (_text(by_key[DOC_FAIL_ITEM_KEY]["itemid"]), "min", "$,5m"),
        (_text(by_key[DB_HEALTH_ITEM_KEY]["itemid"]), "max", "$,5m"),
    }
    actual = {
        (_text(row.get("itemid")), _text(row.get("function")), _text(row.get("parameter")))
        for row in trigger.get("functions") or []
    }
    # The function set alone cannot distinguish the retired all-of expression
    # from the production OR policy.  Verify the logical operators as well so
    # --check reports drift instead of accepting the old Web AND EMR AND DB
    # trigger as compliant.
    operators = tuple(re.findall(r"\b(and|or)\b", _text(trigger.get("expression")).lower()))
    return (
        actual == expected
        and operators == ("or", "or")
        and _text(trigger.get("recovery_expression")) == ""
        and _text(trigger.get("priority")) == AGGREGATE_PRIORITY
        and _text(trigger.get("status")) == "0"
        and _tag_pairs(trigger.get("tags")) == _tag_pairs(production_trigger_tags())
    )


def _dependency_ids(trigger: dict[str, Any] | None) -> set[str]:
    return {
        _text(item.get("triggerid"))
        for item in (trigger or {}).get("dependencies") or []
        if _text(item.get("triggerid"))
    }


def _main_agent_interface_id(host: dict[str, Any]) -> str:
    interfaces = [
        item
        for item in host.get("interfaces") or []
        if _text(item.get("type")) == "1" and _text(item.get("main")) == "1"
    ]
    if len(interfaces) != 1:
        raise ConfigurationError(
            f"expected one main Agent interface on {host.get('name', '<unknown>')}"
        )
    return _text(interfaces[0].get("interfaceid"))


def _trigger_signature(trigger: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _text(trigger.get("expression")),
        _text(trigger.get("recovery_expression")),
        _text(trigger.get("priority")),
        _text(trigger.get("status")),
        _tag_pairs(trigger.get("tags")),
    )


@dataclass
class Plan:
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ProductionTriggerConfigurator:
    def __init__(self, api: Any):
        self.api = api

    def _hosts(self) -> dict[str, dict[str, Any]]:
        names = (*TARGET_HOSTS, ZABBIX_SERVER_HOST)
        rows = self.api.call(
            "host.get",
            {
                "output": ["hostid", "host", "name", "status"],
                "filter": {"name": list(names)},
                "selectInterfaces": ["interfaceid", "type", "main"],
            },
        ) or []
        result: dict[str, dict[str, Any]] = {}
        for name in names:
            result[name] = _one(rows, "name", name, "host")
        return result

    def inspect(self) -> dict[str, Any]:
        hosts = self._hosts()
        zabbix = hosts.get(ZABBIX_SERVER_HOST)
        missing = [name for name in (*TARGET_HOSTS, ZABBIX_SERVER_HOST) if hosts.get(name) is None]
        if missing:
            raise ConfigurationError("missing required Zabbix host(s): " + ", ".join(missing))
        zabbix_id = _text(zabbix["hostid"])

        scenarios = self.api.call(
            "httptest.get",
            {
                "output": "extend",
                "hostids": [zabbix_id],
                "selectSteps": "extend",
                "selectTags": "extend",
            },
        ) or []
        scenario_by_name: dict[str, dict[str, Any] | None] = {}
        for name in (WEB_SCENARIO["name"], DOC_SCENARIO["name"]):
            scenario_by_name[name] = _one(scenarios, "name", name, "web scenario")

        items = self.api.call(
            "item.get",
            {
                "output": ["itemid", "hostid", "name", "key_", "type", "value_type", "delay", "status"],
                "hostids": [zabbix_id],
                "webitems": True,
            },
        ) or []

        triggers = self.api.call(
            "trigger.get",
            {
                "output": [
                    "triggerid",
                    "hostid",
                    "description",
                    "expression",
                    "recovery_expression",
                    "priority",
                    "status",
                    "value",
                    "state",
                ],
                "hostids": [zabbix_id],
                "selectTags": "extend",
                "selectDependencies": "extend",
                "selectFunctions": "extend",
            },
        ) or []
        aggregate = _one(triggers, "description", PRODUCTION_TRIGGER_DESCRIPTION, "production trigger")

        agent_triggers: dict[str, dict[str, Any] | None] = {}
        for name in TARGET_HOSTS:
            host = hosts[name]
            rows = self.api.call(
                "trigger.get",
                {
                    "output": ["triggerid", "hostid", "description", "expression", "priority", "status", "value", "state"],
                    "hostids": [_text(host["hostid"])],
                    "filter": {"description": [AGENT_UNAVAILABLE_DESCRIPTION]},
                    "selectTags": "extend",
                    "selectDependencies": "extend",
                },
            ) or []
            agent_triggers[name] = _one(rows, "description", AGENT_UNAVAILABLE_DESCRIPTION, "Agent availability trigger")

        all_escalations = self.api.call(
            "trigger.get",
            {
                "output": ["triggerid", "hostid", "description"],
                "selectTags": "extend",
            },
        ) or []
        return {
            "hosts": hosts,
            "scenarios": scenario_by_name,
            "items": items,
            "aggregate": aggregate,
            "agent_triggers": agent_triggers,
            "all_escalations": [
                row for row in all_escalations if _has_tag(row.get("tags"), "DR_Escalate", "1")
            ],
        }

    def build_plan(self, state: dict[str, Any]) -> Plan:
        plan = Plan()
        hosts = state["hosts"]
        zabbix = hosts[ZABBIX_SERVER_HOST]
        zabbix_id = _text(zabbix["hostid"])

        scenario_specs = (WEB_SCENARIO, DOC_SCENARIO)
        for spec in scenario_specs:
            current = state["scenarios"].get(spec["name"])
            expected = _expected_scenario_signature(zabbix_id, spec)
            if current is None:
                plan.changes.append(f"create web scenario {spec['name']}")
            elif _scenario_signature(current) != expected:
                plan.changes.append(f"update owned web scenario {spec['name']}")

        items = {_text(item.get("key_")): item for item in state["items"]}
        db_item = items.get(DB_HEALTH_ITEM_KEY)
        if db_item is None:
            plan.changes.append(f"create DB health item {DB_HEALTH_ITEM_KEY}")
        elif _item_signature(db_item) != (DB_HEALTH_ITEM_KEY, "3", "3", "60s", "0"):
            if _text(db_item.get("type")) != "3" or _text(db_item.get("value_type")) != "3":
                plan.errors.append(f"DB health key exists with incompatible item type: {DB_HEALTH_ITEM_KEY}")
            else:
                plan.changes.append(f"update owned DB health item {DB_HEALTH_ITEM_KEY}")

        for name in TARGET_HOSTS:
            if state["agent_triggers"].get(name) is None:
                plan.errors.append(f"missing Agent availability trigger on {name}")

        aggregate = state["aggregate"]
        if aggregate is None:
            plan.changes.append(f"create production trigger {PRODUCTION_TRIGGER_DESCRIPTION}")
        elif not _aggregate_matches_policy(aggregate, state["items"]):
            plan.changes.append(f"update production trigger {PRODUCTION_TRIGGER_DESCRIPTION}")

        desired_dependency_ids = {
            _text(state["agent_triggers"][name]["triggerid"])
            for name in TARGET_HOSTS
            if state["agent_triggers"].get(name)
        }
        if aggregate is None:
            if desired_dependency_ids:
                plan.changes.append("add Agent availability dependencies after production trigger creation")
        else:
            missing_dependencies = desired_dependency_ids - _dependency_ids(aggregate)
            for dependency_id in sorted(missing_dependencies):
                plan.changes.append(f"add production trigger dependency on {dependency_id}")
            extra = _dependency_ids(aggregate) - desired_dependency_ids
            if extra:
                plan.warnings.append(
                    "production trigger has additional dependencies; preserving them: " + ",".join(sorted(extra))
                )

        aggregate_id = _text(aggregate.get("triggerid")) if aggregate else ""
        for row in state["all_escalations"]:
            trigger_id = _text(row.get("triggerid"))
            description = _text(row.get("description"))
            if trigger_id == aggregate_id or description == PRODUCTION_TRIGGER_DESCRIPTION:
                continue
            plan.errors.append(
                f"unexpected DR_Escalate=1 trigger {trigger_id} ({description}); remove retired/test object manually before reconciliation"
            )
        return plan

    @staticmethod
    def _scenario_update_payload(hostid: str, current: dict[str, Any] | None, spec: dict[str, Any]) -> dict[str, Any]:
        payload = _scenario_payload(hostid, spec)
        if current is not None:
            payload = {"httptestid": _text(current["httptestid"]), **payload}
        return payload

    @staticmethod
    def _db_item_payload(hostid: str, interfaceid: str, current: dict[str, Any] | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "hostid": hostid,
            "name": DB_ITEM_NAME,
            "key_": DB_HEALTH_ITEM_KEY,
            "type": 3,
            "value_type": 3,
            "interfaceid": interfaceid,
            "delay": "60s",
            "history": "7d",
            "trends": "30d",
            "status": 0,
            "tags": [
                {"tag": "DR_Policy", "value": "production"},
                {"tag": "DR_Scope", "value": "Hospital-Core"},
            ],
        }
        if current is not None:
            return {"itemid": _text(current["itemid"]), **payload}
        return payload

    def _apply_scenarios(self, state: dict[str, Any]) -> None:
        zabbix_id = _text(state["hosts"][ZABBIX_SERVER_HOST]["hostid"])
        for spec in (WEB_SCENARIO, DOC_SCENARIO):
            current = state["scenarios"].get(spec["name"])
            if current is None:
                self.api.call("httptest.create", _scenario_payload(zabbix_id, spec))
            elif _scenario_signature(current) != _expected_scenario_signature(zabbix_id, spec):
                self.api.call("httptest.update", self._scenario_update_payload(zabbix_id, current, spec))

    def _apply_db_item(self, state: dict[str, Any]) -> None:
        zabbix_host = state["hosts"][ZABBIX_SERVER_HOST]
        zabbix_id = _text(zabbix_host["hostid"])
        interfaceid = _main_agent_interface_id(zabbix_host)
        items = {_text(item.get("key_")): item for item in state["items"]}
        current = items.get(DB_HEALTH_ITEM_KEY)
        if current is None:
            self.api.call("item.create", self._db_item_payload(zabbix_id, interfaceid, None))
        elif _item_signature(current) != (DB_HEALTH_ITEM_KEY, "3", "3", "60s", "0"):
            self.api.call("item.update", self._db_item_payload(zabbix_id, interfaceid, current))

    def _apply_aggregate(self, state: dict[str, Any]) -> None:
        aggregate = state["aggregate"]
        required_keys = (WEB_FAIL_ITEM_KEY, DOC_FAIL_ITEM_KEY, DB_HEALTH_ITEM_KEY)
        item_keys = {_text(item.get("key_")) for item in state["items"]}
        if any(key not in item_keys for key in required_keys):
            raise ConfigurationError("Zabbix web/DB health items are not available after provisioning")
        expression = failover_expression(ZABBIX_SERVER_HOST)
        payload = {
            "description": PRODUCTION_TRIGGER_DESCRIPTION,
            "expression": expression,
            "recovery_expression": "",
            "priority": int(AGGREGATE_PRIORITY),
            "status": 0,
            "tags": production_trigger_tags(),
        }
        if aggregate is None:
            self.api.call("trigger.create", payload)
        elif not _aggregate_matches_policy(aggregate, state["items"]):
            self.api.call("trigger.update", {"triggerid": _text(aggregate["triggerid"]), **payload})

    def _apply_dependencies(self, state: dict[str, Any]) -> None:
        aggregate = state["aggregate"]
        if aggregate is None:
            raise ConfigurationError("production trigger was not present after apply")
        aggregate_id = _text(aggregate["triggerid"])
        existing = _dependency_ids(aggregate)
        for name in TARGET_HOSTS:
            dependency = state["agent_triggers"].get(name)
            if dependency is None:
                raise ConfigurationError(f"missing Agent availability trigger on {name}")
            dependency_id = _text(dependency["triggerid"])
            if dependency_id not in existing:
                self.api.call(
                    "trigger.adddependencies",
                    {"triggerid": aggregate_id, "dependsOnTriggerid": dependency_id},
                )

    def apply(self) -> None:
        state = self.inspect()
        self._apply_scenarios(state)
        state = self.inspect()
        self._apply_db_item(state)
        state = self.inspect()
        self._apply_aggregate(state)
        state = self.inspect()
        self._apply_dependencies(state)

    def snapshot(self, state: dict[str, Any], api_version: str) -> dict[str, Any]:
        return {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "zabbix_api_version": api_version,
            "policy": {
                "trigger": PRODUCTION_TRIGGER_DESCRIPTION,
                "stability_seconds": FAILOVER_STABILITY_SECONDS,
                "web_scenario": WEB_SCENARIO,
                "doc_scenario": DOC_SCENARIO,
                "db_item_key": DB_HEALTH_ITEM_KEY,
            },
            "hosts": _redact(state["hosts"]),
            "scenarios": _redact(state["scenarios"]),
            "aggregate": _redact(state["aggregate"]),
            "agent_triggers": _redact(state["agent_triggers"]),
            "escalation_triggers": _redact(state["all_escalations"]),
        }

    def reconcile(self, *, apply: bool = False) -> tuple[Plan, dict[str, Any]]:
        state = self.inspect()
        plan = self.build_plan(state)
        if apply:
            if plan.errors:
                raise ConfigurationError("; ".join(plan.errors))
            if plan.changes:
                self.apply()
            final_state = self.inspect()
            final_plan = self.build_plan(final_state)
            if final_plan.errors or final_plan.changes:
                detail = "; ".join(final_plan.errors + final_plan.changes)
                raise ConfigurationError(f"post-apply verification failed: {detail}")
            return final_plan, final_state
        return plan, state


def _print_plan(plan: Plan) -> None:
    for change in plan.changes:
        print(f"DRIFT: {change}")
    for warning in plan.warnings:
        print(f"WARN: {warning}")
    for error in plan.errors:
        print(f"BLOCKED: {error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="read-only compliance check (default)")
    mode.add_argument("--apply", action="store_true", help="apply only owned Zabbix changes")
    parser.add_argument("--env-file", help="external file containing Zabbix API settings")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("/var/lib/hospital-dr/zabbix-config-backups"),
        help="external directory for the redacted pre-apply snapshot",
    )
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.env_file)
        client = ZabbixClient(settings)
        version = client.authenticate()
        configurator = ProductionTriggerConfigurator(client)
        state = configurator.inspect()
        if args.apply:
            backup = args.backup_dir / (
                "zabbix-production-dr-pre-apply-"
                + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                + ".json"
            )
            _write_json_atomic(backup, configurator.snapshot(state, version))
            print(f"BACKUP PASS: {backup}")
            plan, _ = configurator.reconcile(apply=True)
        else:
            plan, _ = configurator.reconcile(apply=False)
        _print_plan(plan)
        if plan.errors:
            print("APPLY BLOCKED" if args.apply else "CHECK BLOCKED")
            return 1
        if args.apply:
            print("APPLY PASS: production DR trigger policy verified")
            return 0
        if plan.changes:
            print("CHECK DRIFT: no changes were made; rerun with --apply after review")
            return 2
        print("CHECK PASS: production DR trigger policy is present")
        return 0
    except (ConfigurationError, OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
