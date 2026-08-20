# Publishing Governance Phase 3C Decisions

## Status

Accepted on 2026-08-20. This phase freezes product-policy decisions and does
not add runtime enforcement.

## Decisions

1. Do not add a daily publish-count limit.
2. Keep the current headed/manual-confirmation defaults, but do not make them
   mandatory. Explicit `--automatic-publish --headless` remains available.
3. Do not remove or disable `--automatic-publish`.
4. Keep the existing 30-minute default cooldown and user-supplied scheduling.
   Do not extend the cooldown or add permitted publishing time windows.

## Controls That Remain Mandatory

- Unified CLI publishing permit for Douyin and Xiaohongshu.
- One active publishing task per platform.
- Exact-content deduplication within the existing seven-day window.
- Risk-page, HTTP-error, and upload-deadline fail-closed behavior.
- Secure local state permissions, bounded audit logs, failure evidence, and
  confirmed-success receipts.

## Rationale

The mandatory controls stop duplicate clicks, concurrent tasks, abnormal page
states, and untraceable failures. The rejected controls would instead impose a
business publishing policy and reduce unattended-operation capability. They
remain out of scope unless the operator explicitly reopens this decision.

## Change Rule

Future changes to the four frozen decisions require an explicit operator
request and a separate design approval. They must not be introduced as a hidden
default, incidental refactor, or CI-only behavior.
