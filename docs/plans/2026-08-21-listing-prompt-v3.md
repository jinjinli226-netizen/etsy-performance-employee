# Listing Prompt v3 Implementation Plan

1. Add failing tests for v3 rules, prompt wording, structured Specification validation, and v2 compatibility.
2. Add the new rule field to the safe rules allowlist and make prompt construction version-aware.
3. Add v3-only heading, unique Emoji, and labeled-bullet validation.
4. Bump the default job rules to `mvp-default-v3` and document the output contract.
5. Sync the installed Hermes skill, run focused and full verification, restart the backend, and verify the LAN service.
