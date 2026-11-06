"""Pure production DR escalation policy and Zabbix object contract.

This module deliberately contains no Zabbix API or Slack side effects.  A
production Failover candidate is raised when any one of the independently
measured hospital-core service probes fails for the sustained window, and
must not be caused by a monitoring-path failure, maintenance, or test mode.
"""

from __future__ import annotations

from typing import Any


FAILOVER_STABILITY_SECONDS = 300
ZABBIX_SERVER_HOST = "Zabbix server"
ONPREM_WEB_IP = "192.168.200.3"
ONPREM_DB_IP = "192.168.200.4"
ONPREM_EMR_IP = "192.168.200.5"
WEB_SCENARIO_NAME = "Hospital DR Web Core"
DOC_SCENARIO_NAME = "Hospital DR Doc Core"
WEB_FAIL_ITEM_KEY = f"web.test.fail[{WEB_SCENARIO_NAME}]"
DOC_FAIL_ITEM_KEY = f"web.test.fail[{DOC_SCENARIO_NAME}]"
DB_HEALTH_ITEM_KEY = f"net.tcp.service[tcp,{ONPREM_DB_IP},3306]"
PRODUCTION_TRIGGER_DESCRIPTION = "Hospital DR: On-Prem core services sustained failure"
PRODUCTION_TRIGGER_TAGS = (
    ("DR_Escalate", "1"),
    ("DR_Policy", "production"),
    ("DR_Scope", "Hospital-Core"),
    ("DR_Type", "Failover"),
)


def web_scenario_spec(name: str, step_name: str, url: str) -> dict[str, Any]:
    """Return the source-backed HTTP health scenario contract."""

    return {
        "name": name,
        "delay": "1m",
        "steps": [
            {
                "name": step_name,
                "url": url,
                "status_codes": "200",
                "no": 1,
                "timeout": "5s",
                "follow_redirects": 1,
            }
        ],
        "tags": [
            {"tag": "DR_Policy", "value": "production"},
            {"tag": "DR_Scope", "value": "Hospital-Core"},
        ],
    }


WEB_SCENARIO = web_scenario_spec(
    WEB_SCENARIO_NAME,
    "Web /health",
    f"http://{ONPREM_WEB_IP}/health",
)
DOC_SCENARIO = web_scenario_spec(
    DOC_SCENARIO_NAME,
    "Doc /api/patients",
    f"http://{ONPREM_EMR_IP}/api/patients",
)


def failover_expression(host: str = ZABBIX_SERVER_HOST) -> str:
    """Return the 5-minute per-service failure expression.

    ``web.test.fail`` is 1 on a failed web scenario and the DB TCP simple
    check is 0 on failure.  The failed web scenarios use ``min(...,5m)=1``;
    the DB check uses ``max(...,5m)=0`` because the latter is the form that
    requires every sample in the window to remain failed.  The branches are
    joined with OR, so one sustained core-service failure is sufficient.
    Zabbix keeps the trigger in an unknown/not-ready state until the relevant
    time window has enough data.
    """

    return (
        f"(min(/{host}/{WEB_FAIL_ITEM_KEY},5m)=1 or "
        f"min(/{host}/{DOC_FAIL_ITEM_KEY},5m)=1 or "
        f"max(/{host}/{DB_HEALTH_ITEM_KEY},5m)=0)"
    )


def production_trigger_tags() -> list[dict[str, str]]:
    return [{"tag": tag, "value": value} for tag, value in PRODUCTION_TRIGGER_TAGS]


def evaluate_failover_policy(
    *,
    web_failed: bool,
    doc_failed: bool,
    db_failed: bool,
    duration_seconds: float,
    monitoring_path_failure: bool,
    maintenance: bool,
    broad_test_mode: bool,
    test_marker: bool = False,
) -> bool:
    """Evaluate the production policy without needing a live Zabbix server."""

    return bool(
        (web_failed or doc_failed or db_failed)
        and duration_seconds >= FAILOVER_STABILITY_SECONDS
        and not monitoring_path_failure
        and not maintenance
        and not broad_test_mode
        and not test_marker
    )


def policy_matrix() -> list[dict[str, str]]:
    """Return the concise operator-facing production policy matrix."""

    return [
        {"situation": "Server reboot", "general_alert": "O", "failover": "X", "failback": "X"},
        {"situation": "Agent disconnect", "general_alert": "O", "failover": "X", "failback": "X"},
        {"situation": "WireGuard/monitoring path disconnect", "general_alert": "O", "failover": "X", "failback": "X"},
        {"situation": "CPU/RAM/Disk warning", "general_alert": "O", "failover": "X", "failback": "X"},
        {"situation": "Temporary service outage", "general_alert": "O", "failover": "X", "failback": "X"},
        {"situation": "Individual Web core failure for >=5m, path healthy", "general_alert": "O", "failover": "O", "failback": "X"},
        {"situation": "Individual EMR/Doc core failure for >=5m, path healthy", "general_alert": "O", "failover": "O", "failback": "X"},
        {"situation": "Individual DB core TCP failure for >=5m, path healthy", "general_alert": "O", "failover": "O", "failback": "X"},
        {"situation": "Multiple core-service failures for >=5m, path healthy", "general_alert": "O", "failover": "O", "failback": "X"},
        {"situation": "Core failure with monitoring path failure", "general_alert": "O", "failover": "X", "failback": "X"},
        {"situation": "Failover execution complete", "general_alert": "O", "failover": "-", "failback": "X"},
        {"situation": "Temporary On-Prem recovery", "general_alert": "O", "failover": "X", "failback": "X"},
        {"situation": "DR_ACTIVE + core and required Network/SNMP health stable for >=5m", "general_alert": "O", "failover": "X", "failback": "O"},
        {"situation": "Stale Recovery", "general_alert": "O", "failover": "X", "failback": "X"},
        {"situation": "SNMP device failure", "general_alert": "O", "failover": "X", "failback": "X"},
    ]
