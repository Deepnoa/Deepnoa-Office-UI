from __future__ import annotations

from datetime import datetime
import json
import os

from .schemas import (
    APPROVAL_LIFECYCLE_RULES,
    CONNECTOR_HEALTH_RULES,
    EVENT_HISTORY_RETENTION,
    INTERNAL_EVENTS_RESPONSE_LIMIT,
    SCHEMA_VERSION,
    WORKSPACE_ID,
    build_internal_state_contract,
    build_public_state_contract,
    normalize_event_payload,
    normalize_internal_state,
    normalize_provenance,
    normalize_public_health_status,
    normalize_public_state,
    normalize_public_status_label,
    normalize_public_summary_text,
)
from .source_adapters import dedupe_events, derive_missing_lifecycle, intake_events, manager_activity_events, snapshot_events


EVENT_PRIORITY = {
    "task.blocked": 0,
    "approval.requested": 1,
    "task.failed": 2,
    "runtime.alert": 3,
    "task.completed": 4,
    "agent.status.changed": 5,
    "task.started": 6,
    "connector.status.changed": 7,
    "channel.message.received": 8,
    "channel.message.sent": 9,
}
INTERNAL_ACTIVITY_EVENT_TYPES = frozenset({
    "agent.status.changed",
    "task.created",
    "task.assigned",
    "task.started",
    "channel.message.received",
    "channel.message.sent",
})
PUBLIC_EVENT_SOURCES = frozenset({"github", "cron", "public", "manager", "fallback", "ops", "research"})
PUBLIC_EVENT_TYPES = frozenset({"task.started", "task.blocked", "task.completed", "task.failed", "connector.status.changed", "channel.message.received", "runtime.alert"})


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _same_day(left: datetime, right: datetime) -> bool:
    return left.date() == right.date()


def _file_mtime(path: str | None) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).astimezone().isoformat()
    except Exception:
        return ""


def _age_seconds_from_iso(value: str | None) -> int | None:
    parsed = _parse_iso(value)
    if not parsed:
        return None
    return max(0, int((datetime.now().astimezone() - parsed.astimezone()).total_seconds()))


def _status_from_age(age_seconds: int | None, *, connected_max: int, degraded_max: int) -> str:
    if age_seconds is None:
        return "error"
    if age_seconds <= connected_max:
        return "connected"
    if age_seconds <= degraded_max:
        return "degraded"
    return "error"


def _load_json_file(path: str | None) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def collect_openclaw_inputs(input_paths: dict | None) -> dict:
    paths = dict(input_paths or {})
    cron_jobs_path = paths.get("cron_jobs_path") or ""
    github_worker_log = paths.get("github_worker_log") or ""
    github_deploy_log = paths.get("github_deploy_log") or ""
    office_identity_path = paths.get("office_identity_path") or ""

    cron_jobs = _load_json_file(cron_jobs_path)
    jobs = cron_jobs.get("jobs") or []
    if not isinstance(jobs, list):
        jobs = []

    return {
        "source_catalog": {
            "manager_state": {
                "kind": "event-log",
                "connected": True,
                "path": paths.get("manager_state_path") or "",
            },
            "agents_state": {
                "kind": "agent-presence",
                "connected": True,
                "path": paths.get("agents_state_path") or "",
            },
            "primary_state": {
                "kind": "main-agent-state",
                "connected": True,
                "path": paths.get("primary_state_path") or "",
            },
            "openclaw_cron_jobs": {
                "kind": "connector-input",
                "connected": bool(cron_jobs_path and os.path.exists(cron_jobs_path)),
                "path": cron_jobs_path,
                "jobs": jobs,
                "last_sync": _file_mtime(cron_jobs_path),
            },
            "github_queue_logs": {
                "kind": "connector-input",
                "connected": bool((github_worker_log and os.path.exists(github_worker_log)) or (github_deploy_log and os.path.exists(github_deploy_log))),
                "worker_log": github_worker_log,
                "deploy_log": github_deploy_log,
                "worker_last_sync": _file_mtime(github_worker_log),
                "deploy_last_sync": _file_mtime(github_deploy_log),
            },
            "identity_file": {
                "kind": "office-metadata",
                "connected": bool(office_identity_path and os.path.exists(office_identity_path)),
                "path": office_identity_path,
            },
        }
    }


def normalize_agent_snapshot(agent: dict) -> dict:
    return {
        "agent_id": str(agent.get("agentId") or agent.get("agent_id") or agent.get("key") or ""),
        "name": str(agent.get("name") or agent.get("agentId") or "Unknown agent"),
        "role": str(agent.get("role") or ""),
        "state": normalize_internal_state(agent.get("state")),
        "public_state": normalize_public_state(agent.get("state")),
        "area": str(agent.get("area") or ""),
        "source": str(agent.get("source") or ""),
        "auth_status": str(agent.get("authStatus") or ""),
        "is_main": bool(agent.get("isMain")),
        "updated_at": str(agent.get("updated_at") or ""),
        "last_push_at": str(agent.get("lastPushAt") or ""),
    }


def normalize_task_snapshot(event: dict) -> dict:
    task_state = event["event_type"].split(".")[-1]
    return {
        "task_id": event.get("task_id") or event.get("event_id"),
        "agent_id": event.get("agent_id") or "",
        "state": task_state,
        "summary": event.get("display_summary") or "",
        "severity": event.get("severity") or "info",
        "timestamp": event.get("timestamp") or "",
        "event_type": event.get("event_type") or "",
        "provenance": event.get("provenance") or "actual",
        "provenance_label": event.get("provenance_label") or normalize_provenance(event.get("provenance") or "actual"),
        "approval_status": "",
        "approval_provenance": "",
        "approval_signal_kind": "",
        "approval_id": "",
    }


def normalize_approval_snapshot(event: dict) -> dict:
    approval_status = str(event.get("approval_status") or "")
    signal_source = str((event.get("raw_payload") or {}).get("approval_signal_source") or "")
    return {
        "task_id": event.get("task_id") or event.get("event_id"),
        "approval_id": event.get("approval_id") or "",
        "agent_id": event.get("agent_id") or "",
        "summary": event.get("display_summary") or "",
        "requested_at": event.get("timestamp") or "",
        "status": "pending" if event.get("event_type") == "approval.requested" else approval_status or "resolved",
        "resolution": approval_status,
        "event_type": event.get("event_type") or "",
        "provenance": normalize_provenance(event.get("provenance") or "actual"),
        "provenance_label": event.get("provenance_label") or normalize_provenance(event.get("provenance") or "actual"),
        "source": event.get("source") or "",
        "signal_source": signal_source,
        "signal_kind": "fallback" if signal_source.startswith("fallback.") else "structured" if signal_source else "runtime",
    }


def normalize_connector_snapshot(*, name: str, status: str, last_sync: str, pending_actions: int, auth_status_summary: str) -> dict:
    normalized_status = str(status or "degraded").strip().lower()
    if normalized_status not in {"connected", "degraded", "error", "offline"}:
        normalized_status = "degraded"
    return {
        "name": name,
        "status": normalized_status,
        "last_sync": last_sync,
        "pending_actions": max(0, int(pending_actions or 0)),
        "auth_status_summary": auth_status_summary,
    }


def normalize_alert_snapshot(event: dict) -> dict:
    return {
        "alert_id": event.get("event_id") or "",
        "agent_id": event.get("agent_id") or "",
        "state": normalize_internal_state(event.get("state")),
        "severity": event.get("severity") or "warning",
        "summary": event.get("display_summary") or "",
        "timestamp": event.get("timestamp") or "",
        "event_type": event.get("event_type") or "",
        "provenance": event.get("provenance") or "actual",
        "provenance_label": event.get("provenance_label") or normalize_provenance(event.get("provenance") or "actual"),
    }


def _sort_items(items: list[dict], key_name: str) -> list[dict]:
    return sorted(list(items or []), key=lambda item: str(item.get(key_name) or item.get("timestamp") or ""), reverse=True)


def _normalize_events(manager_state: dict, intake: list[dict], primary_state: dict, normalized_agents: list[dict]) -> list[dict]:
    events = []
    events.extend(manager_activity_events(manager_state))
    events.extend(intake_events(intake))
    events.extend(snapshot_events(primary_state, normalized_agents))
    events.extend(derive_missing_lifecycle(events))
    events = dedupe_events(events)
    return events[:EVENT_HISTORY_RETENTION]


def _build_summary(*, manager_state: dict, normalized_agents: list[dict], tasks: dict, approvals: dict, alerts: list[dict]) -> dict:
    now = datetime.now().astimezone()
    blocked = [task for task in tasks.values() if task["state"] == "blocked"]
    pending_approvals = [item for item in approvals.values() if item["status"] == "pending"]
    active_agents = 0
    for role in (manager_state.get("roles") or {}).values():
        if normalize_internal_state(role.get("state")) not in {"idle", "offline"}:
            active_agents += 1
    for agent in normalized_agents:
        if agent["state"] not in {"idle", "offline"}:
            active_agents += 1

    active_tasks = len([task for task in tasks.values() if task["state"] in {"created", "assigned", "started", "blocked"}])
    if active_tasks == 0:
        active_tasks = len([agent for agent in normalized_agents if agent["state"] not in {"idle", "offline"}])

    completed_today = 0
    for task in tasks.values():
        if task["state"] != "completed":
            continue
        parsed = _parse_iso(task.get("timestamp"))
        if parsed and _same_day(parsed.astimezone(), now):
            completed_today += 1

    alert_count = len([alert for alert in alerts if alert.get("severity") in {"error", "critical"}])
    status = "normal"
    if alert_count:
        status = "attention"
    elif blocked or pending_approvals:
        status = "watch"
    return {
        "active_agents": active_agents,
        "active_tasks": active_tasks,
        "blocked": len(blocked),
        "awaiting_approval": len(pending_approvals),
        "done_today": completed_today,
        "alerts": alert_count,
        "status": status,
    }


def build_openclaw_bridge_snapshot(
    *,
    manager_state: dict,
    agents_state: list[dict],
    primary_state: dict,
    public_systems: list[dict],
    sanitize_public_detail,
    input_paths: dict | None = None,
) -> dict:
    now = datetime.now().astimezone()
    openclaw_inputs = collect_openclaw_inputs(input_paths)
    source_catalog = openclaw_inputs["source_catalog"]
    intake = list(manager_state.get("intake") or [])
    normalized_agents = [normalize_agent_snapshot(agent) for agent in list(agents_state or [])]
    events = _normalize_events(manager_state, intake, primary_state, normalized_agents)

    tasks = {}
    approvals = {}
    alerts = []
    for event in reversed(events):
        event_type = event.get("event_type")
        task_id = event.get("task_id") or event.get("event_id")
        if event_type in {"task.created", "task.assigned", "task.started", "task.blocked", "task.completed", "task.failed"}:
            tasks[task_id] = normalize_task_snapshot(event)
        if event_type in {"approval.requested", "approval.resolved"}:
            approval = approvals.get(task_id) or normalize_approval_snapshot(event)
            if event_type == "approval.resolved":
                approval["status"] = event.get("approval_status") or "resolved"
                approval["resolved_at"] = event.get("timestamp") or ""
            approvals[task_id] = approval
        if event_type == "runtime.alert" or event.get("severity") in {"error", "critical"}:
            alerts.append(normalize_alert_snapshot(event))

    for task_id, approval in approvals.items():
        task = tasks.get(task_id)
        if not task:
            continue
        task["approval_status"] = approval.get("status") or approval.get("resolution") or ""
        task["approval_provenance"] = approval.get("provenance") or ""
        task["approval_signal_kind"] = approval.get("signal_kind") or ""
        task["approval_id"] = approval.get("approval_id") or ""

    latest_manager_update = _parse_iso(manager_state.get("updated_at"))
    manager_age_seconds = 999999
    if latest_manager_update:
        manager_age_seconds = max(0, int((now - latest_manager_update.astimezone()).total_seconds()))

    runtime_rules = CONNECTOR_HEALTH_RULES["openclaw_runtime"]
    cron_rules = CONNECTOR_HEALTH_RULES["openclaw_cron"]
    github_rules = CONNECTOR_HEALTH_RULES["github_worker"]
    connectors = [
        normalize_connector_snapshot(
            name="OpenClaw runtime",
            status=_status_from_age(
                manager_age_seconds,
                connected_max=runtime_rules["connected_max_age_seconds"],
                degraded_max=runtime_rules["degraded_max_age_seconds"],
            ),
            last_sync=str(manager_state.get("updated_at") or ""),
            pending_actions=len([task for task in tasks.values() if task["state"] == "blocked"]) + len([item for item in approvals.values() if item["status"] == "pending"]),
            auth_status_summary="manager-state events",
        )
    ]
    cron_jobs = source_catalog["openclaw_cron_jobs"]["jobs"]
    enabled_jobs = [job for job in cron_jobs if isinstance(job, dict) and job.get("enabled")]
    cron_errors = [job for job in enabled_jobs if ((job.get("state") or {}).get("lastStatus") == "error")]
    cron_running = [job for job in enabled_jobs if ((job.get("state") or {}).get("lastStatus") in {"running", "queued"})]
    if not source_catalog["openclaw_cron_jobs"]["connected"]:
        cron_status = "error"
    elif cron_errors:
        cron_status = "error"
    elif cron_running:
        cron_status = "degraded"
    else:
        cron_status = "connected"
    connectors.append(
        normalize_connector_snapshot(
            name="OpenClaw cron",
            status=cron_status,
            last_sync=source_catalog["openclaw_cron_jobs"]["last_sync"],
            pending_actions=len(cron_errors) + len(cron_running),
            auth_status_summary=cron_rules["source"],
        )
    )

    github_last_sync = max(
        [value for value in [source_catalog["github_queue_logs"]["worker_last_sync"], source_catalog["github_queue_logs"]["deploy_last_sync"]] if value],
        default="",
    )
    github_age_seconds = _age_seconds_from_iso(github_last_sync)
    connectors.append(
        normalize_connector_snapshot(
            name="GitHub worker",
            status=_status_from_age(
                github_age_seconds,
                connected_max=github_rules["connected_max_age_seconds"],
                degraded_max=github_rules["degraded_max_age_seconds"],
            ),
            last_sync=github_last_sync,
            pending_actions=0,
            auth_status_summary=github_rules["source"],
        )
    )
    for item in public_systems:
        if str(item.get("name") or "") == "GitHub":
            continue
        connectors.append(
            normalize_connector_snapshot(
                name=str(item.get("name") or "connector"),
                status=str(item.get("status") or "connected"),
                last_sync=str(manager_state.get("updated_at") or ""),
                pending_actions=0,
                auth_status_summary="summary only",
            )
        )

    failed_tasks = _sort_items([task for task in tasks.values() if task["state"] == "failed"], "timestamp")
    completed_tasks = _sort_items([task for task in tasks.values() if task["state"] == "completed"], "timestamp")
    blocked_tasks = _sort_items([task for task in tasks.values() if task["state"] == "blocked"], "timestamp")
    pending_approvals = _sort_items([item for item in approvals.values() if item["status"] == "pending"], "requested_at")
    degraded_connectors = [item for item in connectors if item.get("status") in {"degraded", "error", "offline"}]
    degraded_connectors.sort(key=lambda item: (0 if item.get("status") in {"error", "offline"} else 1, str(item.get("last_sync") or "")))

    secondary_activity = []
    for event in _sort_items(events, "timestamp"):
        event_type = str(event.get("event_type") or "")
        if event_type not in INTERNAL_ACTIVITY_EVENT_TYPES:
            continue
        secondary_activity.append({
            "task_id": event.get("task_id") or "",
            "approval_status": event.get("approval_status") or "",
            "approval_signal_kind": str((event.get("raw_payload") or {}).get("approval_signal_source") or ""),
            **event,
        })

    summary = _build_summary(
        manager_state=manager_state,
        normalized_agents=normalized_agents,
        tasks=tasks,
        approvals=approvals,
        alerts=alerts,
    )

    public_feed = []
    public_candidates = [
        event for event in events
        if str(event.get("source") or "") in PUBLIC_EVENT_SOURCES
        and str(event.get("event_type") or "") in PUBLIC_EVENT_TYPES
    ]
    for event in sorted(public_candidates, key=lambda item: (EVENT_PRIORITY.get(item["event_type"], 99), item["timestamp"]))[:12]:
        public_feed.append({
            "agent": event.get("agent_id") or "office",
            "status_label": normalize_public_status_label(event.get("state")),
            "summary": normalize_public_summary_text(sanitize_public_detail(event.get("display_summary") or "")),
            "updated_at": event.get("timestamp"),
            "event_type": event.get("event_type"),
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "workspace_id": WORKSPACE_ID,
        "transport": "polling-ready / sse-ready shape",
        "generated_at": now.isoformat(),
        "summary": summary,
        "events": events,
        "tasks": list(tasks.values()),
        "blocked_tasks": blocked_tasks,
        "pending_approvals": pending_approvals,
        "failed_tasks": failed_tasks,
        "completed_tasks": completed_tasks[:8],
        "degraded_connectors": degraded_connectors,
        "connectors": connectors,
        "alerts": alerts[:8],
        "secondary_activity": secondary_activity[:10],
        "public_feed": public_feed,
        "event_policy": {
            "retention": EVENT_HISTORY_RETENTION,
            "api_limit": INTERNAL_EVENTS_RESPONSE_LIMIT,
            "ordering": "timestamp desc",
            "since_rule": "strictly greater than since timestamp",
            "dedupe_rule": "actual beats derived, derived beats backfilled for matching lifecycle keys",
        },
        "connector_health_rules": CONNECTOR_HEALTH_RULES,
        "approval_lifecycle_rules": APPROVAL_LIFECYCLE_RULES,
        "source_catalog": source_catalog,
        "primary_state": {
            "state": normalize_public_state(primary_state.get("state")),
            "detail": str(primary_state.get("detail") or ""),
            "updated_at": str(primary_state.get("updated_at") or ""),
        },
        "normalized_agents": normalized_agents,
    }


def build_public_state_payload(
    *,
    office_name: str,
    public_office_info: dict,
    public_systems: list[dict],
    manager_state: dict,
    role_definitions: dict,
    bridge: dict,
    sanitize_public_detail,
) -> dict:
    now_iso = bridge.get("generated_at") or datetime.now().isoformat()
    roles = manager_state.get("roles") or {}
    agents = [{
        "key": "reception",
        "name": "Reception AI",
        "role": "Gateway",
        "description": "Public AI Gateway that routes inbound work to dedicated internal roles.",
        "state": "idle",
        "status_label": normalize_public_status_label(manager_state.get("gateway", {}).get("status", "待機中"), context="gateway"),
        "detail": normalize_public_summary_text(sanitize_public_detail(manager_state.get("gateway", {}).get("detail", ""))),
        "updated_at": str(manager_state.get("gateway", {}).get("updated_at", now_iso)),
    }]
    for role_key in ("dev", "research", "ops"):
        role_state = roles.get(role_key) or {}
        public_state = normalize_public_state(role_state.get("state"))
        agents.append({
            "key": role_key,
            "name": role_state.get("name", role_definitions.get(role_key, {}).get("name", role_key)),
            "role": role_state.get("role", role_definitions.get(role_key, {}).get("role", role_key)),
            "description": role_state.get("profile", role_definitions.get(role_key, {}).get("profile", "")),
            "state": public_state,
            "status_label": normalize_public_status_label(role_state.get("public_status_label") or public_state),
            "detail": normalize_public_summary_text(sanitize_public_detail(role_state.get("detail", ""))),
            "updated_at": str(role_state.get("updated_at", now_iso)),
        })

    activity = []
    for item in list(bridge.get("public_feed") or [])[:6]:
        agent_key = item.get("agent")
        status_label = normalize_public_status_label(item.get("status_label"))
        activity.append({
            "agent": role_definitions.get(agent_key, {}).get("name", item.get("agent") or "AI Office"),
            "state": normalize_public_state(item.get("status_label")),
            "status_label": status_label,
            "summary": normalize_public_summary_text(sanitize_public_detail(item.get("summary") or "")),
            "updated_at": item.get("updated_at") or now_iso,
            "event_type": item.get("event_type") or "",
        })
    deduped = []
    seen = set()
    for item in activity:
        key = (item["agent"], item["event_type"], item["summary"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    activity = deduped[:6]

    recent_work = [{
        "title": f"{entry['agent']} · {entry['status_label']}",
        "summary": entry["summary"],
        "updated_at": entry["updated_at"],
    } for entry in activity[:4]]

    latest_update = manager_state.get("updated_at") or now_iso
    health = {
        "status": normalize_public_health_status(bridge.get("summary", {}).get("status", "normal")),
        "agent_count": len([key for key in roles if key in role_definitions]),
        "latest_update": latest_update,
        "public_log_policy": "public-safe only",
        "manager_mode": "manager-first",
    }
    intake = [{
        "id": item.get("id"),
        "role": item.get("role"),
        "summary": normalize_public_summary_text(sanitize_public_detail(item.get("summary") or "")),
        "updated_at": item.get("updated_at"),
    } for item in list(manager_state.get("intake") or [])[:4]]
    return build_public_state_contract(
        office={
            "name": office_name,
            "subtitle": public_office_info["subtitle"],
            "human_host": public_office_info["human_host"],
            "gateway_host": public_office_info["gateway_host"],
        },
        gateway={
            "label": public_office_info["gateway_label"],
            "status": normalize_public_status_label(manager_state.get("gateway", {}).get("status", "待機中"), context="gateway"),
            "detail": normalize_public_summary_text(sanitize_public_detail(manager_state.get("gateway", {}).get("detail", ""))),
            "updated_at": manager_state.get("gateway", {}).get("updated_at", now_iso),
        },
        agents=agents,
        activity=activity,
        recent_work=recent_work,
        systems=list(public_systems or []),
        health=health,
        summary=bridge.get("summary", {}),
        intake=intake,
        transport=bridge.get("transport") or "polling",
    )


def build_internal_state_payload(
    *,
    office_name: str,
    public_office_info: dict,
    manager_state: dict,
    role_definitions: dict,
    universal_fallback: dict,
    bridge: dict,
    assets_snapshot: dict,
) -> dict:
    now_iso = bridge.get("generated_at") or datetime.now().isoformat()
    roles = manager_state.get("roles") or {}
    role_cards = []
    for role_key in ("dev", "ops", "research"):
        role_state = roles.get(role_key) or {}
        role_cards.append({
            "key": role_key,
            "name": role_state.get("name", role_definitions.get(role_key, {}).get("name", role_key)),
            "role": role_state.get("role", role_definitions.get(role_key, {}).get("role", role_key)),
            "profile": role_state.get("profile", role_definitions.get(role_key, {}).get("profile", "")),
            "state": normalize_internal_state(role_state.get("state")),
            "public_status_label": role_state.get("public_status_label") or normalize_public_state(role_state.get("state")),
            "detail": str(role_state.get("detail") or ""),
            "updated_at": str(role_state.get("updated_at") or now_iso),
            "allowed_tools": list(role_state.get("allowed_tools") or role_definitions.get(role_key, {}).get("allowed_tools") or []),
            "last_event_type": role_state.get("last_event_type"),
            "last_source": role_state.get("last_source"),
        })
    fallback = manager_state.get("fallback_worker", {}) or {}
    role_cards.append({
        "key": "main",
        "name": fallback.get("name", universal_fallback["name"]),
        "role": fallback.get("role", universal_fallback["role"]),
        "profile": fallback.get("profile", universal_fallback["profile"]),
        "state": "active" if fallback.get("status") == "active" else "idle",
        "public_status_label": "Fallback active" if fallback.get("status") == "active" else "Fallback ready",
        "detail": "Receives work only when explicit role routing does not match or a routed worker fails.",
        "updated_at": str(fallback.get("updated_at") or manager_state.get("updated_at") or now_iso),
        "allowed_tools": list(fallback.get("allowed_tools") or universal_fallback.get("allowed_tools") or []),
        "last_event_type": fallback.get("last_event_type"),
        "last_source": fallback.get("last_source"),
    })

    internal_agents = [{
        "agentId": agent["agent_id"],
        "name": agent["name"],
        "isMain": agent["is_main"],
        "state": agent["state"],
        "area": agent["area"],
        "source": agent["source"],
        "authStatus": agent["auth_status"],
        "updated_at": agent["updated_at"],
        "lastPushAt": agent["last_push_at"],
    } for agent in bridge.get("normalized_agents", [])]

    activity = []
    for event in list(bridge.get("secondary_activity") or [])[:8]:
        role_key = event.get("agent_id")
        activity.append({
            "role": role_key,
            "agent": role_definitions.get(role_key, {}).get("name", universal_fallback["name"]),
            "event_type": event.get("event_type"),
            "source": event.get("source"),
            "state": event.get("state"),
            "summary": event.get("display_summary"),
            "updated_at": event.get("timestamp"),
            "route_reason": (event.get("raw_payload") or {}).get("route_reason"),
            "provenance": event.get("provenance"),
            "provenance_label": event.get("provenance_label"),
        })

    return build_internal_state_contract(
        office={
            "name": office_name,
            "mode": "internal-control-view",
            "public_host": public_office_info["human_host"],
            "gateway_host": public_office_info["gateway_host"],
        },
        manager={
            "updated_at": manager_state.get("updated_at") or now_iso,
            "gateway": manager_state.get("gateway", {}),
            "routing": manager_state.get("routing", {}),
            "fallback_worker": manager_state.get("fallback_worker", {}),
        },
        summary=bridge.get("summary", {}),
        roles=role_cards,
        agents=internal_agents,
        activity=activity,
        blocked=bridge.get("blocked_tasks", []),
        approvals=bridge.get("pending_approvals", []),
        failed=bridge.get("failed_tasks", []),
        connectors=bridge.get("degraded_connectors", []),
        completed=bridge.get("completed_tasks", []),
        alerts=bridge.get("alerts", []),
        events=list(bridge.get("events") or [])[:INTERNAL_EVENTS_RESPONSE_LIMIT],
        intake=list(manager_state.get("intake") or [])[:6],
        assets=assets_snapshot,
        policies={
            "public_state_source": "bridge-normalized manager state",
            "public_view_policy": "read-only public-safe",
            "internal_view_policy": "internal-only operational snapshot",
            "event_policy": bridge.get("event_policy", {}),
            "approval_lifecycle_rules": bridge.get("approval_lifecycle_rules", {}),
            "connector_health_rules": bridge.get("connector_health_rules", {}),
            "source_catalog": bridge.get("source_catalog", {}),
        },
        transport=bridge.get("transport") or "polling",
    )
