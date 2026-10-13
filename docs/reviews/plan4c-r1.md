Reviewer: gpt-6.1-sol (Codex, -s workspace-write). Author: claude-opus-5-5. Mode: cross-model. Reviewed commit: f1bb680f7e5a554338c0f9c68e852cf0b5bb48c4. Verdict: REVISE.

Note: cycle 1 of 3. Fixed in 1224a26.

Reviewed plan commit `f1bb680`, ADR `93d0e90`, plan 4b `e47dbf5`, and product code `8efed52`. Offline CLI checks confirmed Codex 0.160.0, Claude Code 2.1.286, and OpenShell 0.1.2. No repository files were changed; no queue, Beads, credential, or live-test operations were performed.

The plan assigns tasks to the main r16 requirements, but several tasks do not yet establish the guarantees they claim.

1. **[BLOCKING] E20 can publish unapproved bytes.**  
   After rehash and the unchanged-ctime check, publication changes permissions and takes a fresh record, accepting changes to mode and ctime. A same-size rewrite during that interval changes only fields the comparison permits. I reproduced this offline: the comparison accepted the rewrite although the content hash differed.

   **Fix:** Normalize permissions before generating the staged manifest. At fallback publication, perform the final record → rehash → unchanged-record check after all chmod operations, retaining that verified baseline. Add a negative control that rewrites between rehash and chmod. Also specify how publication handles the root’s rename-induced ctime change without resetting file baselines.

2. **[BLOCKING] Staging and publication disagree about manifest modes.**  
   T3 hashes the staged tree; T4 subsequently changes directories to 0555 and files to 0444/0555. The manifest records modes, and `check_fast` compares them. The plan neither normalizes modes before manifest generation nor explains how the published tree retains the approved manifest.

   **Fix:** Set final entry modes before creating the manifest, and publish without changing those modes. Test that the approved manifest digest remains unchanged and that the published tree immediately passes `check_fast`.

3. **[BLOCKING] Private, non-recursive OpenShell binds need a feasibility gate.**  
   T8 says the OpenShell *policy* writes these options. Product code actually emits mounts through `driver_config`, whose mount objects currently contain only `type`, `source`, `target`, and `read_only`. T1 does not establish which fields OpenShell 0.1.2 accepts or whether its Podman driver preserves the required semantics. A scripted `mountinfo` test cannot prove host submounts are excluded.

   **Fix:** Add a T1 backend spike specifying the supported driver fields and verifying private, non-recursive behavior in an offline disposable namespace. Make that result block T8. Unsupported behavior must stop implementation until an explicit backend solution is reviewed.

4. **[BLOCKING] Verification and probe coverage are incomplete.**  
   T8’s probe record omits the root identity and entry device, mode, and link target. It therefore does not fully establish the promised identity of the bound tree. Separately, `verify_pin` specifies refusal on mismatch but does not specify writing a taint marker; ADR §7 explicitly requires failed verification to taint the install.

   **Fix:** Include the root and all relevant identity/listing fields in the probe contract. On an integrity mismatch during launch or host-side verification, persist taint before returning the refusal and signal affected live sessions. Add tests for both paths, including restart persistence.

5. **[BLOCKING] Crash recovery treats existence as successful publication.**  
   T4 renames the tree before writing its manifest and record. T10’s recovery test treats `ALREADY_PUBLISHED` as success. A crash between those writes leaves an existing install with incomplete evidence.

   **Fix:** Define durable publication phases, directory fsyncs, and staging-ID-to-install recovery. An existing name counts as successful publication only after its complete manifest, approved digest, record, and tree are verified. Test crashes after rename and after each metadata write, plus a conflicting existing install.

6. **[BLOCKING] E14 lacks a sufficiently narrow digest-formatting contract.**  
   Grouping does not modify admind’s redactor, which is good. However, E14 permits “artifact digests,” while E5 supplies upstream checksum strings. The plan does not require every grouped value to originate from hashing the actual bytes locally. Its universal `sha256`/16-group description also does not fit npm’s sha512 integrity value.

   **Fix:** Restrict the formatter to locally computed digest values: wsd recomputes the staged manifest digest and artifact hashes, checks them against the supplied evidence, then formats only those computed results. Never apply grouping to arbitrary metadata, source names, URLs, refs, or approval prose. Test that secret-looking upstream strings remain withheld. A full base64url encoding of a **computed SHA-256 digest** is another workable display route; no redaction exception is needed.

7. **[BLOCKING] E13 needs explicit approval predicates.**  
   The narrower validator is reasonable: install approval should not require a design-review or ADR-reference gate. But “every `*_digest` equals” is underspecified and can become vacuously true when fields are missing. The stated predicates also omit an explicit `kind:approval` check.

   **Fix:** Name the required posted and approved digest fields, require each to exist and match the freshly recomputed ask digest, require approval kind, closed/approve state, authorized approver, no gaps, and the journal-bound bead identity. Match the approved effect’s adapter, install name, and manifest digest exactly. Add missing/null-field and wrong-kind tests. wsd must consume the recorded decision and never create or repair approval metadata.

8. **[BLOCKING] The dependency DAG and intermediate interfaces contradict the tasks.**  
   T6 consumes T3’s `Layout`, but declares only T2/T5 dependencies. T10 consumes T9’s `InstallTainted` while being scheduled beside T9. T6 removes lookup interfaces before plan 4b’s host reader migration; its private `_cli` shim does not cover `HostUsageReader`’s direct lookup. T7 also changes production dispatch before its selection wiring lands in T10.

   **Fix:** Add T3 → T6 and T9 → T10 edges, and make T1 findings explicit blockers for affected tasks. Retain lookup compatibility until T8 migrates all callers. Specify coherent production selection wiring and full-suite expectations for each intermediate merge.

9. **[BLOCKING] T1 does not gate all of its findings, and executable test placeholders remain.**  
   Its stop rule addresses refuted *content kinds*, although S9-1 affects extraction layout and S9-5–7 affect updater behavior and generated executables. T2 contains three ellipsis-only tests, which would pass without testing anything.

   **Fix:** Define consumers and stop conditions for every S9 result. Layout and updater failures must block the corresponding implementation, rather than silently dropping content. Replace the ellipsis tests with concrete cases and assertions. Reconcile the inconsistent `verify_pin` signatures and file-map names at the same time.

The requested compatibility and scope decisions are otherwise reasonable:

- **[NON-BLOCKING] Optional `pin=` preserves plan 4b’s contract.** The existing one-argument call still verifies the current selection; launches verify their journaled pin. **Fix:** Add a compatibility test demonstrating both behaviors, and update the plan’s stale plan 4b reference from `ba51be9` to `e47dbf5`.
- **[NON-BLOCKING] Keeping admind’s `binary` key fits §17 item 15, but conflicts literally with §15.** **Fix:** Obtain an explicit ADR clarification that the launcher rejects the key while admind retains it until its later migration.
- **[NON-BLOCKING] Checksum-only v1 is acceptable under §7’s explicit “checksum or signature” wording.** It authenticates through the TLS-delivered upstream metadata and operator approval, without independent signature verification. **Fix:** Record separately whether a signature is available and whether it was verified; `signed: false` currently conflates those facts.
- **[NON-BLOCKING] T12 has a separate approval gate, and T2–11 do not require its host execution.** Only T13’s helper-mode evidence depends on it. **Fix:** Clarify that repository preparation may precede host approval while privileged execution cannot, and that fallback-only completion does not close the store-owner guarantee.

Verdict: REVISE