# Verification and limits

Reviewed base: `2e918f5585a77ce12e55217f1a9630cc3e81deb7`. Source checkpoint: `edc57e1a30bedba0b1686eba76fc767b16345cff`. The cumulative eight-file patch SHA256 is `eb7fcc7b2e1ab266fb7655665e8fcbcaff180f833489cc75cd197ecf8e25042a`; the R2 repair delta SHA256 is `1f189809f4b89a2ca65c5f54048731ef87feedd51a0c85e97800dee090d48048`.

Final stationary candidate compatibility: **200 passed in 72.81s, one retained warning**. Pyright reported zero errors; scoped Ruff and normal commit hooks passed. Ten new predecessor controls failed at the intended owner/schema/unavailable/phase assertions. Initial focused failures and intermediate typing failures remain in private evidence. An earlier 200-pass run began before final source changes and is explicitly prefinal.

Three reviewer-requested prior-test migrations are recorded: remove the unused-field assertion while preserving bind-failure observable assertions; require the invalid recovery/custody phase to refuse; relax the 0.15-second timing threshold to one second while preserving abort/no-graceful-close checks. Of 44 prior top-level test definitions, 41 have exact ASTs. Eleven new parameterized cases were added.

**Later private composition**, not an input to the frozen R3 review: clean actual merge `a3a46693ef12f01962fc7fd5e569ff160b213299` joins public main `81398dbaf3b668e8375b46ec7d54834e66212e79` and the source checkpoint. All eight candidate files are byte-exact; the other 1,628 tracked main files are unchanged. The same 200-test selection plus the primary-child recovery module passed **272 tests in 73.09s, one warning**. No semantic merge resolution or source edit was needed.

The asyncio Unix transport destructor warning remains unexplained; it is not suppressed or claimed resolved. Cumulative quality remains non-green: **151 rows, 79 gates** against the original base (102 preexisting-worse,49 new-symbol). The prior 134-row/68-gate result remains historical evidence. Existing cli/ctl file-level formatter debt remains. Full GitHub CI and deployment are separate gates.

The status snapshot deliberately reports unavailable byte queue accounting, durable disposition completion and last error category. It does not infer durable completion from handler return. No reconnect mutator, group comparison or second backend is included. Cooperative client deadlines do not establish hard preemption. Installed runtime remains unchanged.
