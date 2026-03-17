from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any


APPROVAL_REQUEST_STATUSES = {"approval-pending", "pending"}
APPROVAL_DECISION_TO_STATUS = {
    "allow-once": "approved",
    "allow-always": "approved",
    "approved": "approved",
    "approve": "approved",
    "deny": "rejected",
    "denied": "rejected",
    "rejected": "rejected",
    "reject": "rejected",
    "expired": "expired",
    "timeout": "expired",
    "timed_out": "expired",
    "timed-out": "expired",
    "pending": "pending",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _normalize_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return APPROVAL_DECISION_TO_STATUS.get(normalized, normalized)


def _json_blob(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _walk_records(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_records(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_records(item)


def _build_event(
    *,
    source: str,
    agent: str,
    task_id: str,
    event_type: str,
    state: str,
    detail: str,
    timestamp: str,
    approval_status: str = "",
    approval_id: str = "",
    signal_source: str = "",
) -> dict:
    payload = {
        "source": source,
        "role": agent,
        "event_type": event_type,
        "state": state,
        "detail": detail,
        "task_id": task_id,
        "provenance": "actual",
        "timestamp": timestamp,
    }
    if approval_status:
        payload["approval_status"] = approval_status
    if approval_id:
        payload["approval_id"] = approval_id
    if signal_source:
        payload["approval_signal_source"] = signal_source
    return payload


def _extract_structured_from_record(
    record: dict[str, Any],
    *,
    source: str,
    agent: str,
    task_id: str,
    signal_source: str,
) -> list[dict]:
    events: list[dict] = []
    approval_id = str(record.get("approvalId") or record.get("approval_id") or record.get("id") or "").strip()
    if approval_id and not approval_id.startswith("approval"):
        approval_id = approval_id if "approval" in approval_id else ""
    timestamp = str(record.get("timestamp") or record.get("updated_at") or iso_now())
    status = _normalize_status(record.get("status"))
    decision = _normalize_status(
        record.get("decision") or record.get("approvalDecision") or record.get("approval_status")
    )
    expires_at = record.get("expiresAtMs") or record.get("expires_at")

    if status in APPROVAL_REQUEST_STATUSES and approval_id:
        detail = f"Openclaw approval requested ({approval_id})."
        if expires_at:
            detail = f"Openclaw approval requested ({approval_id}) until {expires_at}."
        events.append(_build_event(
            source=source,
            agent=agent,
            task_id=task_id,
            event_type="approval.requested",
            state="awaiting_approval",
            detail=detail,
            timestamp=timestamp,
            approval_status="pending",
            approval_id=approval_id,
            signal_source=signal_source,
        ))

    if decision in {"approved", "rejected", "expired"}:
        events.append(_build_event(
            source=source,
            agent=agent,
            task_id=task_id,
            event_type="approval.resolved",
            state="idle" if decision == "approved" else "error",
            detail=f"Openclaw approval resolved: {decision}.",
            timestamp=timestamp,
            approval_status=decision,
            approval_id=approval_id,
            signal_source=signal_source,
        ))
    return events


def _session_dir(agent: str) -> str:
    openclaw_home = os.environ.get("OPENCLAW_HOME", os.path.expanduser("~/.openclaw"))
    return os.path.join(openclaw_home, "agents", agent, "sessions")


def _recent_session_files(agent: str, started_at: float) -> list[str]:
    session_dir = _session_dir(agent)
    if not os.path.isdir(session_dir):
        return []
    files: list[str] = []
    for name in os.listdir(session_dir):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(session_dir, name)
        try:
            if os.path.getmtime(path) + 1 < started_at:
                continue
        except OSError:
            continue
        files.append(path)
    files.sort(key=lambda item: os.path.getmtime(item), reverse=True)
    return files[:3]


def _extract_structured_from_transcripts(
    *,
    agent: str,
    source: str,
    task_id: str,
    started_at: float,
) -> list[dict]:
    events: list[dict] = []
    for path in _recent_session_files(agent, started_at):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    message = entry.get("message") or {}
                    if entry.get("type") != "message" or message.get("role") != "toolResult":
                        continue
                    details = message.get("details")
                    if not isinstance(details, dict):
                        continue
                    timestamp = str(entry.get("timestamp") or message.get("timestamp") or iso_now())
                    for event in _extract_structured_from_record(
                        {**details, "timestamp": timestamp},
                        source=source,
                        agent=agent,
                        task_id=task_id,
                        signal_source="session.tool_result.details",
                    ):
                        events.append(event)
        except Exception:
            continue
    return events


def _extract_structured_from_payload(
    payload: dict[str, Any],
    *,
    source: str,
    agent: str,
    task_id: str,
) -> list[dict]:
    events: list[dict] = []
    result = payload.get("result") or payload
    for record in _walk_records(result):
        events.extend(_extract_structured_from_record(
            record,
            source=source,
            agent=agent,
            task_id=task_id,
            signal_source="result.payload",
        ))
    return events


def _extract_fallback_events(
    payload: dict[str, Any],
    *,
    source: str,
    agent: str,
    task_id: str,
) -> list[dict]:
    result = payload.get("result") or {}
    meta = result.get("meta") or {}
    text_blob = _json_blob(result).lower()
    timestamp = str(meta.get("timestamp") or iso_now())
    events: list[dict] = []
    if "approval required" in text_blob or "approval requested" in text_blob:
        events.append(_build_event(
            source=source,
            agent=agent,
            task_id=task_id,
            event_type="approval.requested",
            state="awaiting_approval",
            detail="Openclaw approval requested (fallback text match).",
            timestamp=timestamp,
            approval_status="pending",
            signal_source="fallback.text",
        ))
    for token in ("approved", "rejected", "expired"):
        if token in text_blob:
            events.append(_build_event(
                source=source,
                agent=agent,
                task_id=task_id,
                event_type="approval.resolved",
                state="idle" if token == "approved" else "error",
                detail=f"Openclaw approval resolved: {token} (fallback text match).",
                timestamp=timestamp,
                approval_status=token,
                signal_source="fallback.text",
            ))
            break
    return events


def _dedupe(events: list[dict]) -> list[dict]:
    priority = {
        "session.tool_result.details": 0,
        "result.payload": 1,
        "fallback.text": 2,
    }
    ranked = sorted(
        events,
        key=lambda item: (
            str(item.get("timestamp") or ""),
            priority.get(str(item.get("approval_signal_source") or ""), 9),
        ),
    )
    seen = set()
    result: list[dict] = []
    for event in ranked:
        key = (
            event.get("event_type"),
            event.get("task_id"),
            event.get("approval_status"),
            event.get("approval_id"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result


def extract_approval_events(
    payload: dict[str, Any],
    *,
    source: str,
    agent: str,
    task_id: str,
    started_at: float,
) -> list[dict]:
    structured = _extract_structured_from_payload(payload, source=source, agent=agent, task_id=task_id)
    structured.extend(
        _extract_structured_from_transcripts(
            agent=agent,
            source=source,
            task_id=task_id,
            started_at=started_at,
        )
    )
    structured = _dedupe(structured)
    if structured:
        has_request = any(item.get("event_type") == "approval.requested" for item in structured)
        has_resolution = any(item.get("event_type") == "approval.resolved" for item in structured)
        if has_request and has_resolution:
            return structured
    fallback = _extract_fallback_events(payload, source=source, agent=agent, task_id=task_id)
    return _dedupe(structured + fallback)
