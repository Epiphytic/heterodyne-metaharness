# Actual third MEDIUM review: APPROVE as PoC

All ten noncritical findings are retained verbatim below and deferred before activation. No gate is waived. Hosted full Pyright subsequently found three deliberate malformed-model test arguments without typing casts. The CI-only repair adds explicit Any casts, preserving runtime behavior and every production source byte. The initially mistyped local test command ran no tests and is retained; corrected exact eight-module run recorded separately. Full-project Pyright now passes. A fourth MEDIUM review must assess the actual repaired head before it is pushed.

**Verdict: APPROVE for a PoC.** I found nothing critical. Oversized, malformed, typed and aliased inputs are all refused, and I found no path where unsafe data leaves or a proposal is granted wrongly. Most findings below are about which side gets blamed for a refusal, two exception gaps, memory, and wasted work. Finding 1 is worth fixing before activation.

This was a static read only: no tests, probes or edits. The msgspec error-text behaviour behind finding 4 comes from what I know of the library; I did not run it.

10 findings, most severe first:

1. `src/heterodyne/gatekeeper/validate.py:605`: The pre-conversion check `data(raw)` walks the whole request, including host-owned fields, but its errors are caught at line 623 and returned as `OriginGap` (with field ""), so host faults are reported as origin input gaps.

   Failure scenario: The host builds a sufficiency request whose context, open_tasks or public_bindings exceed 256 KiB of strings or 32768 items, or contain an unsupported type such as a set or datetime. `prepare` returns OriginGap(oversize or schema, "") instead of HostFailed("egress_oversize") or InvalidHost. `project._nonready` then returns NeedsGrooming / HoldInvalidInput / EscalateProposal("input_profile"), so the origin is told to fix its card, and the caller may count a failed grooming round. Before this change, the same request reached `_host` and was held as egress_oversize.

2. `src/heterodyne/gatekeeper/validate.py:344`: A permission `invocation` dict that has no `input` key returns an origin gap (schema at /invocation/input), which contradicts the stated rule that invocation-envelope faults stay host-owned.

   Failure scenario: The host builds {kind: permission, invocation: {cwd_id, sha256}} without `input`. The check at line 633 passes because it is a dict, then `_origin_fields` fails with OriginGap(schema, "/invocation/input"). `disposition` returns EscalateProposal("input_profile"), blaming the origin's tool input, when it should return InvalidHost("/invocation") or invalid_host.

3. `src/heterodyne/gatekeeper/validate.py:77`: The size check counts an item when it is taken off the work stack, not when it is pushed, so a deep shared graph can grow the stack to millions of entries before the 32768 limit trips.

   Failure scenario: A chain of 64 nested dicts, each holding up to 32768 entries that point to the next (values can be None). Each pop pushes about 65536 entries while the counter rises by 1, so the stack reaches about 64×65536 ≈ 4M tuples (hundreds of MB) before the depth limit fails. Counting `len(children)` before `extend` would cap the stack at 32768 entries.

4. `src/heterodyne/gatekeeper/validate.py:336`: The reported path and code come from msgspec's error text, which can include input text, so an origin or model can choose the reported public path and switch schema/profile.

   Failure scenario: A root-level unknown key named "x` - at `$.title" in the candidate (or in the model result for `decode_result`) produces "Object contains unknown field `x` - at `$.title`". The regex at line 206 projects that as /candidate/title (or /title), and an invalid tag value containing "unknown field" turns schema into profile. No private bytes leak, but the diagnostic path and code are under input control.

5. `src/heterodyne/gatekeeper/validate.py:769`: `check_result` and `decode_result` do not catch AttributeError, so a malformed typed request raises out of these public functions, contradicting the "malformed typed inputs, no exceptions" claim.

   Failure scenario: Calling `check_result(raw, request, privacy)` with a request that is not a Struct raises AttributeError at `request.privacy_sha256` (line 767). It also raises when `request.context` holds a non-ContextItem, through `aliases(request)` in `decode_result` (lines 812/823). Both escape instead of returning Invalid("result", ...).

6. `src/heterodyne/gatekeeper/validate.py:551`: The same request is walked and converted to plain data many times over.

   Failure scenario: `_host` calls `data(request)` for the snapshot digest, then `canonical(request)` walks it again (lines 551-555). `check_result` runs `data(raw)` and then `canonical(built)` walks it again (772-773). `disposition` calls `prepare` again on an already-prepared request, and `privacy_context` re-hashes the redaction-kernel fingerprint on every call. A single disposition therefore does roughly 6-10 full graph walks plus 2-3 fingerprint hashes; computing `data()` once and taking digest and length from the same output would remove about half.

7. `src/heterodyne/gatekeeper/validate.py:622`: For typed sufficiency/permission requests, the origin fields are validated twice: `_typed_origin` runs `_candidate`/`_native` on the typed object, then `_origin_fields` runs the same checks on the converted dict.

   Failure scenario: Every candidate text field and every input key/leaf goes through the full multi-pass redaction twice. If both checks are kept, the duplicated work is real; otherwise, catching the converted-dict check's exceptions alone gives the same refusal coverage.

8. `src/heterodyne/admind/redact.py:83`: `redact_with_dirs` re-sorts and rebuilds the login-directory pattern (a cache lookup in `re.compile`) on every call, and `_text` calls it once per text field.

   Failure scenario: A sufficiency request with 32 context items, 512 input leaves/keys and up to 64 groom gaps does hundreds of sort/join/escape/compile-cache lookups for the same `login_dirs`. Compiling once per privacy context and passing the pattern in avoids this. This was deferred after round 1, but the cost has grown with the second validation pass and the prewalk.

9. `src/heterodyne/gatekeeper/validate.py:684`: `decode_request` copies the OriginGap-to-Invalid mapping that `_request_invalid` already has (lines 696-700).

   Failure scenario: The duplicated branch can drift. For example, if profile/schema mapping changes in only one place, egress and decode_request would report different codes for the same origin gap. `return _request_invalid(checked)` covers every non-ready case.

10. `src/heterodyne/gatekeeper/_rule_identity.py:5`: The docstring says optimization, including -OO, fails closed, but plain -O keeps docstrings, and these functions contain no asserts, so their bytecode should not change and -O runs would still be admitted.

    Failure scenario: Running under `python -O` would pass `matches()` and run although only "unoptimized CPython 3.12" is admitted. The behaviour is harmless, but the documented admission boundary is wrong; either detect `sys.flags.optimize` or narrow the statement to -OO.
