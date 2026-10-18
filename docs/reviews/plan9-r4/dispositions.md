# Plan9 fourth actual review

Actual Claude Opus built-in `/code-review`, MEDIUM, accepted the f7742d2 source-adapter slice as an offline PoC. The exact required critical-flaws question was used. First two HIGH and third MEDIUM reviews remain retained; this is not a fresh lineage. All ten findings remain OPEN and unwaived.

1. **OPEN_UNWAIVED** — `src/heterodyne/acceptance/source_adapters.py:140`: The comment at lines 131-132 says native diagnostics stay private and never expose raw prose, but NativeDiagnostic.gaps returns the native gap strings unchanged, and these include text taken from the bead.

2. **OPEN_UNWAIVED** — `src/heterodyne/acceptance/source_adapters.py:129`: gate_valid is computed against capture.policy, which comes from the evidence itself, and correlate() never ties that policy to the binding envelope's policy principals or the attempt's sender_key/current_policy, so the approver set the native gate checks is self-asserted.

3. **OPEN_UNWAIVED** — `src/heterodyne/acceptance/source_adapters.py:101`: The replacements for _git_repo_present and _git do not keep native semantics: native returns False/None (a gap) for NUL in repo, args or stdin, and requires 'rev-parse --git-dir' to succeed, while the captured versions return True or the captured stdout and need no rev-parse read.

4. **OPEN_UNWAIVED** — `src/heterodyne/acceptance/source_adapters.py:166`: Attempt correlation only looks at attempts whose ref equals via_ref: other recorded attempts (for example a recorded 'deny') are ignored, and attempt.started_at is never compared with authority.approved_at.

5. **OPEN_UNWAIVED** — `src/heterodyne/acceptance/source_adapters.py:109`: Executing the pinned source runs module-level code that does live host I/O: Path(__file__).resolve() on '/captured/bin/btq' (lstat/readlink) and Path.home() (reads HOME or the passwd database). This contradicts the module docstring's 'no live ... used', and the I/O-trap test does not cover it.

6. **OPEN_UNWAIVED** — `src/heterodyne/acceptance/source_adapters.py:138`: Only some exceptions from native execution are converted to Hold: AttributeError, IndexError/LookupError, RecursionError, and anything raised by _namespace/exec (which runs outside the try) escape raw. Separately, codec.Hold subclasses ValueError, so native approval_valid quietly catches any Hold raised inside it.

7. **OPEN_UNWAIVED** — `tests/test_acceptance_source_adapters.py:275`: The stderr regression test only runs the gate_valid=True path, where native approval_valid prints nothing, and it never checks with capsys/capfd that rejection messages stay off the host stderr.

8. **OPEN_UNWAIVED** — `tests/test_acceptance_source_adapters.py:199`: The parametrized refusal tests (here and at line 85) use a bare pytest.raises(Hold) with no match=, so a case can pass because of an unrelated failure.

9. **OPEN_UNWAIVED** — `src/heterodyne/acceptance/source_adapters.py:150`: Repeated work: every validate() recompiles and re-executes the full ~2,750-line native source, correlate() parses the capture twice (inside validate, then again at line 150), and ask_digest runs twice per validate (once directly, once inside approval_valid).

10. **OPEN_UNWAIVED** — `src/heterodyne/acceptance/source_adapters.py:110`: Altitude: isolation depends on patching module globals after exec and overwriting Queue.__init__. Any reachable native path that calls os, Path or open directly (for example _check_object_store) bypasses the capture, and only the hash pin keeps that true today.

Native gap strings are unsanitized private diagnostic data. Captured policy is evidence supplied by the source owner, not independently authenticated current policy. The captured Git substitutions are not equivalent on all native inputs. Complete pinned module initialization may consult host path/home information, and execution exceptions are not fully contained. The diagnostic must not be exposed as a public report, current-policy authority or a production capture mechanism. Its require_authority path always HOLDs, and the evaluator remains Unavailable. No constructor, native writes, operational collector, runbook, corpus or qualifying clock is admitted.
