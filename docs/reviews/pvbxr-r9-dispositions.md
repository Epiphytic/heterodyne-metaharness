# Actual ninth review: offline PoC only

Actual Claude Opus built-in `/code-review`, MEDIUM, on signed source
`a3ad1574f8e00103331d87b7599409f308122648`. The first two reviews were full HIGH;
all actual later rounds and repairs remain retained. Exact requested PoC question
was used. Read the adjacent complete actual report and provenance.

The reviewer found no critical flaw in the narrow high-descriptor listener repair
or offline composition. This is no production/deployment approval, APP token,
full task acceptance, or independently rerun validation.

The signed repair replaces listener `select.select` with zero-timeout `poll`,
preserving held-on-error behavior. Actual real-listener descriptor1024/1100
predecessor run: 2 FAIL, 1 PASS. Candidate: 141 focused PASS. Broader hook series:
194 PASS and 1 baseline fixture failure reproduced on exact untouched predecessor.
Configured typing: 413 files, 0 errors, 0 warnings. All 264 existing test files
remain byte-exact. The first unconfigured typing attempt is retained separately.
New test formatting, Ruff and diff checks pass; hook baseline formatting and three
quality findings remain OPEN, unwaived. Stable combined admind verification is
running; no repaired full-suite pass is claimed here. Historical failing process
FD counts were not captured, so its descriptor accumulation remains a hypothesis.

## Findings retained in exact report order

1. OPEN: V1 receipt plus later V2 metadata has no supported protected-card recovery.
2. OPEN: answered V2 operation cards require explicit cancellation until native lifecycle exists.
3. OPEN / deployment blocker: old writers sharing unchanged DDL can suppress V2; retire/compatibility proof required.
4. OPEN: clearing metadata can restore a stale V1 suppression stamp.
5. OPEN: high-FD regression can skip with a low soft limit; actual recorded RED/green cases executed.
6. OPEN: fetch streaming has a separate high-FD select limitation outside this card/listener slice.
7. OPEN: historical FD accumulation/leak and bounded lifecycle cleanup not measured or repaired.
8. OPEN: preexisting pending fulfillment intent can remain after later V2 metadata.
9. OPEN: raw receipt reminder exclusion and separate list snapshots remain.
10. OPEN: zero-timeout readiness helper duplication remains.

All previous unresolved findings, native V2 lifecycle/reminder/cancellation/recovery
and independently pinned reader/older-writer deployment holds remain in force.
No database migration or old-writer compatibility waiver was introduced.
