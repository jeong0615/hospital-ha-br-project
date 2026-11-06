#!/usr/bin/env python3
"""Read-only validation of provisioned Grafana panel queries.

The checker calls Grafana's query API with the same target definitions stored
in the dashboard JSON.  It never writes dashboards, datasources, metrics, or
state.  Credentials are read from the external Grafana environment file and
are never printed.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
from typing import Any

import requests


DEFAULT_DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "monitoring/grafana/provisioning/dashboards/json"


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _numeric_values(frame: dict[str, Any]) -> list[float]:
    values = (frame.get("data") or {}).get("values") or []
    if len(values) < 2:
        return []
    result: list[float] = []
    for value in values[1]:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result.append(float(value))
    return result


def _frame_row_count(frame: dict[str, Any]) -> int:
    values = (frame.get("data") or {}).get("values") or []
    return max((len(column) for column in values if isinstance(column, list)), default=0)


def _string_field_count(frame: dict[str, Any]) -> int:
    fields = (frame.get("schema") or {}).get("fields") or []
    return sum(field.get("type") == "string" for field in fields if isinstance(field, dict))


def _raw_data_refs(panel: dict[str, Any]) -> set[str]:
    return {
        str(target.get("refId"))
        for target in panel.get("targets", [])
        if any(metric.get("type") == "raw_data" for metric in target.get("metrics", []) if isinstance(metric, dict))
    }


def _query_panel(session: requests.Session, url: str, panel: dict[str, Any], time_from: str, time_to: str) -> dict[str, Any]:
    targets = []
    for target in panel.get("targets", []):
        query = copy.deepcopy(target)
        if query.get("datasource") is None and panel.get("datasource"):
            query["datasource"] = panel["datasource"]
        targets.append(query)
    response = session.post(
        f"{url.rstrip('/')}/api/ds/query",
        json={"from": time_from, "to": time_to, "queries": targets},
        timeout=45,
    )
    response.raise_for_status()
    return response.json()


def check_dashboard(session: requests.Session, url: str, path: Path, time_from: str | None, time_to: str) -> list[dict[str, Any]]:
    dashboard = json.loads(path.read_text(encoding="utf-8"))
    selected_from = time_from or dashboard.get("time", {}).get("from", "now-24h")
    results: list[dict[str, Any]] = []
    for panel in dashboard.get("panels", []):
        title = panel.get("title", f"panel-{panel.get('id')}")
        if panel.get("type") == "text":
            content = panel.get("options", {}).get("content", "").splitlines()
            results.append({"title": title, "status": "PASS", "kind": "explicit-state", "detail": content[0] if content else "TEXT"})
            continue
        if not panel.get("targets") and panel.get("fieldConfig", {}).get("defaults", {}).get("noValue"):
            results.append(
                {
                    "title": title,
                    "status": "PASS",
                    "kind": "explicit-state",
                    "detail": f"explicit-state={panel['fieldConfig']['defaults']['noValue']}",
                }
            )
            continue
        if panel.get("targets") and all(target.get("hide") for target in panel.get("targets", [])):
            results.append({"title": title, "status": "SKIP", "kind": "hidden-query", "detail": "hidden"})
            continue
        try:
            payload = _query_panel(session, url, panel, selected_from, time_to)
            frames = []
            raw_frames = []
            errors = []
            raw_refs = _raw_data_refs(panel)
            for ref_id, result in (payload.get("results") or {}).items():
                if result.get("error"):
                    errors.append("query-error")
                result_frames = result.get("frames") or []
                if ref_id in raw_refs:
                    raw_frames.extend(result_frames)
                else:
                    frames.extend(result_frames)
            numeric = [value for frame in frames for value in _numeric_values(frame)]
            explicit_empty = panel.get("fieldConfig", {}).get("defaults", {}).get("noValue")
            raw_rows = sum(_frame_row_count(frame) for frame in raw_frames)
            malformed_raw_frames = sum(
                _frame_row_count(frame) > 0 and _string_field_count(frame) == 0
                for frame in raw_frames
            )
            raw_string_fields = sum(_string_field_count(frame) for frame in raw_frames)
            if errors:
                status = "FAIL"
                detail = "query-error"
            elif raw_refs and malformed_raw_frames:
                status = "FAIL"
                detail = "raw-data-frame-missing-string-fields"
            elif raw_refs and raw_rows:
                status = "PASS"
                detail = f"raw_frames={len(raw_frames)} rows={raw_rows} string_fields={raw_string_fields}"
            elif raw_refs and explicit_empty:
                status = "PASS"
                detail = f"raw_rows=0 explicit-state={explicit_empty}"
            elif raw_refs:
                status = "NO_DATA"
                detail = "raw-data query succeeded with zero rows in the selected range; collection freshness is unverified"
            elif frames and numeric:
                status = "PASS"
                if panel.get("datasource", {}).get("type") == "elasticsearch":
                    detail = f"frames={len(frames)} sum={sum(numeric):g} last={numeric[-1]:g}"
                else:
                    detail = f"frames={len(frames)} last={numeric[-1]:g}"
            elif explicit_empty:
                status = "PASS"
                detail = f"explicit-state={explicit_empty}"
            else:
                status = "FAIL"
                detail = "empty-frame-without-explicit-state"
            results.append({"title": title, "status": status, "kind": "query", "detail": detail})
        except (OSError, ValueError, requests.RequestException, json.JSONDecodeError) as exc:
            results.append({"title": title, "status": "FAIL", "kind": "query", "detail": type(exc).__name__})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grafana-url", default=os.environ.get("GRAFANA_URL", "http://127.0.0.1:3001"))
    parser.add_argument("--env-file", type=Path, default=Path("/home/cloud/grafana/.env"))
    parser.add_argument("--dashboard-dir", type=Path, default=DEFAULT_DASHBOARD_DIR)
    parser.add_argument("--dashboard", action="append", help="Dashboard filename; defaults to every JSON dashboard")
    parser.add_argument("--from", dest="time_from", help="Grafana relative time, otherwise use each dashboard default")
    parser.add_argument("--to", dest="time_to", default="now")
    args = parser.parse_args(argv)

    values = _read_env(args.env_file)
    user = os.environ.get("GF_SECURITY_ADMIN_USER", values.get("GF_SECURITY_ADMIN_USER", ""))
    password = os.environ.get("GF_SECURITY_ADMIN_PASSWORD", values.get("GF_SECURITY_ADMIN_PASSWORD", ""))
    if not user or not password:
        print("GRAFANA_QUERY_CHECK=BLOCKED_MISSING_EXTERNAL_CREDENTIAL")
        return 2

    paths = [args.dashboard_dir / name for name in args.dashboard] if args.dashboard else sorted(args.dashboard_dir.glob("*.json"))
    session = requests.Session()
    session.auth = (user, password)
    failures = 0
    no_data_panels = 0
    for path in paths:
        for result in check_dashboard(session, args.grafana_url, path, args.time_from, args.time_to):
            print(f"PANEL={path.stem}/{result['title']} STATUS={result['status']} {result['kind']} {result['detail']}")
            failures += result["status"] == "FAIL"
            no_data_panels += result["status"] == "NO_DATA"
    if failures:
        summary = "FAIL"
    elif no_data_panels:
        summary = "PASS_WITH_NO_DATA"
    else:
        summary = "PASS"
    print(f"GRAFANA_QUERY_CHECK={summary}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
