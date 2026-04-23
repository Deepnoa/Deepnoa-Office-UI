# Deepnoa-Office-UI Runbook

Last Updated: 2026-04  
Status: Stable (post-cleanup)

---

## 1. Overview

This repository is in a stable, single-branch operational state.

- main is the single source of truth
- canonical working directory is fixed
- legacy/broken repositories are deprecated
- cron and runtime paths are unified

---

## 2. Canonical Repository

`/home/deepnoa/Deepnoa-Office-UI-light-polish`

### Rules
- All development MUST happen here
- Do NOT use other clones
- All services must run from this path

---

## 3. Branch Strategy

### Current
- main only

### Policy
- Use short-lived branches for development
- Merge via PR
- Delete after merge
- Preserve history via tags if needed

### Archive Tags
Examples:  
`archive/claude-run-detail-hierarchy`  
`archive/codex-merge-health-into-dashboard`

---

## 4. Local Server (Port 19000)

### Start
```bash
cd /home/deepnoa/Deepnoa-Office-UI-light-polish
python3 backend/app.py
```

### URL
`http://127.0.0.1:19000`

### Critical Rule
Never start the server from any other directory.

---

## 5. Cron (GitHub Sync)

### Current Configuration
```bash
* * * * * /home/deepnoa/Deepnoa-Office-UI-light-polish/scripts/sync_manager_sources.py >> /home/deepnoa/Deepnoa-Office-UI-light-polish/manager-sync.log 2>&1
```

### Behavior
- emits `connector.status.changed`
- does NOT emit task events

### Critical Rule
Do NOT reference old repository paths.

---

## 6. Deprecated Repository

`/home/deepnoa/Deepnoa-Office-UI_DEPRECATED`

### Status
- contains git corruption
- not safe for development or execution

### Policy
- do not run
- do not develop
- remove in future after confirmation

---

## 7. Data Model Notes (Important)

### /openclaw runs health
- read-only
- does NOT create runs

### /runs
- execution log
- increases only when actual execution happens

### Dashboard execution tasks
- sourced from `/api/internal/state`
- NOT the same as runs

---

## 8. Quick Check Commands（確認コマンド）

### Git 状態
```bash
git status
git branch -vv
git log -1
```

### Cron 確認
```bash
crontab -l
```

### サーバー確認（19000）
```bash
ps aux | grep 19000
lsof -i :19000
```

### 実行パス確認（重要）
```bash
pwd
```

---

## 9. 障害時の初動（3ステップ）

### Step 1: 実行パス確認
- サーバーが canonical repo から起動しているか確認
- 間違った checkout の可能性を排除

### Step 2: cron確認
- sync script が正しいパスを指しているか
- 旧 repo を参照していないか

### Step 3: state確認
- `/api/internal/state` を確認
- 異常な task 増加やイベント分類をチェック

---

## 10. Known Pitfalls

### A. Wrong Checkout Execution
Symptom
- UI outdated
- missing features

Cause
- running server from wrong directory

Fix
- use canonical path only

---

### B. Cron Re-contamination
Symptom
- active_tasks increasing continuously

Cause
- old sync script path

Fix
- ensure cron uses canonical repo

---

### C. Empty task_id Events
Symptom
- KPI inflation

Cause
- malformed task events

Fix
- fixed at source
- optional backend safeguard

---

## 11. Development Rules

- Do not push directly to main
- Use PRs
- 1 PR = 1 responsibility
- Separate:
  - frontend / backend
  - internal / public

---

## 12. Status Summary

The system is currently:

- repository: clean
- branches: simplified
- runtime: stable
- cron: fixed
- UI: consistent with main

👉 Ready for continued development

---

## 13. Optional Next Improvements

- backend safeguard for malformed task events
- dashboard UX improvements
- public UI refinement
- rename canonical repo directory (optional)

---

## 14. One-line Summary

Deepnoa-Office-UI is now operating in a clean, single-source, stable environment centered on main and one canonical path.

