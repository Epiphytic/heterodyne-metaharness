# Plan8 integration — actual round 8 dispositions

The actual medium review approved the source-only PoC merge and retained activation prerequisites. This repair fixes only the maintenance-hold integration defect. No deployment, activation, live acceptance or waiver is claimed.

1. **FIXED** — harness_advance returns before advancing during maintenance. Actual pre-fix regression fails and passes with this guard; ordinary ticks remain enabled.

2. **OPEN UNWAIVED — BEFORE ACTIVATION** — EffectStore initialization/installation integration remains a controller-assessment prerequisite. This repair does not initialize any store or activate systemd ownership.

3. **OPEN UNWAIVED — BEFORE ACTIVATION** — Shared alert lifetime/capacity and retained harness alerts require controller assessment. No retention/alert semantics changed here.

4. **OPEN UNWAIVED** — Interrupted copy cycle failure/retry behavior unchanged.

5. **OPEN UNWAIVED** — Cancelled queued-job running-origin bookkeeping unchanged.

6. **OPEN UNWAIVED** — Cadence range diagnostic finding retained for assessment; no settings changes in this repair.

7. **OPEN UNWAIVED** — Health UID/effective-UID consistency finding unchanged.

8. **OPEN UNWAIVED** — Repeated retained-backup verification cost unchanged.

9. **ROOT-OWNED PENDING SOURCE COMMIT** — Merged source remains uncommitted for controller inspection/signing; no worker commit or history reset.

The exact previously reviewed seven-file CI repair is adopted by patch SHA d05c813736c72fa1425924f7025c64c10a597191e0ce751f4e133623ff4f7cf6; all seven resulting file hashes match its adoption manifest. The watchdog diagnoses bounded shutdown failure and never converts a timeout to success; it is not a claim to have identified the hosted shutdown root cause. Historical review ordinals remain unchanged; next controller review is round 9.
