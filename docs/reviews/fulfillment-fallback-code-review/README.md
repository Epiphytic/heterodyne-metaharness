# Fulfillment fallback: reviewed default-off source PoC

Actual FULL HIGH rounds 1 and 2 were REVISE. Actual THIRD MEDIUM used the required critical-flaws PoC question and returned **APPROVE as a PoC (default off), no critical flaws**. The actual Skill result, parent log, stream, start, configuration, result and inputs are retained. All F23–F28 are OPEN/unwaived; prior F1–F22 history and complete dispositions remain retained, including F14–F22 OPEN. This is source-only clearance for the reviewed default-off candidate, not activation, deployment, migration or native acceptance authority. No source or test was changed after this gate in this preparation.

`fulfillment.py` remains byte-identical with ENABLED=False and is excluded from the four changed-source snapshots. All five current hashes match the actual review result and focused test result. The runtime switch is unfinished **source-owned** future work under separate pending p5r9 approval; the review's “outside capability” wording does not outsource that gap. The rejected default-on variant remains historical evidence and is not merged by this packet.

## Supported behavior and damage limits

Guaranteed ordinary-posting fallback is limited to the known **trigger-compatible metadata/trigger damage** tested: missing trigger, malformed metadata or missing meta table with surviving triggers otherwise compatible. Counting becomes conservative; fresh fulfillment mutations hold and no schema is repaired. Exact already-applied receipt replay remains idempotent. Unmigrated and unsupported base schemas continue to refuse as specified.

Dropping fulfillment_generations, fulfillment_verifications or fulfillment_dirty leaves surviving triggers referencing missing tables. Posting fails with rollback; answers, ask updates and cancellation may also fail, **even with ENABLED=False**. No availability guarantee covers those cases. The daemon comment's broader wording remains F26 OPEN; this README narrows the claim without modifying reviewed source.

**Manual DDL restoration is unsafe:** restoring an exact trigger or metadata without rotating the verification epoch/dirtying receipts can resurrect old verification stamps despite intervening answers. No owned quiesced reset/recovery command exists. Damage recovery is a separately designed future operation; do not hand-write repair/drop-all SQL, infer fresh validation, or treat permanent conservative pending as recovered. F24/F25 remain OPEN. F23's shipped-False assertion gap, F27's stray-trigger/migration-validation gap and F28's Get/Propose/confirm gaps remain OPEN and unwaived. No post-gate fixes are claimed.

## Qualification and preserved failures

Root's exact final focused log/result proves **73 PASS in 18.59s**, all five source hashes unchanged. Historical broad post-hooks qualification proves **2775 PASS**, before the last eleven test additions and whitespace-only projection formatting; it is not claimed as a new broad run after those additions. Final native-path Ruff and four-file typing pass; projection/tests formatting passes. Existing daemon formatting debt remains OPEN. Final ripwire quality exit2 and its complete raw findings remain OPEN/unwaived (35 rows/16 gates in reviewer inputs); no quality waiver is inferred from review approval.

R1 false fixtures/shadowing and corrected behavioral RED/62-case result remain retained. R2 first eleven cases produced 6 FAIL/5 PASS due to an incorrect expectation that an exact already-applied receipt must refuse. A correction script failed before editing and the unchanged repeated attempt again produced 6 FAIL/5 PASS. Corrected actual eleven cases passed. No changed-operation receipt was fabricated. Root applied candidate-v2, caught a formatter-moved S608 suppression with native Ruff and returned it to the SQL line without runtime change; root-final-comment-correction.diff preserves that exact difference. No test was run preparing this documentation.

## Exact evidence and publication boundary

Evidence is deterministic gzip (mtime0), transported as ASCII base64 .gz.b64, with compressed and decompressed sizes/SHA256 in evidence-manifest.json. Readable reviews/dispositions normalize only the home prefix to <operator-home>; exact original private bytes remain losslessly encoded. The original partial packet/freeze is preserved separately, including its truthful then-pending MEDIUM3 statement. omissions.json enumerates every encountered excluded cache artifact, with size/hash/reason. No cache/compiled binary is silently shipped.

The explicit copier publishes documentation paths only and verifies each member before writing; it invokes no Git commands. Root alone owns source commits, publication and any later activation decisions. All review histories and original test/oracle evidence remain part of this packet.

Root's exact source review gate record is retained at evidence/x84mg-fallback-poc-source-review-gate-20261020.json.gz.b64; it confirms the actual gate and five unchanged hashes, and supplies no deployment authority.

## Lossless hook-compatible transport revision

The prior final packet was copied and staged by root, but the commit hook refused it: documentation source snapshots ending .py were linted as tests outside their original paths (S101), compressed binary bytes produced a false email finding, and readable normalization metadata contained a literal operator home path. No commit or reviewed source/test change resulted. The initial tool output was truncated. Root subsequently reproduced the real pre-commit failure and captured complete stdout/stderr/result; those exact artifacts are retained as encoded evidence under x84mg-fallback-hook-publication-failure. No reconstructed log is substituted. No hook is skipped or weakened by this transport revision.

Raw gzip evidence is now ASCII RFC4648 base64 in .gz.b64 files. For an individual artifact, run `base64 --decode artifact.gz.b64 > artifact.gz`, then `gzip --decompress --stdout artifact.gz` to obtain the exact original. encoded-evidence-manifest.json records encoded bytes/hash, decoded gzip bytes/hash, and original decompressed bytes/hash independently. Source snapshots are inert .py.txt files with byte-identical source bytes. Actual original source paths/hashes remain in source-manifest.json. Previous exact README/source/evidence manifests are also retained as encoded raw evidence.

This changes evidence transport only. The actual HIGH1/HIGH2/MED3 verdicts, all OPEN findings, original source/test hashes and source-only default-off limits are unchanged. Prior partial and final freezes remain untouched. Replacement-removals.json (external copier metadata) lists only obsolete .gz/.py documentation paths root must remove when replacing its staged documentation packet; no runtime/test path is removed.
