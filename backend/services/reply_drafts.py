from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUNS_DIR = Path("/home/deepnoa/deepnoa-agent-runtime/runs")
MAX_REPLY_DRAFTS = 50

# draft-actions.json lives at the project root (two levels up from this file)
_PROJECT_ROOT = Path(__file__).parent.parent.parent
DRAFT_ACTIONS_FILE = _PROJECT_ROOT / "draft-actions.json"
_draft_lock = threading.Lock()

VALID_ACTIONS: dict[str, str] = {
    "approve": "approved",
    "reject": "rejected",
    "regenerate": "pending_regenerate",
}


def load_draft_actions() -> dict[str, dict[str, str]]:
    """Load persisted draft action states. Returns {} on missing file or parse error."""
    try:
        if DRAFT_ACTIONS_FILE.exists():
            data = json.loads(DRAFT_ACTIONS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_draft_action(task_id: str, status: str) -> None:
    """Persist an action state for task_id, replacing any previous value."""
    with _draft_lock:
        actions = load_draft_actions()
        actions[task_id] = {
            "status": status,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        DRAFT_ACTIONS_FILE.write_text(
            json.dumps(actions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


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
    """Load recent reply drafts, overlaying persisted action states."""
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

    drafts.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    if limit > 0:
        drafts = drafts[:limit]

    # Overlay persisted action states so UI-driven status survives page reloads
    actions = load_draft_actions()
    for draft in drafts:
        tid = draft.get("task_id", "")
        if tid and tid in actions:
            draft["status"] = actions[tid]["status"]

    return drafts
