# Reproducible Zabbix ChatOps configuration

`configure_chatops.py` reconciles the named Zabbix media types, actions, and
DR recipient through the Zabbix JSON-RPC API. It never writes PostgreSQL and
does not store API credentials or Slack secrets in the repository.

The policy is:

```text
ChatOps_Alert_Automation
  Problem   -> ChatOps_Alert
  Recovery  -> ChatOps_Alert

Cloud_Slack_DR_Trigger (event tag DR_Escalate equals 1)
  Problem   -> Slack DR Trigger
  Recovery  -> Slack Failback
```

`DR_Escalate=1` is an explicit DR-trigger contract, not a generic Agent or
SNMP availability condition. Only a service-level/composite trigger that has
been deliberately tagged by the operator should set it; ordinary Agent
unavailability, host availability, and an individual SNMP device remain
general monitoring alerts. The production Action 3 message includes the
Zabbix Event ID and `EVENT.AGE`; the ChatOps approval endpoint rejects a
missing, invalid, or older-than-24-hours Zabbix approval event. This blocks a
historical Recovery from creating a new Failback request. Production Failback
also requires the persistent DR lifecycle state to be `DR_ACTIVE`; a Recovery
while the MSP is `IDLE`, `FAILED`, or otherwise outside the active DR lifecycle
is rejected before Slack delivery. The existing two-person gate, duplicate
merge behavior, and separate Broad Test Mode path are preserved.

The script resolves media, action, user, and group IDs by their exact names.
Existing unrelated user media are preserved. Duplicate target media or
ambiguous operations stop the run for manual review rather than deleting or
merging live configuration.

## Production Failover escalation policy

`operations/zabbix/configure_production_dr_triggers.py` owns the production
candidate that feeds `Cloud_Slack_DR_Trigger`. It runs the source-backed checks
from the Zabbix server host:

- Web: `http://192.168.200.3/health` must fail.
- Doc/EMR: `http://192.168.200.5/api/patients` must fail.
- DB: `net.tcp.service[tcp,192.168.200.4,3306]` must fail.

Any one of the three checks must remain failed through its Zabbix five-minute
window before the composite trigger `Hospital DR: On-Prem core services
sustained failure` can enter Problem. The HTTP failure counters use
`min(...,5m)=1`, while the TCP health value uses `max(...,5m)=0`, so a single
transient sample cannot satisfy the sustained-failure policy. The trigger carries
`DR_Escalate=1`,
`DR_Type=Failover`, and `DR_Scope=Hospital-Core`. The three existing
On-Prem Agent-unavailable triggers are dependencies of this composite trigger;
this deliberately fails closed when the monitoring/WireGuard path is down.
CPU, memory, disk, reboot, individual service, SNMP, Wazuh, and generic Agent
events remain ordinary alerts.

| Situation | General Alert | Failover approval | Failback approval |
| --- | ---: | ---: | ---: |
| Reboot, Agent disconnect, WG/path disconnect | O | X | X |
| CPU/RAM/Disk warning or temporary service outage | O | X | X |
| Individual Web/Doc/EMR core failure for the 5-minute window and path healthy | O | O | X |
| Individual DB core TCP failure for the 5-minute window and path healthy | O | O | X |
| Multiple core-service failures for the 5-minute window and path healthy | O | O | X |
| Monitoring-path failure, SNMP failure, or Wazuh disconnect | O | X | X |
| Failover completion | O | - | X |
| Temporary On-Prem recovery | O | X | X |
| `DR_ACTIVE` plus all core services and required Network/SNMP health stable for 5 minutes | O | X | O |
| Stale Recovery event | O | X | X |

Failback has an additional read-only Network/SNMP gate. It requires fresh
availability data for the Router, Catalyst 2950, Catalyst 3560, and Catalyst
3550 from the existing Zabbix items. A device marked unavailable or stale
blocks Failback; unrelated SNMP warnings and unsupported OIDs do not. The
gate is evaluated again before an approval is accepted, and a regression
resets any partial approvals.

The production Action 3 filter requires both `DR_Escalate=1` and
`Problem is suppressed = No`, so maintenance-suppressed events cannot create a
production approval. Retired test objects are not silently normalized by the
production reconciler; they must be removed with the exact decommissioning
utility before a stray escalation tag can be accepted. The production
reconciliation is idempotent, has `--check` and `--apply`, never deletes
objects, and writes a redacted external pre-change snapshot before an apply:

```bash
python3 operations/zabbix/configure_production_dr_triggers.py \
  --check --env-file /path/to/external/zabbix.env

python3 operations/zabbix/configure_production_dr_triggers.py \
  --apply --env-file /path/to/external/zabbix.env \
  --backup-dir /var/lib/hospital-dr/zabbix-config-backups
```

The policy is intentionally fail-closed. A complete physical host outage is
not distinguishable from a monitoring-path outage while the Agent dependency
is in Problem, so it is suppressed until an independent path signal is
available. No AWS or Slack execution is performed by the reconciler.

Use an external environment file or exported variables. The current MSP API
credential file is outside this repository; for example:

```bash
python3 operations/zabbix/configure_chatops.py \
  --check \
  --env-file /home/cloud/hospital-dr-ansible/roles/web_deploy/files/.env
```

The file must provide `ZABBIX_URL` (or `ZABBIX_API_URL`) and either
`ZABBIX_USER`/`ZABBIX_PASSWORD` or an API token. Optional names are
`ZABBIX_RECIPIENT_USERNAME`, `ZABBIX_FAILOVER_RECIPIENT_GROUP`, and
`ZABBIX_CHATOPS_ALERT_URL`. Secret values are read only in memory and are
never printed.

Review drift without changing Zabbix:

```bash
python3 operations/zabbix/configure_chatops.py --check --env-file /path/to/external/zabbix.env
```

Apply only the required changes. A redacted, mode-0600 pre-change export is
written outside Git before the API mutation:

```bash
python3 operations/zabbix/configure_chatops.py \
  --apply \
  --env-file /path/to/external/zabbix.env \
  --backup-dir /var/lib/hospital-dr/zabbix-config-backups
```

Create a standalone redacted export when needed:

```bash
python3 operations/zabbix/configure_chatops.py \
  --export /var/lib/hospital-dr/zabbix-config-backups/chatops-live.json \
  --env-file /path/to/external/zabbix.env
```

`--check` returns 0 when compliant, 2 when drift is found, and 1 when the
configuration is blocked or the API cannot be reached. `--apply` verifies the
result with a fresh readback.

## Broad approval-path test mode

The broad test action is separate from `Cloud_Slack_DR_Trigger` and is off by
default. No persistent ChatOps test server is provisioned anymore. If a
controlled test window is explicitly opened, the action matches only the
named existing hosts and routes a Problem to the existing Failover approval
engine and a Recovery to the existing Failback approval engine. The test
action uses the existing shell media path with a test marker; it does not
bypass two-person approval.

Inspect without changing Zabbix:

```bash
python3 operations/zabbix/configure_broad_test_mode.py \
  --check --env-file /path/to/external/zabbix.env
```

Enable only for a controlled test window:

```bash
python3 operations/zabbix/configure_broad_test_mode.py \
  --enable --env-file /path/to/external/zabbix.env \
  --backup-dir /var/lib/hospital-dr/zabbix-config-backups
```

Disable it after the test; the production `DR_Escalate=1` action is not
changed:

```bash
python3 operations/zabbix/configure_broad_test_mode.py \
  --disable --env-file /path/to/external/zabbix.env \
  --backup-dir /var/lib/hospital-dr/zabbix-config-backups
```

Test-mode Recovery messages include the Zabbix event age. Events older than
`ZABBIX_TEST_MODE_MAX_EVENT_AGE_SECONDS` are rejected before a new Failback
approval request is created, preventing stale historical Problems from
creating a test request.

Do not create temporary trapper items, triggers, or containers in production
Zabbix. If a live validation is explicitly scheduled, use a real monitored
host whose impact is understood and restore the action to disabled state after
the window. A host stop/start verifies only the triggers and actions actually
attached to that host; it does not bypass the normal two-person approval,
Failback stability, stale-event, or duplicate-request guards.

For an additional host, include its exact visible host name in the enabled
broad test action without editing the secret file:

```bash
python3 operations/zabbix/configure_broad_test_mode.py \
  --enable \
  --host 'EXACT SNMP HOST NAME' \
  --env-file /home/cloud/hospital-dr-ansible/roles/web_deploy/files/.env
```
