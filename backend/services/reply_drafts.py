from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUNS_DIR = Path("/home/deepnoa/deepnoa-agent-runtime/runs")
MAX_REPLY_DRAFTS = 50


def _safe_iso_from_mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
    except Exception:
        return ""


def _normalize_draft(data: dict[str, Any], result_path: Path) -> dict[str, Any]:
    result_payload = data.get("result") if isinstance(data.get("result"), dict) else {}
    created_at = (
        str(data.get("ended_at") or "").strip()
        or str(data.get("started_at") or "").strip()
        or _safe_iso_from_mtime(result_path)
    )
    return {
        "task_id": str(data.get("task_id") or "").strip(),
        "role": str(data.get("role") or "").strip(),
        "status": str(data.get("status") or "").strip(),
        "exit_code": data.get("exit_code"),
        "created_at": created_at,
        "content": str(result_payload.get("content") or "").strip(),
    }


def load_reply_drafts(limit: int = MAX_REPLY_DRAFTS) -> list[dict[str, Any]]:
    if not RUNS_DIR.exists():
        return []

    drafts: list[dict[str, Any]] = []
    for result_path in RUNS_DIR.glob("*/result.json"):
        try:
            raw = result_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        result_payload = data.get("result")
        if not isinstance(result_payload, dict):
            continue
        if str(result_payload.get("type") or "").strip() != "reply_draft":
            continue
        normalized = _normalize_draft(data, result_path)
        if not normalized["content"]:
            continue
        drafts.append(normalized)

    # Sort latest first. ended_at > started_at > file mtime fallback was already normalized.
    drafts.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    if limit <= 0:
        return drafts
    return drafts[:limit]

