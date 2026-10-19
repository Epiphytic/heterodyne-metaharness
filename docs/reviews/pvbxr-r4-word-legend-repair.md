# PV R4 finding 1 repair

Actual R4 MEDIUM review accepted the offline PoC, while keeping producer word-legend spoofing as a hold before live use. This repair refuses producer lines that begin with any native accepted approval/denial word and contain a legend delimiter. It derives the word set directly from `verbs`, preserves native parsing, and does not sanitize a misleading line into an approval card.

Fresh regression: 34 failures and eight passes on the unchanged predecessor. After the repair, 42 new cases and 153 combined focused cases pass. The first RED attempt used the wrong editable environment and failed collection; that failed attempt is retained separately and is not behavioral evidence. All 257 previously tracked test files remain byte identical. Ruff and formatting pass. Quality delta against b024f37 reports three findings, zero gating: two new pytest functions reported as dead code and one minor literal-validator churn finding. All measured debt remains open; no quality reset or acknowledgment.

The initial type invocation omitted the interpreter override, producing missing-environment import cascades; it is retained, not represented as a source failure or configured check. The configured invocation is recorded separately.

Actual review history and all prior findings are retained. R4 finding 1 is repaired pending the next MEDIUM critical-flaws review. R4 findings 2–10 and all earlier unresolved findings remain OPEN. This does not implement the native queue child, enrollment, installation or deployment. No live authority is granted.
