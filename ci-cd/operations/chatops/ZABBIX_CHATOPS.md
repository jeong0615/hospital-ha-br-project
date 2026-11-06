# Zabbix to ChatOps Alert bridge

## Current Routing

The old Zabbix `Slack_Alert` Media Type is a separate monitoring path:

```text
Zabbix Slack_Alert -> params.URL -> Slack Incoming Webhook -> channel
```

It does not use `SLACK_ALERT_BOT_TOKEN`, `/zabbix/alert`, the SQLite registry,
or the ChatOps 24-hour audit/cleanup process. It is not the Failover or
Failback control path and must not be removed before the replacement is
verified.

The target monitoring path is:

```text
Zabbix -> /zabbix/alert -> ChatOps Alert Bot -> #dr-alerts
Wazuh -> /wazuh/alert -> ChatOps Alert Bot -> #dr-alerts
```

The control paths remain separate:

```text
Slack_DR_Trigger -> ChatOps Failover -> two-person approval
Slack Failback    -> ChatOps Failback -> two-person approval
```

Action 3 Recovery uses the same fail-closed recovery guard as the explicit
failback controller.  `send_failback.sh` invokes the mounted
`zabbix_failback_guard.sh` only for the production `__zabbix__` marker.  The
guard requires persistent `DR_ACTIVE`, no prior failback request, and a
continuous `FAILBACK_STABILITY_SECONDS` (default 300) window in which the
configured on-prem Web, Doc, DB, and required Network/SNMP probes pass.  The
Network/SNMP probe reads the existing Zabbix `Cisco IOS: SNMP agent
availability` items for Router, Catalyst 2950, Catalyst 3560, and Catalyst
3550; it does not poll physical devices a second time. A missing guard or missing
probe configuration is a safe rejection, not an immediate approval.  The
separate broad test media remains opt-in and can apply the guard only when
`FAILBACK_GUARD_APPLY_TO_TEST_MEDIA=true` is explicitly supplied.

Set `ONPREM_WEB_HEALTH_URL`, `ONPREM_DOC_HEALTH_URL`, and `ONPREM_DB_HOST` in
the external runtime environment.  Configure the existing read-only Zabbix
API identity through `NETWORK_DR_HEALTH_ZABBIX_ENV_FILE` or the
`NETWORK_DR_HEALTH_ZABBIX_*` variables.  The guard has no documentation-address
fallback; missing probe configuration or missing/stale Network/SNMP data fails
closed. Unrelated SNMP warnings (Fan2, an unused interface, unsupported OID,
or CPU/RAM warning) are not part of this gate.

Failover approval events carry a correlation ID into the runtime RTO timeline
at `/var/lib/hospital-dr/rto_timeline.json`.  Approval creation, final
approval, workflow start, and successful service recovery are recorded with
UTC ISO-8601 timestamps; incomplete runs remain `NOT_MEASURED`.
When `zabbix_sender` is available, the workflow publishes the measured
timeline to the existing `Hospital_DR_Automation` trapper contract:
`dr.rto.seconds`, `dr.rto.target_seconds`, `dr.rto.result`,
`dr.rto.detection_seconds`, `dr.rto.approval_creation_seconds`,
`dr.rto.approval_seconds`, `dr.rto.approval_to_start_seconds`, and
`dr.rto.execution_seconds`. Raw stage timestamps use `dr.<stage>`; missing
stages are omitted rather than published as zero.

The Zabbix script media types use the current shared request engine:

```text
Slack DR Trigger -> /usr/lib/zabbix/alertscripts/send_slack.sh
Slack Failback   -> /usr/lib/zabbix/alertscripts/send_failback.sh
```

Their Zabbix 6 script parameters are three newline-delimited values:

```text
__zabbix__
{ALERT.SUBJECT}
{ALERT.MESSAGE}
```

Broad TEST MODE uses the same three parameters. Its alert message contains
the event ID, host, and event age fields; the shell helper extracts those
fields because separate non-`ALERT.*` macros may be passed literally by the
Zabbix 6 custom-script runner.

The marker distinguishes a real Zabbix invocation from the two-argument
manual helper form. Zabbix custom scripts receive configured parameters as
command-line arguments; they must not depend on inherited environment
variables. When an event ID is not explicitly supplied, the helper derives a
stable non-secret digest from the alert subject and message for duplicate
suppression.

## Bridge Behavior

`POST /zabbix/alert` accepts a normalized Zabbix event and sends a
button-free monitoring alert through the existing `SLACK_ALERT_BOT_TOKEN`
client. It reuses the existing SQLite message registry and audit/cleanup
process. Duplicate `source`/`EventID`/lifecycle-status deliveries are
suppressed by the registry. Zabbix recovery operations may reuse the original
problem EventID, so PROBLEM and RESOLVED notifications are tracked separately.

Only an explicit `DR_Escalate=1` from an existing Zabbix DR Action can reuse
the existing Failover request engine. Normal Zabbix alerts never create
approval requests. Alert and approval messages remain separate.

The canonical production source for that tag is the Zabbix composite trigger
`Hospital DR: On-Prem core services sustained failure`, reconciled by
`operations/zabbix/configure_production_dr_triggers.py`. It requires the Web
`/health`, Doc `/api/patients`, and DB TCP/3306 checks to fail across the
five-minute Zabbix window and depends on the three Agent-unavailable triggers
so a monitoring/WireGuard path failure is suppressed. Action 3 additionally
requires the event to be unsuppressed (outside maintenance). Individual
Agent, reboot, resource, SNMP, Wazuh, service, or DB-monitor alerts remain
alert-only unless all three core checks fail together.

`zabbix_media_type.js` is the payload template for the Zabbix JavaScript Media
Type. It does not contain a Slack token. Its `params.URL` must eventually
point to an internal-only bridge path.

## Docker-to-Host Constraint

ChatOps remains bound to `127.0.0.1:5000`. The Zabbix server is defined in
`/home/cloud/zabbix/docker-compose.yml` as a normal Compose service; it does
not use `network_mode: host` or a configured `extra_hosts` entry. A container's
`127.0.0.1` is therefore not the MSP host loopback. Adding only
`host.docker.internal` would also be insufficient while ChatOps listens only
on loopback.

Recommended minimum-change path:

1. Read-only inspect the running Zabbix container and its actual bridge
   gateway/subnet.
2. Run `zabbix_host_proxy.py` as a host-side reverse proxy bound only to
   `172.18.0.1:5001`. It allows only source `172.18.0.5` and forwards to
   `127.0.0.1:5000/zabbix/alert`.
3. Keep ChatOps on loopback and do not publish port 5000 or the proxy port on
   the MSP/Internet-facing interface.
4. Point the Zabbix Media Type `params.URL` to
   `http://172.18.0.1:5001/zabbix/alert` only
   after the local and container-origin tests pass.

Changing Zabbix to host networking or containerizing ChatOps would affect more
of the existing deployment and is not the first choice.

The confirmed current network is:

```text
network: zabbix_default
subnet: 172.18.0.0/16
gateway: 172.18.0.1
zabbix-server: 172.18.0.5
```

The proxy is implemented by `zabbix_host_proxy.py` and the systemd unit
`systemd/hospital-dr-zabbix-proxy.service`:

```text
172.18.0.1:5001 (source 172.18.0.5 only)
    -> 127.0.0.1:5000/zabbix/alert
```

On the MSP host, direct access to the proxy is expected to return `403` because
the host is not the allow-listed Zabbix container source. The actual
Zabbix-container request must return `2xx`; that test requires Docker socket
access and must be run with read-only `sudo docker exec` if the regular user is
not a member of the Docker group.

## Activation and Rollback

Before activation:

1. Confirm the running `zabbix-server` container and Docker network with
   read-only `sudo docker inspect`.
2. Start ChatOps, then start the proxy unit and verify that it binds only to
   `172.18.0.1:5001`.
3. Verify the proxy from the Zabbix container to `/zabbix/alert`.
4. Send a non-DR test and verify one ChatOps Alert message with no buttons.
5. Send an explicit escalation test with the Failover executor mocked.
6. Confirm audit/cleanup registration and no duplicate messages.
7. Disable the old `Slack_Alert` Media Type only after the replacement passes;
   retain its configuration for immediate rollback.

The Zabbix Media Type URL is then:

```text
http://172.18.0.1:5001/zabbix/alert
```

Do not put Slack tokens or Webhook values in this repository.
