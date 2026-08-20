# Publishing Governance Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fail-closed timeouts, stronger risk detection, secure state files, PID-aware locking, and local publish receipts without changing successful normal publishing.

**Architecture:** Extend the existing `utils/risk_control.py` and `utils/browser_profile.py` boundaries, then wire the small interfaces into Douyin/Xiaohongshu upload stages and the unified CLI. Platform uploaders expose only confirmed success URL state; `PublishGuard` owns audit and receipt persistence.

**Tech Stack:** Python 3.12, unittest, Patchright/Playwright, argparse.

**Spec:** `docs/superpowers/specs/2026-08-20-publishing-governance-phase-2-design.md`

## Global Constraints

- Video upload hard deadline: 900 seconds.
- Image/note upload hard deadline: 600 seconds.
- Browser profile directory mode: `0700`; cookie JSON mode: `0600`.
- No retry, bypass, or extra platform reconciliation request after a risk signal.

---

### Task 1: Deadline and risk-signal primitives

**Files:**
- Modify: `utils/risk_control.py`
- Modify: `tests/test_risk_control.py`

**Interfaces:**
- Produces: `StageDeadline(platform, stage, timeout_seconds, clock=None)`, `assert_healthy_navigation_response(response, platform, stage)`.
- Extends: `assert_no_risk_prompt(page, platform, stage)` with URL checks and additional phrases.

- [x] Write tests proving an expired deadline, verification URL, HTTP 429/5xx, upload-failure text, and normal page behavior.
- [x] Run the focused tests and verify the new cases fail for missing behavior.
- [x] Implement the minimal primitives and expanded signal table.
- [x] Run focused tests and verify they pass.

### Task 2: Secure browser state persistence

**Files:**
- Modify: `utils/browser_profile.py`
- Modify: `uploader/douyin_uploader/main.py`
- Modify: `uploader/xiaohongshu_uploader/main.py`
- Modify: `tests/test_publish_governance.py`

**Interfaces:**
- Produces: `save_secure_storage_state(context, account_file)`.
- Consumes: account cookie JSON paths already held by each uploader.

- [x] Write a test proving a new state file is mode `0600` after a real helper write.
- [x] Run the focused test and verify it fails before implementation.
- [x] Implement the helper and replace Douyin/Xiaohongshu direct `storage_state(path=...)` writes.
- [x] Run focused tests and verify they pass.

### Task 3: PID-aware locks, task IDs, and receipts

**Files:**
- Modify: `utils/risk_control.py`
- Modify: `sau_cli.py`
- Modify: `tests/test_risk_control.py`
- Modify: `tests/test_publish_governance.py`

**Interfaces:**
- Extends: `PublishGuard.mark_success(success_url="", work_id=None)`.
- Produces: `extract_work_id(url) -> str | None` and a receipt JSON under `.sau_safety/receipts/`.

- [x] Write tests proving live-PID rejection, dead-PID recovery, stable task ID audit linkage, receipt contents, and known query-field work-ID extraction.
- [x] Run focused tests and verify failures are caused by missing behavior.
- [x] Implement PID parsing/liveness, task IDs, receipt persistence, and URL extraction.
- [x] Wire CLI success-finally blocks to pass uploader success URLs into `mark_success`.
- [x] Run focused tests and verify they pass.

### Task 4: Wire stage deadlines and navigation checks

**Files:**
- Modify: `uploader/douyin_uploader/main.py`
- Modify: `uploader/xiaohongshu_uploader/main.py`
- Modify: `tests/test_douyin_declaration.py`
- Modify: `tests/test_xiaohongshu_uploader.py`

**Interfaces:**
- Consumes: `StageDeadline`, `assert_healthy_navigation_response`, and existing `assert_no_risk_prompt`.
- Produces: uploader fields `publish_success_url` and `publish_work_id` after confirmed success.

- [x] Add focused uploader tests proving deadline/risk exceptions occur before any publish click.
- [x] Run tests and verify they fail before wiring.
- [x] Add one deadline per upload operation, check it inside every unbounded wait loop, and validate navigation responses.
- [x] Capture confirmed success URL/work ID without making another platform request.
- [x] Run focused tests and verify they pass.

### Task 5: Documentation and full verification

**Files:**
- Modify: `README.md`
- Modify: `docs/CLI.md`

- [x] Document hard deadlines, profile/cookie locations and permissions, PID-lock recovery, and receipt paths.
- [x] Run `python -m compileall -q sau_cli.py sau_backend.py utils uploader myUtils tests`.
- [x] Run `python -m unittest discover -s tests` and require zero failures.
- [x] Run CLI help smoke checks, Flask legacy-Web tests, and `git diff --check`.
