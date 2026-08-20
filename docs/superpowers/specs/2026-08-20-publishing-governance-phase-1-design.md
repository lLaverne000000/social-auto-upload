# Publishing Governance Phase 1 Design

## Objective

Lower accidental account-risk exposure for Douyin and Xiaohongshu without adding evasion or anti-detection behavior.

## Approved changes

1. Publishing is allowed only through `sau_cli`; legacy Web/batch and unofficial XHS signing entry points fail closed.
2. Douyin video declaration and Xiaohongshu video/note content source are explicit operator choices. Missing choices stop before browser upload. Douyin note publishing had no automatic declaration and is outside this declaration change.
3. Each account uses one persistent browser profile. Upload performs one cookie validation, and visible risk prompts stop the flow at navigation, upload, editing, submission, and success confirmation.

## Compatibility

Login/check commands remain available. Existing cookie JSON is imported into the persistent profile once and continues to be refreshed for compatibility. No browser fingerprint spoofing, challenge bypass, or retry-on-risk behavior is introduced.
