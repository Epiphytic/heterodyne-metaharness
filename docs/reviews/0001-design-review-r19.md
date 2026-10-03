# ADR 0001 design review r19 (revision 13, round 2)

Reviewer: gpt-6.1-sol (Codex, read-only, reasoning high). Author: claude-opus-5-5. Mode: cross-model. Reviewed: `design..9306c03`.

Round-1 findings on ingress, redaction, batch provenance, fallback cadence, and rendering are resolved. Membership recovery remains blocking.

- **BLOCKING — [docs/adr/0001-workstreams-v2.md:486](../adr/0001-workstreams-v2.md:486):** Startup still treats the target count as proof that the intended change completed. Example: journal “remove Alice,” crash before mutation, then Bob leaves. Recovery sees `to` and commits, although Alice remains. This can bless an unauthorized change; the documented count-preserving residual risk does not cover it. Require recovery to account for membership events before releasing dispatch/posting and confirm the intended operation completed. When that cannot be established, latch for host recovery rather than trusting the count alone.

VERDICT: REJECT