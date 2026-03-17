# Deepnoa Office UI Role Architecture

## Roles

- `dev`
  - Owns GitHub webhook execution, implementation, code review, and release workflows.
- `ops`
  - Owns cron, monitoring, NAS checks, service health, and operational triage.
- `research`
  - Owns public summaries, research tasks, and gateway requests that are informational and public-safe.
- `main`
  - Universal fallback worker. Used only when a task does not clearly belong to another role or when a routed role fails.

## Escalation To Main

- `dev -> main`
  - When the task is not primarily development work.
  - When role execution fails and fallback continuity is required.
- `ops -> main`
  - When the task is not primarily operational work.
  - When role execution fails and fallback continuity is required.
- `research -> main`
  - When the task is not primarily research work.
  - When role execution fails and fallback continuity is required.

## Public-State Rules

- Canonical public route is `GET /api/public/state`.
- Deprecated compatibility route is `GET /public-state`.
- Deprecated-route hit logging now writes to `deprecated-route-access.jsonl` and should be checked before compatibility removal.
- Public state is bridge-generated from:
  - `manager-state.json`
  - `agents-state.json`
  - `state.json`
  - OpenClaw cron jobs metadata when available
  - GitHub worker/deploy log mtimes when available
- Raw internal logs are never exposed.
- Public payloads never include approval ids, approval contents, or event provenance labels.
- Public summaries must not include:
  - private repo names
  - internal file paths
  - API keys or tokens
  - NAS contents
  - Task-Noa private data
  - raw internal logs

## Internal Separation

- `/`
  - public-safe AI Office view
- `/gateway`
  - public-safe intake and routing view
- `/internal`
  - internal-only legacy view placeholder, not part of the public experience

## Approval Contract

- Canonical approval statuses:
  - `pending`
  - `approved`
  - `rejected`
  - `expired`
- `approval.requested`
  - starts the approval lifecycle
  - does not terminate the task lifecycle
- `approval.resolved`
  - terminates the approval lifecycle
  - should be ordered before any related `task.failed` or `task.completed`
- `rejected`
  - remains distinct from `task.failed`
  - becomes task failure only when runtime also emits or the bridge receives `task.failed`
- provenance labels in internal state:
  - `actual`
  - `derived`
  - `backfilled`

## Internal View Interpretation

- The internal surface should be read top-down by operational urgency:
  - pending approvals
  - blocked tasks
  - failed tasks
  - degraded or error connectors
  - recently completed tasks
  - low-priority activity
- `approval.resolved(rejected)` is not identical to `task.failed`
  - rejection explains approval outcome
  - `task.failed` explains task termination
- task rows should surface linked approval state when present so operators can see whether a task is blocked by approval, ended after rejection, or failed independently
- runtime payload summaries that remain English are an upstream localization issue
  - frontend translates labels and enums only
  - upstream producers or bridge summaries should own summary-text localization

## Internal Drilldown Direction

- Keep the lifecycle-priority overview visible at all times.
- Selecting an approval, task, alert, or connector should open a persistent detail pane instead of replacing the overview.
- Task drilldown should show:
  - linked approval state
  - recent lifecycle events
  - per-event provenance
  - connector and alert impact
  - raw internal summary
- Approval drilldown should stay distinct from task failure:
  - `approval.resolved(rejected)` explains the approval outcome
  - `task.failed` explains whether the task later terminated
