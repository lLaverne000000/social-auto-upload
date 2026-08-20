# Publishing Governance Phase 1 Implementation Plan

1. Add failing tests for CLI-only permits, disabled legacy paths, mandatory Douyin-video declaration, and mandatory Xiaohongshu source choices.
2. Add a private in-process publish permit issued by `sau_cli`, pass it into current uploaders, and reject direct/legacy publish calls.
3. Add deterministic persistent profile directories with one-time cookie import; remove the duplicate CLI setup check.
4. Add risk-prompt checks at navigation, upload wait, metadata, pre-submit, and post-submit stages.
5. Run unit tests, compile checks, CLI help smoke tests, and `git diff --check`.
