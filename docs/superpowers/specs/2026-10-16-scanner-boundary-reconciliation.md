# Scanner boundary: retained design and current-source reconciliation

Status: planning publication for btq-18sfj, 2026-10-16. No implementation or human
design approval is asserted. This document summarizes the existing frozen design;
it does not replace its bytes or reopen its exhausted three-round review.

The authoritative historical inputs are `design-scanner-boundary-d3.md`,
`plan-scanner-d3.r5.md`, and `ask-scanner-r5.md` in the durable Hermes handoff
`2026-10-15/tmp-docs`. Their complete hashes, earlier revisions, review reports,
and review briefs are pinned in
[the artifact manifest](../../reviews/scanner-boundary/frozen-artifacts.json).
The historical originals remain unchanged there. They contain host-specific
paths and are retained outside portable source rather than rewritten, encoded to
evade checks, or exempted from install-agnostic checks. The handoff must accompany
this publication when another controller assumes the work; hashes alone do not
make unavailable evidence usable.

## Existing design decision

Distinctive vendor prefixes match inside words; AWS access-key IDs also lose
their trailing boundary. The existing start-boundary rule for `sk-` remains,
with an additional embedded form requiring at least 32 body characters and a
digit, uppercase letter or underscore within a bounded prefix, or 256 lowercase
characters. JWT segments longer than 256 characters fail closed. Possessive or
bounded repetitions prevent the reviewed quadratic cases.

Redaction finds all secret, identifier and hex spans on the same original text,
then merges overlapping or touching spans. The first-starting span's marker wins;
ties retain the existing pattern order. The fixpoint loop remains necessary.
Scanner widening and this redactor change must land together: widening alone
introduces the historically demonstrated partial-JWT disclosure.

Accepted residuals remain exactly those in d3: embedded short or lowercase-only
`sk-` gaps, stated ordinary-name false positives, case-sensitive private-key
matching, and a separate install-check boundary fix. Rendered marker counts can
decrease and one control/hex case hides its visible escape prefix. No message
template or operator wording change is proposed.

## What the retained reviews establish

The design reports record REVISE, REVISE, APPROVE across cycles 1–3. Final d3
approval is a historical Codex review of the prior author's design, including
overlap, continuation, control/hex and scaling probes. It is not a new Opus
review or authenticated human decision.

The implementation-plan reports also record REVISE, REVISE, APPROVE across three
cycles. The final report explicitly approves only the r2-to-r4 delta. Frozen r5
subsequently fixes red-test failure classification and adds Appendix C, with
recorded author measurements and a stand-in shell audit. No independent final-r5
approval report is present in this retained set. Neither approval may be silently
extended to a different digest, and neither cycle may receive a fourth round.

## Current-source findings

The claimed planning checkout starts at
`2c0c5b5b775ee652d2ce32c192091aa2314e5b32`. Current merged main examined here is
`3aa124cd4c6e11343d8222cc7ac17d10bec98c34`.

The scanner, redactor and hook source files are unchanged between these commits.
Two target test files changed with the admind guard work: a membership-event
fixture now explicitly creates its intended hold, and a direct post fixture
supplies its owner. Both changes are unrelated to scanner behavior and must be
preserved. Both frozen Appendix B patches pass a dry application check against
temporary copies of exact current-main files. No patch was applied and no scanner
test or historical result was rerun as part of this planning publication.

Frozen r5 requires `50fc42fb95dde0cdf38410c54e4e89bc3227f37b` (plan5i parts 2/3)
as an ancestor. It is not an ancestor of current main. Its ask, notice and harness
consumer modules are absent on current main. Therefore clean patch application
does not satisfy the approved plan's integration prerequisite or establish its
historical test counts on today's source.

The continuation choices and authentic approval request are in
[the admission plan](../plans/2026-10-16-scanner-boundary-admission.md).
