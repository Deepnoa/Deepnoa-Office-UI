#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ROOT / "openclaw-role-prompts"
OPENCLAW_ROOT = Path(os.environ.get("OPENCLAW_ROOT", os.path.expanduser("~/openclaw")))
BUILD_CONFIG = ROOT / "scripts" / "build_verify_config.py"


def parse_prompt_meta(path: Path) -> dict:
    meta = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and line.split("=", 1)[0] in {"ROLE_ID", "VALIDATION_MARKER", "ESCALATE_TO"}:
            key, value = line.split("=", 1)
            meta[key] = value.strip()
    return meta


def run_role(role: str, config_path: Path) -> tuple[int, str]:
    cmd = [
        "env",
        f"OPENCLAW_CONFIG_PATH={config_path}",
        "node",
        str(OPENCLAW_ROOT / "dist" / "index.js"),
        "agent",
        "--local",
        "--agent",
        role,
        "--message",
        "Return only your validation marker. Output the marker text only.",
        "--json",
        "--timeout",
        "30",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout or proc.stderr


def extract_text(payload: str) -> str:
    data = json.loads(payload)
    blobs = data.get("payloads")
    if not isinstance(blobs, list):
        blobs = ((data.get("result") or {}).get("payloads") or [])
    text = "\n".join(str(item.get("text") or "") for item in blobs).strip()
    return text


def main() -> int:
    failures = 0
    build = subprocess.run(["python3", str(BUILD_CONFIG)], capture_output=True, text=True)
    if build.returncode != 0:
        print("[FAIL] could not build verification config")
        sys.stdout.write(build.stdout)
        sys.stderr.write(build.stderr)
        return 1
    config_path = Path(build.stdout.strip().splitlines()[-1])
    for prompt_file in sorted(PROMPTS_DIR.glob("*.md")):
        if prompt_file.name.startswith("._"):
            continue
        meta = parse_prompt_meta(prompt_file)
        role = meta["ROLE_ID"]
        expected = meta["VALIDATION_MARKER"]
        rc, output = run_role(role, config_path)
        if rc != 0:
            print(f"[FAIL] {role}: command failed")
            failures += 1
            continue
        try:
            text = extract_text(output)
        except Exception:
            print(f"[FAIL] {role}: invalid json output")
            failures += 1
            continue
        if expected not in text:
            print(f"[FAIL] {role}: expected marker `{expected}` not found in `{text}`")
            failures += 1
            continue
        print(f"[PASS] {role}: {text}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
