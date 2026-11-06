"""Read-only Zabbix-backed network health for the production Failback gate.

The network gate deliberately consumes the existing Zabbix SNMP availability
items instead of polling physical devices a second time.  It checks only the
four required device health items and ignores unrelated trigger/problem noise
such as a fan warning, an unused interface, or an unsupported OID.

The module is side-effect free apart from read-only JSON-RPC requests.  It
never prints credentials and returns a small, safe snapshot suitable for
ChatOps state and operator-facing messages.
"""

from __future__ import annotations

import json
import shlex
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


READY = "READY"
NOT_READY = "NOT_READY"
UNKNOWN = "UNKNOWN"

DEFAULT_REQUIRED_HOSTS = (
    "Cisco-2800-Series",
    "Catalyst-2950-Series",
    "Catalyst-3560-Series",
    "Catalyst-3550-Series",
)
DEFAULT_AVAILABILITY_ITEM_NAME = "Cisco IOS: SNMP agent availability"
DEFAULT_FRESHNESS_SECONDS = 300


class NetworkHealthError(RuntimeError):
    """Raised for a failed read-only Zabbix network-health query."""


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _csv(value: Any, default: Iterable[str]) -> tuple[str, ...]:
    if isinstance(value, (tuple, list)):
        values = [str(item).strip() for item in value if str(item).strip()]
    else:
        values = [item.strip() for item in str(value or "").split(",") if item.strip()]
    return tuple(values) or tuple(default)


def _safe_device(status: str, *, reason: str = "", value: Any = None, last_clock: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status}
    if reason:
        result["reason"] = reason
    if value is not None:
        result["value"] = str(value)
    if last_clock is not None:
        result["last_clock"] = int(last_clock)
    return result


def _unknown(reason: str, devices: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "status": UNKNOWN,
        "reason": reason,
        "source": "ZABBIX_SNMP_AVAILABILITY",
        "checked_at": _iso_now(),
        "devices": devices or {},
    }


def evaluate_network_snapshot(
    hosts: Iterable[dict[str, Any]],
    items: Iterable[dict[str, Any]],
    *,
    required_hosts: Iterable[str] = DEFAULT_REQUIRED_HOSTS,
    availability_item_name: str = DEFAULT_AVAILABILITY_ITEM_NAME,
    availability_item_key: str = "",
    now: float | None = None,
    freshness_seconds: int = DEFAULT_FRESHNESS_SECONDS,
) -> dict[str, Any]:
    """Classify a previously fetched Zabbix host/item snapshot.

    ``UNKNOWN`` is used for missing, disabled, unsupported, or stale data.
    ``NOT_READY`` is reserved for explicit current availability ``0`` or an
    explicitly unavailable SNMP interface.  This distinction prevents an
    API/data outage from being mistaken for a physical network failure while
    still failing closed for Failback.
    """

    required = tuple(required_hosts)
    host_rows = list(hosts or [])
    item_rows = list(items or [])
    by_name: dict[str, list[dict[str, Any]]] = {}
    for host in host_rows:
        by_name.setdefault(_text(host.get("name")), []).append(host)

    devices: dict[str, dict[str, Any]] = {}
    missing = [name for name in required if len(by_name.get(name, [])) != 1]
    if missing:
        return _unknown("required Zabbix SNMP host missing or ambiguous: " + ", ".join(missing))

    item_by_host: dict[str, list[dict[str, Any]]] = {}
    for item in item_rows:
        host_name = _text(item.get("host_name") or item.get("host"))
        item_by_host.setdefault(host_name, []).append(item)

    current = time.time() if now is None else float(now)
    try:
        freshness = max(1, int(freshness_seconds))
    except (TypeError, ValueError):
        return _unknown("network freshness configuration is invalid")

    for name in required:
        host = by_name[name][0]
        candidates = [
            item
            for item in item_by_host.get(name, [])
            if (
                _text(item.get("name")) == availability_item_name
                or (availability_item_key and _text(item.get("key_")) == availability_item_key)
            )
        ]
        if len(candidates) != 1:
            return _unknown(f"SNMP availability item missing or ambiguous: {name}", devices)
        item = candidates[0]
        last_clock = _text(item.get("lastclock"))
        try:
            last_clock_number = int(last_clock)
        except (TypeError, ValueError):
            return _unknown(f"SNMP availability item has no timestamp: {name}", devices)
        if last_clock_number <= 0 or current - last_clock_number > freshness:
            devices[name] = _safe_device(
                UNKNOWN,
                reason="SNMP availability item is stale",
                value=item.get("lastvalue"),
                last_clock=last_clock_number,
            )
            return _unknown(f"SNMP availability item is stale: {name}", devices)
        if _text(item.get("status", "0")) != "0" or _text(item.get("state", "0")) != "0":
            devices[name] = _safe_device(
                UNKNOWN,
                reason="SNMP availability item is disabled or unsupported",
                value=item.get("lastvalue"),
                last_clock=last_clock_number,
            )
            return _unknown(f"SNMP availability item is not supported: {name}", devices)

        value = _text(item.get("lastvalue")).strip()
        interface_availability = {
            _text(interface.get("available"))
            for interface in host.get("interfaces") or []
            if _text(interface.get("type")) == "2"
        }
        if value == "0" or "2" in interface_availability:
            devices[name] = _safe_device(
                NOT_READY,
                reason="SNMP device is explicitly unavailable",
                value=value,
                last_clock=last_clock_number,
            )
            return {
                "status": NOT_READY,
                "reason": f"required SNMP device is unavailable: {name}",
                "source": "ZABBIX_SNMP_AVAILABILITY",
                "checked_at": _iso_now(),
                "devices": devices,
            }
        if value != "1" or (interface_availability and interface_availability - {"1"}):
            devices[name] = _safe_device(
                UNKNOWN,
                reason="SNMP availability value is indeterminate",
                value=value,
                last_clock=last_clock_number,
            )
            return _unknown(f"SNMP availability is indeterminate: {name}", devices)
        devices[name] = _safe_device(READY, value=value, last_clock=last_clock_number)

    return {
        "status": READY,
        "reason": "all required SNMP availability items are fresh and ready",
        "source": "ZABBIX_SNMP_AVAILABILITY",
        "checked_at": _iso_now(),
        "devices": devices,
    }


def _read_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path:
        return values
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        try:
            parsed = shlex.split(raw_value.strip(), comments=False, posix=True)
        except ValueError:
            continue
        values[key] = parsed[0] if parsed else ""
    return values


def _configured(values: dict[str, str], environ: dict[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = environ.get(name, values.get(name, ""))
        if str(value).strip():
            return str(value).strip()
    return default


class ZabbixNetworkHealthProvider:
    """Small read-only JSON-RPC provider for the existing Zabbix instance."""

    def __init__(
        self,
        *,
        api_url: str,
        api_token: str = "",
        api_user: str = "",
        api_password: str = "",
        required_hosts: Iterable[str] = DEFAULT_REQUIRED_HOSTS,
        availability_item_name: str = DEFAULT_AVAILABILITY_ITEM_NAME,
        availability_item_key: str = "",
        freshness_seconds: int = DEFAULT_FRESHNESS_SECONDS,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ):
        self.api_url = api_url if api_url.rstrip("/").endswith("api_jsonrpc.php") else api_url.rstrip("/") + "/api_jsonrpc.php"
        self.api_token = api_token
        self.api_user = api_user
        self.api_password = api_password
        self.required_hosts = tuple(required_hosts)
        self.availability_item_name = availability_item_name
        self.availability_item_key = availability_item_key
        self.freshness_seconds = freshness_seconds
        self.opener = opener
        self._request_id = 0

    def _call(self, method: str, params: dict[str, Any], auth: str | None) -> Any:
        self._request_id += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params, "id": self._request_id}
        if auth:
            body["auth"] = auth
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json-rpc"},
            method="POST",
        )
        try:
            with self.opener(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            raise NetworkHealthError(f"Zabbix API request failed: {method}") from exc
        if payload.get("error"):
            raise NetworkHealthError(f"Zabbix API rejected: {method}")
        return payload.get("result")

    def snapshot(self) -> dict[str, Any]:
        try:
            auth = self.api_token or None
            self._call("apiinfo.version", {}, None)
            if not auth:
                if not self.api_user or not self.api_password:
                    return _unknown("Zabbix API read-only identity is not configured")
                auth = self._call(
                    "user.login",
                    {"user": self.api_user, "password": self.api_password},
                    None,
                )
            hosts = self._call(
                "host.get",
                {
                    "output": ["hostid", "name", "status"],
                    "filter": {"name": list(self.required_hosts)},
                    "selectInterfaces": ["interfaceid", "type", "available"],
                },
                auth,
            ) or []
            host_ids = [_text(host.get("hostid")) for host in hosts if _text(host.get("hostid"))]
            items = self._call(
                "item.get",
                {
                    "output": [
                        "itemid",
                        "hostid",
                        "name",
                        "key_",
                        "lastvalue",
                        "lastclock",
                        "state",
                        "status",
                    ],
                    "hostids": host_ids,
                    "sortfield": "name",
                },
                auth,
            ) or []
            names_by_id = {_text(host.get("hostid")): _text(host.get("name")) for host in hosts}
            for item in items:
                item["host_name"] = names_by_id.get(_text(item.get("hostid")), "")
            return evaluate_network_snapshot(
                hosts,
                items,
                required_hosts=self.required_hosts,
                availability_item_name=self.availability_item_name,
                availability_item_key=self.availability_item_key,
                freshness_seconds=self.freshness_seconds,
            )
        except NetworkHealthError as exc:
            return _unknown(str(exc))


def build_zabbix_network_provider(
    file_values: dict[str, str] | None = None,
    environ: dict[str, str] | None = None,
) -> Callable[[], dict[str, Any]] | None:
    """Build a provider only when an existing read-only Zabbix identity exists."""

    values = dict(file_values or {})
    env = dict(environ or {})
    external_env = _configured(values, env, "NETWORK_DR_HEALTH_ZABBIX_ENV_FILE")
    if external_env:
        external_values = _read_env_file(external_env)
        for key, value in external_values.items():
            values.setdefault(key, value)
    api_url = _configured(
        values,
        env,
        "NETWORK_DR_HEALTH_ZABBIX_API_URL",
        "ZABBIX_API_URL",
        "ZABBIX_URL",
    )
    token = _configured(values, env, "NETWORK_DR_HEALTH_ZABBIX_API_TOKEN", "ZABBIX_API_TOKEN", "ZABBIX_TOKEN")
    user = _configured(values, env, "NETWORK_DR_HEALTH_ZABBIX_API_USER", "ZABBIX_API_USER", "ZABBIX_USER")
    password = _configured(values, env, "NETWORK_DR_HEALTH_ZABBIX_API_PASSWORD", "ZABBIX_API_PASSWORD", "ZABBIX_PASSWORD")
    if not api_url or not token and not (user and password):
        return None
    required_hosts = _csv(
        _configured(values, env, "NETWORK_DR_HEALTH_REQUIRED_HOSTS"),
        DEFAULT_REQUIRED_HOSTS,
    )
    item_name = _configured(
        values,
        env,
        "NETWORK_DR_HEALTH_ITEM_NAME",
        default=DEFAULT_AVAILABILITY_ITEM_NAME,
    )
    item_key = _configured(values, env, "NETWORK_DR_HEALTH_ITEM_KEY")
    try:
        freshness = int(
            _configured(
                values,
                env,
                "NETWORK_DR_HEALTH_FRESHNESS_SECONDS",
                default=str(DEFAULT_FRESHNESS_SECONDS),
            )
        )
    except ValueError:
        freshness = DEFAULT_FRESHNESS_SECONDS
    provider = ZabbixNetworkHealthProvider(
        api_url=api_url,
        api_token=token,
        api_user=user,
        api_password=password,
        required_hosts=required_hosts,
        availability_item_name=item_name,
        availability_item_key=item_key,
        freshness_seconds=freshness,
    )
    return provider.snapshot
