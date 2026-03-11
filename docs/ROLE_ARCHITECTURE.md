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

- `public-state` is generated from manager state only.
- Raw internal logs are never exposed.
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
