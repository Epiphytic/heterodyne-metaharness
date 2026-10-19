# Finish-health registration repair

The real composed recovery test exposed registration reading an ordinary operation
record even for finish-health, whose actual target is backup.json. Registration
now validates the exact backup owner and backup/failure/enrollment/source hashes
and renders truthful backup-owner provenance. Ordinary operation actor validation
and the final queue EX/CAS settlement path are unchanged. Registration grants no
consent or native effect.

Twenty new offline tests pass. The unchanged mandatory health oracle and its other
14 composed cases pass against an explicit private candidate source export and
accepted queue46fd58. The full named recovery family passes132 cases. The old
null-root fixture originally failed because it supplied arbitrary backup hashes
without backup/failure files; its setup alone now uses a real health target. All
original delivery/consent/permit/certificate/generation assertions remain, with an
explicit null-root assertion added. Original failing evidence and original fixture
bytes are retained privately; no test is skipped.

Full configured typing has zero errors; Ruff, formatting and diff checks pass.
Measured quality debt (churn and Service verbosity) remains OPEN, UNWAIVED. All
eight earlier R5 composition findings retain their recorded activation blockers
and limitations. This repair is source-only, not a deployment, live consent,
old-writer retirement proof or operational release acceptance. The next review
uses the operator's critical-flaws/PoC wording and medium reasoning.
