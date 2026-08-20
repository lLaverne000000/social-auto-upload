# Publishing Governance Phase 2 Design

## Scope

Add controls that stop abnormal or hung publishing tasks without changing successful normal publishing behavior.

## Design

1. Every upload wait loop has a monotonic hard deadline. Video upload gets 15 minutes; image/note upload gets 10 minutes. Expiry raises `RiskControlError` and never clicks publish.
2. Risk checks combine visible body text, login/verification URL redirects, and navigation HTTP status. Login redirects, verification pages, rate limits, 4xx/5xx responses, upload failures, account restrictions, and system-busy prompts fail closed.
3. Persistent browser profile directories use mode `0700`; cookie/storage-state files use mode `0600`. All Douyin/Xiaohongshu state writes go through one secure helper.
4. Publish locks record their owner PID. A live owner always blocks; a confirmed dead owner can be recovered immediately; malformed locks remain fail-closed until the existing six-hour stale threshold. Audit records include a unique task ID.
5. After a platform success URL is confirmed, the guard writes a local receipt containing task ID, platform, account, status, success URL, timestamp, and any work ID found in known URL query fields. No extra platform request is made solely for reconciliation.

## Non-goals

- No CAPTCHA solving, fingerprint spoofing, challenge bypass, proxy rotation, or retry-after-risk behavior.
- No change to successful publish content, normal form interactions, or existing 30-minute default cooldown.
- No claim that a success URL always exposes a platform work ID; missing IDs are recorded as requiring manual reconciliation.

## Verification

- Unit tests cover deadlines, risk URLs/statuses, secure file modes, live/dead PID locks, task IDs, and receipts.
- Existing uploader/CLI tests remain green.
- Compile, CLI help, Flask legacy-Web smoke, and `git diff --check` pass.
