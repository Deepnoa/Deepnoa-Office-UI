#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ROOT / "openclaw-role-prompts"
VERIFY_ROOT = ROOT / ".role-verify"
WORKSPACES_DIR = VERIFY_ROOT / "workspaces"
CONFIG_PATH = VERIFY_ROOT / "openclaw.verify.json"
SOURCE_CONFIG = Path(os.environ.get("OPENCLAW_SOURCE_CONFIG", os.path.expanduser("~/.openclaw/openclaw.json")))
ROLES = ("dev", "ops", "research", "main")


def main() -> int:
    src = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
    VERIFY_ROOT.mkdir(parents=True, exist_ok=True)
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)

    agents = []
    for role in ROLES:
        prompt = (PROMPTS_DIR / f"{role}.md").read_text(encoding="utf-8")
        workspace = WORKSPACES_DIR / role
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "AGENTS.md").write_text(prompt + "\n", encoding="utf-8")
        agents.append({
            "id": role,
            "default": role == "main",
            "workspace": str(workspace),
            "tools": {"profile": "minimal"}
        })

    cfg = {
        "models": src.get("models", {}),
        "agents": {
            "defaults": {
                "model": ((src.get("agents") or {}).get("defaults") or {}).get("model", {"primary": "ollama/gpt-oss:20b"})
            },
            "list": agents
        }
    }
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(CONFIG_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
