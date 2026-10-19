Presentation note: the single absolute native-source pathname is displayed as ${PINNED_NATIVE_SOURCE}; findings and verdict are unchanged. Original private report SHA256: 9e0ccd7c965a099fe35f0f99eace95746d9c9e2265d3e9066c045ee100cc14f5.

**Verdict: APPROVE as a PoC.** None of these is a critical flaw within the stated non-authority scope. The source hash is checked before exec, the evaluator stays Unavailable, and `require_authority` always raises Hold.

Scope: `git diff 347de696..f7742d28`, which touches only `src/heterodyne/acceptance/source_adapters.py` and `tests/test_acceptance_source_adapters.py`. I checked the pinned native source at `${PINNED_NATIVE_SOURCE}`; its sha256 matches `NATIVE_SOURCE_SHA256`. Following the review recipe, nothing was verified and nothing was run. I left out F9 and the ten R3 findings, which are already open. No CLAUDE.md rule applies to this Python diff.

The finding that most undercuts what the code claims is #1: the code says it keeps native prose private, but the returned `gaps` carry it out unchanged. A side note for #6: `codec.Hold` subclasses `ValueError`, so native `approval_valid` would quietly catch any Hold raised inside it. Today that has no effect only because `ask_digest` runs first over the same reads.

10 findings, most severe first:

1. `src/heterodyne/acceptance/source_adapters.py:140`: The comment at lines 131-132 says native diagnostics stay private and never expose raw prose, but NativeDiagnostic.gaps returns the native gap strings unchanged, and these include text taken from the bead.

   Failure scenario: A capture whose ask.refs contains a ref like {'kind':'commit','id':['<arbitrary text>']} or unknown keys makes native ask_digest emit gaps such as "ref id is not a non-empty string: [...]" or "ref has field(s) ... <key names>". tuple(gaps) is returned through the operational diagnostic, with only the 1 MiB capture bound limiting its size, so arbitrary captured prose reaches callers despite the stated invariant.

2. `src/heterodyne/acceptance/source_adapters.py:129`: gate_valid is computed against capture.policy, which comes from the evidence itself, and correlate() never ties that policy to the binding envelope's policy principals or the attempt's sender_key/current_policy, so the approver set the native gate checks is self-asserted.

   Failure scenario: A capture sets policy={'approvers':['mallory']} and approved_by='mallory', and a binding is built with decided_by/operator='mallory' while envelope.policy lists only 'op' (or the attempt has current_policy=False). validate() returns gate_valid=True and correlate() passes, so 'strict whole record/binding correlation' never checks the one input that decides the native gate.

3. `src/heterodyne/acceptance/source_adapters.py:101`: The replacements for _git_repo_present and _git do not keep native semantics: native returns False/None (a gap) for NUL in repo, args or stdin, and requires 'rev-parse --git-dir' to succeed, while the captured versions return True or the captured stdout and need no rev-parse read.

   Failure scenario: Capture repos=['r\x00x'] with matching cat-file/ls-tree reads. Native _git_repo_present('r\x00x') returns False, giving the gap 'repo ... does not exist' and gate_valid=False. The captured repo_present returns True and the git lookup returns the captured commit/blob, so the digest has no gap and gate_valid=True. The claim of 'exact native implementations' fails on this input.

4. `src/heterodyne/acceptance/source_adapters.py:166`: Attempt correlation only looks at attempts whose ref equals via_ref: other recorded attempts (for example a recorded 'deny') are ignored, and attempt.started_at is never compared with authority.approved_at.

   Failure scenario: The envelope has the matching approve attempt plus a second attempt with another ref, action='deny', settled='recorded', or the approve attempt's started_at is a day after approved_at. correlate() still returns the diagnostic. The 'native-time' test only fails because the metadata approved_at differs, so attempt timing is never actually checked.

5. `src/heterodyne/acceptance/source_adapters.py:109`: Executing the pinned source runs module-level code that does live host I/O: Path(__file__).resolve() on '/captured/bin/btq' (lstat/readlink) and Path.home() (reads HOME or the passwd database). This contradicts the module docstring's 'no live ... used', and the I/O-trap test does not cover it.

   Failure scenario: On a host where /captured is a symlink or an unreadable mount, or where HOME is unset and the passwd lookup fails, exec raises OSError/RuntimeError (or KeyError from pwd) before the try block in validate(). That surfaces as a raw exception instead of Hold, and the host filesystem is consulted on every validate call.

6. `src/heterodyne/acceptance/source_adapters.py:138`: Only some exceptions from native execution are converted to Hold: AttributeError, IndexError/LookupError, RecursionError, and anything raised by _namespace/exec (which runs outside the try) escape raw. Separately, codec.Hold subclasses ValueError, so native approval_valid quietly catches any Hold raised inside it.

   Failure scenario: A future-allowed native path or an odd capture shape raises AttributeError, or the exec-time module code fails. The caller gets an untyped exception instead of Hold('native_capture_unavailable'). Separately, any capture miss reached only inside approval_valid would be logged to the private stderr and turned into gate_valid=False instead of Hold.

7. `tests/test_acceptance_source_adapters.py:275`: The stderr regression test only runs the gate_valid=True path, where native approval_valid prints nothing, and it never checks with capsys/capfd that rejection messages stay off the host stderr.

   Failure scenario: If the namespace sys=SimpleNamespace(stderr=...) override were dropped, real rejections ('btq: approval ... rejected: ...') would write to process stderr, yet this test would still pass because no print happens on the passing path.

8. `tests/test_acceptance_source_adapters.py:199`: The parametrized refusal tests (here and at line 85) use a bare pytest.raises(Hold) with no match=, so a case can pass because of an unrelated failure.

   Failure scenario: The 'native-time' case passes from the metadata approved_at mismatch, not from attempt timing. The 'float' or 'uncaptured' cases would also pass if an earlier check started raising for a different reason, so regressions in the specific guards go undetected.

9. `src/heterodyne/acceptance/source_adapters.py:150`: Repeated work: every validate() recompiles and re-executes the full ~2,750-line native source, correlate() parses the capture twice (inside validate, then again at line 150), and ask_digest runs twice per validate (once directly, once inside approval_valid).

   Failure scenario: Each correlate costs a full compile plus module exec, two strict decodes of up to 1 MiB, and two full ref resolutions. Compiling once in __init__ (exec of a cached code object per call) and having validate return the parsed Capture would remove most of it.

10. `src/heterodyne/acceptance/source_adapters.py:110`: Altitude: isolation depends on patching module globals after exec and overwriting Queue.__init__. Any reachable native path that calls os, Path or open directly (for example _check_object_store) bypasses the capture, and only the hash pin keeps that true today.

    Failure scenario: Native code that reaches os.stat or Path.read_text through a helper that was not patched does real host I/O despite the 'finite captured reads' claim, and the fix is another patch on the list. The deeper fix is a native pure core that takes an injected I/O provider.
