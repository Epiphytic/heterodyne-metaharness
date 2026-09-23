# btq-harness-67e876d0 verification

Design was committed first as 77310cb; implementation follows the signed merge of
canonical 49f1e34 into the task branch. Logs in this directory are durable versioned
fixtures and development evidence, not live deployment verification.

Command: `env -u PYTHONPATH /tmp/harness-67e876d0-venv/bin/python -m unittest discover -s tests -v`.
The temporary interpreter has system-site-packages (PyYAML) and the pinned
requirements-blockers.txt installed. PYTHONPATH is unset to prevent the unrelated
Hermes `tests` package from shadowing this repository's tests. Tests themselves and
logs do not depend on preserving that interpreter directory; rebuild a venv and
install the same dependencies to reproduce.

Fixtures cover real NIP-01 Schnorr signatures, payload/scope/authority/expiry
rejection, native gate edges and retained digest evidence, consent before effect,
Marmot operator-ask transport, registered agent dispatch, hourly watch retry,
authorized review fixes, and secondary isolation/unblock/explicit resume. Beads,
review-provider and tmux effects use fakes; no live terminal consent or deployment
is asserted. One optional external integration test may skip; signed-receipt tests
must run with coincurve installed.

The initial full development run passed 444 tests (one skip). Later focused logs
retain both passing runs and the mock adaptation failure introduced by the added
concurrent-claim guard; the fixture was updated to model that query. inbox-regression-suite.log retains the broader run that exposed per-destination
ordering and incomplete Supervisor test setup. regression-suite.log records the
48-test correction run. full-suite.log records the subsequent comprehensive run. No implementation commit or stage has been recorded: staging failed
with `Read-only file system` creating
`/home/operator/repos/harness-improvements/.git/worktrees/checkout3/index.lock`.
The final working-tree run must be followed by a committed-head run after the
manager reconciles Git write access or performs the authorized signed commit.
This is an upfront worker-permissions occurrence (class b), not approval to move
Git metadata or use an alternate identity. The retained submission manifest pins
base HEAD and every changed file's digest.

Governance validation and git diff --check are required. ripwire quality-delta is
not green: its report includes same-name symbol collisions (for example new start
against unchanged review_dispatch.start), short-horizon churn, and growth in the
existing Supervisor/CLI classes. Submission guards were moved into one shared
helper used by direct sends and inbox readiness. New validation functions remain
explicit for review; no metric baseline was reset to hide findings. Change-check
is structural evidence only, not a replacement for tests or operator review.

No service, runtime config or permission policy was changed. Enabling blockers
requires installing the verifier and reviewed signer/assignee/adapter configuration.
Merge, final tests, deployment and live verification remain manager-owned.
