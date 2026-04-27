from __future__ import annotations

import json
import os
from collections import OrderedDict
from typing import Any

LOG_PATH = "/home/deepnoa/openclaw/runs/runtime-events.jsonl"
MAX_RUNTIME_EVENTS = 100


def _normalize_event(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": str(raw.get("timestamp") or ""),
        "component": str(raw.get("component") or ""),
        "event_type": str(raw.get("event_type") or ""),
        "task_id": str(raw.get("task_id") or ""),
        "role": str(raw.get("role") or ""),
        "status": str(raw.get("status") or ""),
        "exit_code": raw.get("exit_code"),
        "runtime_status": raw.get("runtime_status"),
        "route_reason": raw.get("route_reason"),
    }


def load_runtime_events(limit: int = MAX_RUNTIME_EVENTS) -> list[dict[str, Any]]:
    if not os.path.exists(LOG_PATH):
        return []

    rows: list[dict[str, Any]] = []
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except Exception:
                    continue
                if isinstance(raw, dict):
                    rows.append(_normalize_event(raw))
    except Exception:
        return []

    if limit <= 0:
        return rows
    return rows[-limit:]


def build_runtime_task_summary(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_by_task: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for event in events:
        task_id = str(event.get("task_id") or "").strip()
        if not task_id:
            continue
        latest_by_task[task_id] = {
            "task_id": task_id,
            "role": event.get("role") or "",
            "status": event.get("status") or "",
            "runtime_status": event.get("runtime_status"),
            "exit_code": event.get("exit_code"),
            "last_event_type": event.get("event_type") or "",
            "updated_at": event.get("timestamp") or "",
        }
    return list(reversed(list(latest_by_task.values())))
