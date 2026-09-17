# SQLite runtime execution evidence

Recorded from operator deployment reports on 2026-09-17 UTC for
`btq-harness-9a729b18a99e76122751427b`.
Contract: [runtime specification](../spec/runtime.md).
Procedure and upstream sources: [repair runbook](sqlite-runtime-repair.md).

## Runtime and dependency result

Hermes changed from Python 3.11.15 / bundled SQLite 3.50.4 to Python 3.11.16 /
SQLite 3.53.1. The inspected new SQLite source ID is
`2026-05-05 10:34:17 c88b22011a54b4f6fbd149e9f8e4de77658ce58143a1af0e3785e4e6475127e9`.
Native provisioning, locked dependency sync and isolated native import smoke were
used; no broad Hermes update or smoke bypass was performed.

Initial candidate smoke correctly failed because setuptools metadata omitted seven
existing state modules. The guarded seven-line correction was backed up privately
and committed in the native Hermes repository as `8b1c055084`. The exact retained
private generation was inspected and reused on retry, not selected by recency.

The locked rebuild omitted ten locally installed packages. Operator restored only
these exact pins to the candidate using `uv pip install --no-deps`:
`tabulate==0.10.0`, `pynacl==1.5.0`, `jmespath==1.1.0`,
`botocore==1.42.97`, `brotlicffi==1.2.0.1`, `edge-tts==7.2.7`,
`discord-py==2.7.1`, `davey==0.1.6`, `boto3==1.42.89`,
`s3transfer==0.16.1`. Remaining existing-package version/origin differences: `{}`.
Real PluginManager loads `marmot` and `hermes-maintenance`.

System Python/SQLite and separate Anubis Nix runtime were not replaced. The previous
maintenance evidence's SQLite warning remains an accurate historical observation;
this deployment repairs the affected Hermes venv subsequently.

## Cutover, preservation and recovery

Operator stopped gateway and supervisor for the quiet window, stopped the Hermes
manager and confirmed old PIDs gone. The persistent Codex worker remained alive.
Apply succeeded with a retained fsynced runtime archive, coherent SQLite backup,
and parked old venv. Private backups were not published to the brain repository.
Packaging backup is under
`/home/operator/.local/state/hermes-quarantine/sqlite-runtime-packaging-20260917`;
runtime repair artifacts are under
`/home/operator/.local/state/hermes-quarantine/sqlite-runtime-20260917`.

Before/after cutover: 31 sessions, their lineage, and 8,521 messages identical.
Database integrity result `ok`; foreign-key violations `[]`. Operator open-file
audit confirmed both old-runtime consumers held only `state.db`, which was backed up.
Gateway and resumed manager executables were verified through `/proc/PID/exe`
to use the new Python generation. Final native import probe confirms the fixed
runtime. Fresh gateway logs contain no linked-runtime vulnerability warning. One
informational notice mentions WAL-reset as an example while reporting
`cron/executions.db` changing DELETE to WAL under the existing configured journal
policy now that the runtime is fixed. This is normal Hermes behavior, not a
remaining vulnerability warning; zero WAL-reset string matches are not claimed.
Services are active. Manager native identity
`20260916_182541_e8cb77` resumed alive; Codex fork
`01a0acf9-dfb2-7d43-93f6-e90472d7c123` remained unchanged. No physical reboot
was performed or claimed, and coding workloads were not replayed.

## Verification status and limits

Nine focused runtime/packaging tests pass, including coherent WAL backup,
package-origin comparison, packaging preservation, generation reuse after failed
smoke, repeat apply and interrupted-cutover refusal. Governance validation and
`git diff --check` pass. Operator previously ran 158 full-suite tests successfully
before the two additional packaging/retry tests. Final operator suite: **160 tests
passed in 11.643 seconds**. Harness source integrated at `444196f`.

Ripwire quality-delta returned findings about test callback reachability,
fixture duplication and test-class length; it is not reported as a clean gate.
The wrapper's quiet-window switch is an operator assertion, not enforcement against
arbitrary external writers. It uses native private helper interfaces and pins native
helper, dependency lock and project metadata between staging and apply. Interrupted
cutover requires operator reconciliation. Backups remain retained for reviewed
rollback/retention; database rollback is never automatic.
