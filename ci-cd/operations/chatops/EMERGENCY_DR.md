# Emergency DR ChatOps

The Emergency Console is a separate, private Slack channel surface for
creating a DR request when an automatic or ordinary approval message cannot be
used.  It does not grant authority by channel membership: every button and
modal submission is checked against `SLACK_ADMIN_USER_IDS`.

## Configuration

Set these values in the external `DR_SECRET_FILE`; do not commit the file or
tokens:

```text
SLACK_EMERGENCY_CHANNEL_ID=<private-channel-id>
# Set SLACK_EMERGENCY_BOT_TOKEN only in the external DR_SECRET_FILE.
# Set SLACK_EMERGENCY_SIGNING_SECRET only in the external DR_SECRET_FILE.
EMERGENCY_TTL_SECONDS=900
DUMP_BACKUP_DIR=/home/cloud/db_backups
DUMP_RESTORE_EXEC_ENABLED=false
DUMP_RESTORE_EXECUTOR=
```

The Emergency App is a separate Slack App named `ChatOps Emergency`.  It must
use its own bot token and signing secret; the runtime never falls back to the
Failover bot credential.  Configure its Interactivity Request URL to
`/slack/action`, invite it to the private `긴급승인` channel, and keep the
existing `SLACK_ADMIN_USER_IDS` allowlist.

The minimum Bot Token Scopes used by this implementation are:

- `chat:write` for the Console and request messages
- `pins:write` for pinning the Console

No `conversations.info`/channel-read scope is required.  If `pins:write` is
missing, the Console remains usable and the runtime records
`EMERGENCY_CONSOLE_PIN=SKIPPED_NO_PERMISSION`.

The Console contains exactly four controls: `상태 점검`, `긴급 Failover 요청`,
`긴급 Failback 요청`, and `긴급 Failover (Dump)`.  The status and Dump
controls use Slack's default button style, Failover uses `danger`, and
Failback uses `primary`; no custom Slack styling or emoji is used.

Manual Slack setup order:

1. Create/install the `ChatOps Emergency` App.
2. Configure `chat:write` and `pins:write`.
3. Enable Interactivity with the `/slack/action` callback URL.
4. Store the App bot token and signing secret only in the external
   `DR_SECRET_FILE`.
5. Invite the App to the private `긴급승인` channel and set its channel ID.
6. Restart ChatOps, then allow the existing cleanup worker to create and pin
   the Console.

The existing cleanup worker performs a read-only Emergency Console presence
check every 60 seconds and keeps the normal terminal-message cleanup interval
at 300 seconds. When the persisted message exists, the watchdog makes no
`chat.update` call, does not repin, and does not change the state record. It
creates and pins a replacement only when Slack reports the persisted message
is missing. Slack errors, including rate limits, are left for the next
60-second cycle and do not stop ChatOps.

## Request and approval policy

The console creates `EMERGENCY_FAILOVER` or `EMERGENCY_FAILBACK` requests with
a 15-minute TTL.  Emergency requests bypass automatic detection conditions,
not execution safety checks.  Existing two-distinct-admin approval,
cancel/withdraw, audit, global execution lock, and stale execution recovery
remain in force.  There is no single-admin or MFA path.

Failback requests remain visible when Wazuh is `UNKNOWN` or `BLOCKED`.
`UNKNOWN`/`BLOCKED` is shown as a warning and the approval handler performs a
fresh check; an unsafe request is reset/blocked rather than silently hiding
the request.

## Dump restore

`Dump 복구` inventories only complete, readable `.sql.gz.enc` and matching
`.master_status` pairs whose File/Position metadata parses and whose
OpenSSL `Salted__` encrypted-container marker is present, newest first (up to
20).  The modal displays the computed checksum, parsed replication
coordinates, and container/integrity status.  Selection and confirmation
create a `DUMP_RESTORE_FAILOVER` approval request; they never restore directly.
`DUMP_RESTORE_EXEC_ENABLED=false` is the default, so even two approvals cannot
run a restore until a separately reviewed executor is configured.  File paths,
passwords, tokens, and private keys are not written to Slack or the audit
record; the audit contains backup ID, timestamp, size/checksum metadata,
`request_origin`, and approval lifecycle fields.

## Security escalation

Only an existing Wazuh destructive `CRITICAL` event affecting an important
hospital host is promoted to `SECURITY_DR_ESCALATION`.  Ordinary auth, FIM,
SCA, vulnerability, SQLi/XSS detection, or generic high-severity events remain
security alerts.  Security escalation still creates a two-admin approval and
never auto-executes a DR workflow.

## Safe contract checks

Use the repository test suite and a local test configuration.  Do not send a
real Slack message or invoke a live workflow while validating the feature.
