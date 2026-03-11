#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ROOT / "openclaw-role-prompts"
AGENTS_HOME = Path(os.environ.get("OPENCLAW_AGENTS_HOME", os.path.expanduser("~/.openclaw/agents")))


def main() -> int:
    for prompt_file in sorted(PROMPTS_DIR.glob("*.md")):
        if prompt_file.name.startswith("._"):
            continue
        role = prompt_file.stem
        target_dir = AGENTS_HOME / role / "agent"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "AGENTS.md"
        target.write_text(prompt_file.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"synced {role} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
