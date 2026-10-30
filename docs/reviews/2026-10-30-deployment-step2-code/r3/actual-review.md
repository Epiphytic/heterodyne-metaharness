**Round 3 verdict: this is good enough to implement as a dormant PoC.** The review found no critical flaws at `76c07c0` against `eef8c8f`. Every earlier R1/R2 HIGH finding is either fixed or truthfully disclosed as held. The public opener and the protected producer/custody stay HOLD, and the code doesn't claim otherwise. The review re-ran the 92 focused Step 2 tests and they pass.

**What happened to the R1/R2 findings:**

| Finding | Status |
|---|---|
| R2-1 login dirs | **Fixed.** `_rendering` and `_check_card` use the fixed-profile `login_dirs` with `redact_with_dirs` (`deployment_authority.py:119-159`). This was checked by reading the code only: the tests replace `_rendering` with a stand-in. |
| R2-2 outer digest | **Partly fixed, acceptably held.** The card now shows a separate "Outer Packet" URL, and verifying the published bytes is disclosed as a runtime prerequisite. Medium gap #1 below. |
| R2-3 cancel at ordinal 0 | **Fixed** (`deployment_outage_journal.py:250`), but it accepts more cases than it should. Medium gap #2 below. |
| R2-4 helper/profile/sudo pins | **Fixed in source.** The packet pins all three hashes (`deployment_outage.py:53-55`) and root ownership is checked in both sweeps (`deployment_outage_authority.py:95-106`). |
| R2-5 chunk size | **Fixed, approximately.** The size check uses the real chunk settings and renderer, and the native single-part check still applies. |
| R2-6 Step 1 card change | **Disclosed** in the qualification doc and the ADR. |
| R2-7 journal write lock | **Fixed.** Journal opens are now read-only (`deployment_outage_journal.py:91-103`). |
| R2-8 any target accepted | **Fixed.** The installer is limited to the protected target mapping (`deployment_host_store.py:221`). |
| R2-9, R1-8, R1-9 duplication | Card-check copy consolidated; the remaining duplicates are disclosed. |
| R2-10 `of_bytes` | Removed, but see #3. |

**Medium issues (none open execution):**
1. **Outer URL can equal the inner one** (`deployment_outage_authority.py:31`). Nothing refuses an outer Packet URL that is the same as the inner Release URL, and the tests at `test_deployment_outage.py:125` and `:143` do exactly that. So the "distinct" claim in the qualification doc isn't enforced. Fix: refuse when the outer commit equals `release.packet_commit`.
2. **Not just cancel** (`deployment_outage_journal.py:250`). `fail_never_issued` accepts any recovery-hold at ordinal 0, including safe-stop and held-restore, not only cancel. It then records "failed-no-effect" and frees the target, which contradicts the `decode_recovery` docstring and the comment at `:221-222`. Fix: require that every recovery row for that execution is a cancel.
3. **Docstring no longer true** (`harness/digests.py:20`). It says `ComputedDigest` is only built through `of_manifest` or `of_fd`. This PR builds it directly from hex strings at `deployment_authority.py:173` and `:176` and `deployment_outage_authority.py:42`, which is a weaker guarantee than the removed `of_bytes` gave.

**Low issues:**
- **Size check uses a made-up ask row** (`deployment_authority.py:142`). If the real ask header is longer, the card could split into two parts after passing this check. The native single-part check would still refuse it, so the cost is a wasted approval.
- **Helper path written twice** (`deployment_outage_authority.py:95`). It duplicates `_ROOT_INSTALL_HELPER` from `deployment_host_store.py:28`, so the pinned file and the file sudo actually runs could drift apart.
- **Config reloaded on every card check** (`deployment_authority.py:137`). It happens about 4–5 times per outage binding. A config edit during that window could make the inner and outer checks see different profiles.
- **Sudoers policy not pinned** (`deployment_outage_authority.py:101`). `/etc/sudoers` and `/etc/sudoers.d` aren't pinned or ownership-checked. This is inside the held root-contract scope, but `sudo_sha256` shouldn't be read as covering what root access the executor is granted.

I'd fix #1 and #2 before final clearance because they're small and they close the gap between what the code says and what it does. The rest can be follow-ups. Nothing was edited, committed or posted, and the R1/R2 reports and qualification doc are untouched.
