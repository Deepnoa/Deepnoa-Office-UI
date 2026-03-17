from __future__ import annotations

from .schemas import WORKSPACE_ID, normalize_event_payload, normalize_provenance


def manager_activity_events(manager_state: dict) -> list[dict]:
    events = []
    for item in list(manager_state.get("activity") or []):
        payload = dict(item)
        payload.setdefault("agent_id", item.get("role"))
        payload["provenance"] = normalize_provenance(item.get("provenance") or "actual")
        events.append(normalize_event_payload(payload, workspace_id=WORKSPACE_ID))
    return events


def intake_events(intake: list[dict]) -> list[dict]:
    events = []
    for item in list(intake or []):
        payload = {
            "event_type": "channel.message.received",
            "timestamp": item.get("updated_at"),
            "workspace_id": WORKSPACE_ID,
            "source": "public",
            "agent_id": item.get("role"),
            "task_id": item.get("id"),
            "summary": item.get("summary"),
            "severity": "info",
            "provenance": "actual",
            "raw_item": item,
        }
        events.append(normalize_event_payload(payload, workspace_id=WORKSPACE_ID))
    return events


def snapshot_events(primary_state: dict, normalized_agents: list[dict]) -> list[dict]:
    events = []
    if primary_state:
        events.append(normalize_event_payload({
            "event_type": "agent.status.changed",
            "source": "state-file",
            "agent_id": "main",
            "state": primary_state.get("state"),
            "timestamp": primary_state.get("updated_at"),
            "detail": primary_state.get("detail"),
            "provenance": "derived",
        }, workspace_id=WORKSPACE_ID))
    for agent in normalized_agents:
        events.append(normalize_event_payload({
            "event_type": "agent.status.changed",
            "source": agent["source"] or "agents-state",
            "agent_id": agent["agent_id"],
            "state": agent["state"],
            "timestamp": agent["last_push_at"] or agent["updated_at"],
            "detail": agent["name"],
            "provenance": "derived",
        }, workspace_id=WORKSPACE_ID))
    return events


def derive_missing_lifecycle(events: list[dict]) -> list[dict]:
    by_task = {}
    for event in sorted(events, key=lambda item: str(item.get("timestamp") or "")):
        task_id = event.get("task_id") or ""
        if not task_id:
            continue
        by_task.setdefault(task_id, []).append(event)

    derived = []
    for task_id, task_events in by_task.items():
        types = {item.get("event_type") for item in task_events}
        first = task_events[0]
        if "task.started" in types and "task.created" not in types:
            derived.append(normalize_event_payload({
                "event_type": "task.created",
                "source": first.get("source"),
                "agent_id": first.get("agent_id"),
                "task_id": task_id,
                "summary": first.get("display_summary"),
                "timestamp": first.get("timestamp"),
                "state": "executing",
                "provenance": "backfilled",
            }, workspace_id=WORKSPACE_ID))
        if "task.started" in types and "task.assigned" not in types:
            derived.append(normalize_event_payload({
                "event_type": "task.assigned",
                "source": first.get("source"),
                "agent_id": first.get("agent_id"),
                "task_id": task_id,
                "summary": first.get("display_summary"),
                "timestamp": first.get("timestamp"),
                "state": "executing",
                "provenance": "backfilled",
            }, workspace_id=WORKSPACE_ID))
    return derived


def dedupe_events(events: list[dict]) -> list[dict]:
    ranked = sorted(
        list(events or []),
        key=lambda item: (
            str(item.get("timestamp") or ""),
            0 if item.get("provenance") == "actual" else 1 if item.get("provenance") == "derived" else 2,
        ),
        reverse=True,
    )
    seen = set()
    result = []
    for event in ranked:
        dedupe_key = (
            event.get("event_type"),
            event.get("agent_id"),
            event.get("task_id"),
            event.get("timestamp"),
            event.get("approval_status"),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        result.append(event)
    result.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    return result
