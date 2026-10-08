# S7 spike review r2 (cycle 2 of 3)

Reviewer: gpt-6.1-sol (Codex, configured effort). Author: claude-opus-5-5. Reviewed commit: 99adfec.

R1 blockers:
- **1 resolved:** every offline run enforces network isolation.
- **2 resolved:** L1–L6 use restricted mounts exposing only the selected scratch state and credential.
- **3 resolved:** the launcher clears inherited authentication and endpoint variables with `env -i`.
- **4 resolved:** retained expired-token evidence supports seven blocked refresh attempts; successful rotation and persistence remain unknown.

1. [BLOCKING] `docs/spikes/s7-accounts-and-usage.md:113–114,187`: capability (d)’s enable condition calls L2 a “host-side read,” but that command runs through `sandbox.sh` inside bubblewrap. ADR r14 §4.4 D3/D9 requires the trusted read outside every sandbox, under the D8 lock. L6 also uses bubblewrap, so passing these commands would not demonstrate the required execution path. **Fix:** retain these as isolated client tests and require a separate host-side read using only the dedicated scratch login, under the credential lock, before enabling (d).

REVISE