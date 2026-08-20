# Publishing Governance Phase 3A Implementation Plan

**Goal:** Add bounded, concurrency-safe local audit logs, sanitized failure
evidence, and a read-only safety status command without changing publishing
policy or successful platform interaction.

**Architecture:** Keep persistence and status inspection in
`utils/risk_control.py`. `PublishGuard` owns audit/evidence writes, while
`sau_cli.py` only resolves the selected account, requests a read-only snapshot,
and formats it. Upload wrappers provide their high-level operation and the last
known page URL after an exception.

## Task 1: Lock behavior with failing tests

- [x] Add tests for size rotation, backup retention, JSONL validity, and secure
  permissions.
- [x] Add tests for metadata-only failure evidence and URL/reason sanitization.
- [x] Add tests for missing, healthy, locked, and corrupt safety state snapshots.
- [x] Add parser/dispatch tests for human and JSON safety status output and prove
  that missing-state inspection creates no files.
- [x] Run the focused tests and confirm they fail for the missing behavior.

## Task 2: Implement the risk-control persistence layer

- [x] Add a dedicated audit advisory lock and bounded rotation.
- [x] Add sanitized, account-scoped failure evidence.
- [x] Add a side-effect-free safety status reader.
- [x] Run focused risk-control tests.

## Task 3: Wire the CLI and uploader context

- [x] Add `sau safety status` and `--json` output.
- [x] Pass upload operation names and last known page URLs into failure evidence.
- [x] Run CLI and governance tests.

## Task 4: Document and verify

- [x] Document paths, retention, evidence contents, and status usage.
- [x] Run all unit tests and compile checks.
- [x] Run CLI help smoke tests and `git diff --check`.
