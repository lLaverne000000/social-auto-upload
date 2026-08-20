# Publishing Governance Phase 3A Design

## Scope

Add local observability and bounded audit storage without changing successful
Douyin or Xiaohongshu publishing behavior.

## Design

1. Audit records remain JSON Lines at `.sau_safety/audit.jsonl`. A dedicated
   cross-platform advisory lock serializes append and rotation across platforms.
   Each record is written as one encoded line. The active log rotates before it
   exceeds 5 MiB, and five numbered backups are retained. Audit files and the
   lock use mode `0600` where POSIX modes apply.
2. Failed guarded publishes write one metadata-only JSON file beneath an
   account-specific `.sau_safety/evidence/` directory. The record contains the
   task ID, platform, account, operation/stage, timestamp, exception class,
   sanitized reason, and a URL with query and fragment removed. It never stores
   page text, cookies, storage state, request headers, or screenshots. Evidence
   directories use mode `0700` and files use mode `0600` where supported.
3. `sau safety status --platform PLATFORM --account ACCOUNT` reads local safety
   state without creating directories, launching a browser, contacting a
   platform, acquiring the publish lock, or changing any file. Human-readable
   output is the default; `--json` returns structured output. The status includes
   last success, cooldown estimate, recent dedup entry count, lock metadata,
   audit size/backups, and the latest failure evidence for the account.
4. Missing local state is a healthy empty state. Corrupt state is reported as a
   diagnostic condition rather than silently treated as safe.

## Compatibility

- The current 30-minute cooldown and 7-day exact-content dedup behavior remain
  unchanged.
- Existing `--automatic-publish`, headed/headless choices, publishing schedules,
  declarations, and upload flows remain unchanged.
- Existing success receipts remain unchanged.

## Non-goals

- No daily publish quota.
- No forced headed mode or forced manual confirmation.
- No removal of `--automatic-publish`.
- No cooldown extension or publishing time-window policy.
- No CAPTCHA solving, fingerprint spoofing, proxy rotation, challenge bypass,
  or other platform-control evasion.
- No Windows CI work in Phase 3A; that remains Phase 3B.

## Verification

- Unit tests cover rotation/retention, JSONL validity, file modes, evidence
  sanitization, corrupt/missing state, lock reporting, read-only behavior, CLI
  parsing, and JSON output.
- Existing focused uploader and governance tests remain green.
- Full unit tests, compile checks, CLI help, and `git diff --check` pass.
