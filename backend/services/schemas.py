from __future__ import annotations

from datetime import datetime, timezone
import hashlib


SCHEMA_VERSION = "2026-03-17"
WORKSPACE_ID = "deepnoa-office"
EVENT_HISTORY_RETENTION = 200
INTERNAL_EVENTS_RESPONSE_LIMIT = 100
PUBLIC_AGENT_STATES = frozenset({"idle", "writing", "researching", "executing", "syncing", "error"})
INTERNAL_AGENT_STATES = PUBLIC_AGENT_STATES | frozenset({"blocked", "awaiting_approval", "offline", "degraded"})
EVENT_SEVERITIES = frozenset({"info", "warning", "error", "critical"})
CONNECTOR_STATUSES = frozenset({"connected", "degraded", "error", "offline"})
APPROVAL_STATUSES = frozenset({"pending", "approved", "rejected", "expired"})
STANDARD_EVENT_TYPES = frozenset({
    "agent.created",
    "agent.status.changed",
    "task.created",
    "task.assigned",
    "task.started",
    "task.blocked",
    "task.completed",
    "task.failed",
    "approval.requested",
    "approval.resolved",
    "channel.message.received",
    "channel.message.sent",
    "connector.status.changed",
    "runtime.alert",
})
EVENT_PROVENANCE_TYPES = frozenset({"actual", "derived", "backfilled"})
TASK_TERMINAL_EVENT_TYPES = frozenset({"task.completed", "task.failed"})
APPROVAL_TERMINAL_EVENT_TYPES = frozenset({"approval.resolved"})

PUBLIC_ABSTRACTION_RULES = {
    "task_names": "never expose raw task titles; use sanitized summaries",
    "customer_names": "never expose customer or tenant names",
    "file_paths": "replace internal paths with [internal-path]",
    "links": "replace internal or private URLs with [internal-link]",
    "payloads": "never expose queue payloads, tokens, or raw connector state",
    "approvals": "show counts only; do not expose approval contents on public surfaces",
    "errors": "show abstract health only; keep raw error details internal",
    "provenance": "public surfaces omit actual or derived provenance labels",
}

CONNECTOR_HEALTH_RULES = {
    "openclaw_runtime": {
        "connected_max_age_seconds": 180,
        "degraded_max_age_seconds": 900,
        "error_after_seconds": 900,
        "source": "manager-state.json updated_at",
    },
    "github_worker": {
        "connected_max_age_seconds": 300,
        "degraded_max_age_seconds": 1800,
        "error_after_seconds": 1800,
        "source": "github_queue_local worker/deploy log mtime",
    },
    "openclaw_cron": {
        "connected_when": "jobs.json exists and enabled jobs have no error",
        "degraded_when": "jobs.json exists and any enabled job is running/queued",
        "error_when": "jobs.json missing or any enabled job lastStatus == error",
        "source": "~/.openclaw/cron/jobs.json",
    },
}

APPROVAL_LIFECYCLE_RULES = {
    "requested_event": "approval.requested opens the approval lifecycle and does not terminate the task",
    "resolved_event": "approval.resolved terminates the approval lifecycle only",
    "approved_effect": "approved means execution may continue; task completion still needs task.completed",
    "rejected_effect": "rejected stays distinct from task.failed; emit task.failed separately when the task actually terminates",
    "expired_effect": "expired means the approval window closed; task termination remains separate unless runtime emits task.failed",
    "ordering": "when both occur, emit approval.resolved before any terminal task event",
}

DEPRECATED_ROUTE_META = {
    "/public-state": {
        "replacement": "/api/public/state",
        "status": "deprecated",
    },
    "/internal-state": {
        "replacement": "/api/internal/state",
        "status": "deprecated",
    },
}


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def normalize_public_state(value: str | None) -> str:
    state = str(value or "idle").strip().lower() or "idle"
    if state in PUBLIC_AGENT_STATES:
        return state
    if state in {"blocked", "awaiting_approval"}:
        return "syncing"
    if state in {"offline", "degraded"}:
        return "error"
    return "idle"


def normalize_public_status_label(value: str | None, *, context: str = "general") -> str:
    raw = str(value or "").strip()
    normalized = normalize_public_state(raw)
    lowered = raw.lower()

    if context == "gateway":
        if "routing" in lowered or "dispatch" in lowered or "受付" in raw or "振り分" in raw:
            return "受付中"
        if "error" in lowered or "offline" in lowered or "停止" in raw:
            return "要確認"
        if "standby" in lowered or "ready" in lowered or normalized == "idle":
            return "待機中"
        return "受付中"

    if "research" in lowered or "調査" in raw:
        return "調査中"
    if "review" in lowered or "build" in lowered or "processing" in lowered or "処理" in raw:
        return "処理中"
    if "monitor" in lowered or "watch" in lowered or "standby" in lowered or "ready" in lowered or "待機" in raw:
        return "待機中"
    if "need" in lowered or "investigat" in lowered or "attention" in lowered or "review" in lowered or "要確認" in raw:
        return "要確認"
    if "offline" in lowered or "停止" in raw:
        return "停止中"
    if "degraded" in lowered or "注意" in raw:
        return "注意"

    return {
        "idle": "待機中",
        "writing": "処理中",
        "researching": "調査中",
        "executing": "稼働中",
        "syncing": "同期中",
        "error": "要確認",
    }.get(normalized, "待機中")


def normalize_public_health_status(value: str | None) -> str:
    lowered = str(value or "").strip().lower()
    if lowered in {"watch", "attention", "degraded", "error"}:
        return "attention"
    if lowered == "offline":
        return "offline"
    return "normal"


def normalize_public_summary_text(value: str | None) -> str:
    import re

    text = str(value or "").strip()
    if not text:
        return "公開可能な作業内容はありません。"

    rewrites = [
        (r"GitHub webhook queue is being processed and deployment flow is active\.?", "GitHub連携の処理を進めています。"),
        (r"Processing GitHub webhook queue\.?", "GitHub連携の処理を進めています。"),
        (r"GitHub webhook queue\.?", "GitHub連携の処理を進めています。"),
        (r"deployment flow is active\.?", "公開可能な更新処理を進めています。"),
        (r"Dispatching .*? via manager-first router\.?", "公開リクエストを適切な担当へ振り分けています。"),
        (r"Ready for work\.?", "新しい依頼に備えて待機しています。"),
        (r"Reception AI ready to route work\.?", "公開リクエストを受け付ける準備ができています。"),
        (r"Routing a public-safe intake through the reception layer\.?", "公開リクエストを受付経由で処理しています。"),
        (r"Public-safe request received by the gateway\.?", "公開リクエストを受け付けました。"),
        (r"(\d+)\s+scheduled system checks are configured and standing by\.?", r"定期システムチェック\1件を待機監視しています。"),
        (r"scheduled system checks are configured and standing by\.?", "定期システムチェックを待機監視しています。"),
        (r"reviewing repository activity and preparing implementation work\.?", "実装や更新に向けて、リポジトリの動きを確認しています。"),
        (r"monitoring scheduled checks and system health\.?", "定期チェックとシステム状態を確認しています。"),
        (r"collecting public information and drafting a summary\.?", "公開可能な情報を整理し、要点をまとめています。"),
        (r"Universal worker is handling a task that did not match a dedicated role\.?", "専用ルートに当てはまらない作業を処理しています。"),
        (r"is processing a routed task\.?", "担当タスクを処理しています。"),
    ]
    for pattern, replacement in rewrites:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_internal_state(value: str | None) -> str:
    state = str(value or "idle").strip().lower() or "idle"
    if state in INTERNAL_AGENT_STATES:
        return state
    return normalize_public_state(state)


def normalize_provenance(value: str | None) -> str:
    provenance = str(value or "actual").strip().lower() or "actual"
    if provenance in EVENT_PROVENANCE_TYPES:
        return provenance
    return "actual"


def normalize_approval_status(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "allow-once": "approved",
        "allow_always": "approved",
        "allow-always": "approved",
        "approved": "approved",
        "approve": "approved",
        "deny": "rejected",
        "denied": "rejected",
        "rejected": "rejected",
        "reject": "rejected",
        "timeout": "expired",
        "timed_out": "expired",
        "timed-out": "expired",
        "expired": "expired",
        "pending": "pending",
        "approval-pending": "pending",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in APPROVAL_STATUSES:
        return normalized
    return ""


def normalize_severity(value: str | None, state: str | None = None, event_type: str | None = None) -> str:
    severity = str(value or "").strip().lower()
    if severity in EVENT_SEVERITIES:
        return severity
    if event_type == "runtime.alert":
        return "warning"
    normalized_state = normalize_internal_state(state)
    if normalized_state in {"error", "offline"}:
        return "error"
    if normalized_state in {"blocked", "awaiting_approval", "degraded"}:
        return "warning"
    return "info"


def normalize_event_type(value: str | None, source: str | None = None, state: str | None = None) -> str:
    event_type = str(value or "").strip().lower()
    if event_type in STANDARD_EVENT_TYPES:
        return event_type

    legacy_map = {
        "manual": "agent.status.changed",
        "manual_status": "agent.status.changed",
        "agent_push": "agent.status.changed",
        "github_webhook": "task.started",
        "research_task": "task.started",
        "public_summary": "task.started",
        "cron_check": "connector.status.changed",
        "system_check": "connector.status.changed",
    }
    if event_type in legacy_map:
        return legacy_map[event_type]

    normalized_source = str(source or "").strip().lower()
    normalized_state = normalize_internal_state(state)
    if normalized_source in {"github", "public", "slack", "line"}:
        return "channel.message.received"
    if normalized_source in {"cron", "ops", "system"}:
        return "connector.status.changed"
    if normalized_state == "blocked":
        return "task.blocked"
    if normalized_state == "awaiting_approval":
        return "approval.requested"
    if normalized_state in {"error", "offline", "degraded"}:
        return "runtime.alert"
    return "agent.status.changed"


def build_event_id(payload: dict, timestamp: str) -> str:
    parts = [
        str(payload.get("event_type") or ""),
        str(payload.get("source") or ""),
        str(payload.get("agent_id") or payload.get("agentId") or ""),
        str(payload.get("task_id") or ""),
        str(payload.get("summary") or payload.get("detail") or ""),
        timestamp,
    ]
    digest = hashlib.sha1("::".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"evt_{digest}"


def normalize_event_payload(payload: dict, *, workspace_id: str = WORKSPACE_ID, timestamp: str | None = None) -> dict:
    ts = timestamp or str(payload.get("timestamp") or payload.get("updated_at") or iso_now())
    normalized_state = normalize_internal_state(payload.get("state"))
    normalized_type = normalize_event_type(payload.get("event_type"), payload.get("source"), normalized_state)
    summary = str(payload.get("display_summary") or payload.get("summary") or payload.get("detail") or "").strip()
    provenance = normalize_provenance(payload.get("provenance"))
    approval_status = normalize_approval_status(payload.get("approval_status"))
    return {
        "event_id": str(payload.get("event_id") or build_event_id(payload, ts)),
        "event_type": normalized_type,
        "timestamp": ts,
        "workspace_id": str(payload.get("workspace_id") or workspace_id),
        "source": str(payload.get("source") or "manager"),
        "agent_id": str(payload.get("agent_id") or payload.get("agentId") or payload.get("role") or ""),
        "task_id": str(payload.get("task_id") or ""),
        "severity": normalize_severity(payload.get("severity"), normalized_state, normalized_type),
        "display_summary": summary or normalized_type.replace(".", " "),
        "state": normalized_state,
        "provenance": provenance,
        "provenance_label": provenance.replace("_", " "),
        "approval_status": approval_status,
        "approval_id": str(payload.get("approval_id") or ""),
        "raw_payload": payload,
    }


def ensure_summary_contract(summary: dict | None) -> dict:
    base = {
        "active_agents": 0,
        "active_tasks": 0,
        "blocked": 0,
        "awaiting_approval": 0,
        "done_today": 0,
        "alerts": 0,
        "status": "normal",
    }
    if isinstance(summary, dict):
        base.update({k: summary.get(k, base[k]) for k in base})
    return base


def build_public_state_contract(
    *,
    office: dict,
    gateway: dict,
    agents: list[dict],
    activity: list[dict],
    recent_work: list[dict],
    systems: list[dict],
    health: dict,
    summary: dict,
    intake: list[dict],
    transport: str,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "surface": "public",
        "transport": transport,
        "office": office,
        "gateway": gateway,
        "summary": ensure_summary_contract(summary),
        "agents": list(agents or []),
        "activity": list(activity or []),
        "recent_work": list(recent_work or []),
        "systems": list(systems or []),
        "health": dict(health or {}),
        "intake": list(intake or []),
        "public_abstraction_rules": dict(PUBLIC_ABSTRACTION_RULES),
    }


def build_internal_state_contract(
    *,
    office: dict,
    manager: dict,
    summary: dict,
    roles: list[dict],
    agents: list[dict],
    activity: list[dict],
    blocked: list[dict],
    approvals: list[dict],
    failed: list[dict],
    connectors: list[dict],
    completed: list[dict],
    alerts: list[dict],
    events: list[dict],
    intake: list[dict],
    assets: dict,
    policies: dict,
    transport: str,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "surface": "internal",
        "transport": transport,
        "office": office,
        "manager": dict(manager or {}),
        "summary": ensure_summary_contract(summary),
        "roles": list(roles or []),
        "agents": list(agents or []),
        "activity": list(activity or []),
        "blocked": list(blocked or []),
        "approvals": list(approvals or []),
        "failed": list(failed or []),
        "connectors": list(connectors or []),
        "completed": list(completed or []),
        "alerts": list(alerts or []),
        "events": list(events or []),
        "intake": list(intake or []),
        "assets": dict(assets or {}),
        "policies": dict(policies or {}),
    }


def build_events_contract(*, events: list[dict], since: str = "") -> dict:
    filtered = list(events or [])
    filtered.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    if since:
        filtered = [item for item in filtered if str(item.get("timestamp") or "") > since]
    filtered = filtered[:INTERNAL_EVENTS_RESPONSE_LIMIT]
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "surface": "internal",
        "ordering": "timestamp desc",
        "since_rule": "strictly greater than since timestamp",
        "retention": EVENT_HISTORY_RETENTION,
        "events": filtered,
    }
