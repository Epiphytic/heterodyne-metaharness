# Exact717 execution checklist — disabled draft, author incorporation d2

This is an execution companion to draft.md §§3–6, not authority. All commands remain unexecuted. The bootstrap exits75 unconditionally; authority.pending.json is unapproved. The sole HIGH review returned REVISE10, incorporated once; no second review/validation. Historical reviewed checklist bytes remain under history/reviewed-d1.

## Immutable target and moving author checkout

Controller update: the x84mg author checkout has advanced to `018ebc94b1e4b66bd6431a54cc68ffa20e6097aa` on `codex/reviewed-fulfillment-fallback-20261020` (PR133). That newer fallback source is outside this deployment request. The sole HIGH design review began with HEAD717 and reviews the absolute draft file only. This deployment target remains exactly `71713d8a3e35da7a297948c45351360bbc0de37c`; source comparisons must use immutable `git show 71713d8a3e35da7a297948c45351360bbc0de37c:<path>` or the proposed clean exact717 export, never the current author working tree. No future fallback substitution is authorized by this checklist.

## Retained concrete bindings

| Role | Actual retained path / evidence |
|---|---|
| Service user | `openclaw`; fresh UID/ownership equality required |
| Database | `<operator-home>/.local/state/heterodyne/admind/admind.db` |
| State/config directories | `<operator-home>/.local/state/heterodyne/admind`; `<operator-home>/.config/heterodyne` |
| Policy | `<operator-home>/.config/heterodyne/policy.toml` |
| User unit | `<operator-home>/.config/systemd/user/heterodyne-admind.service` |
| Install record | `<operator-home>/.local/share/heterodyne/install/record.json` |
| Existing guard Python | `<operator-home>/repos/heterodyne-metaharness-v2-btq-btq-qzkdk/.venv/bin/python` (path exists/executable; resolves `/usr/bin/python3.12`; dependencies/runtime not re-probed) |
| Old exact checkout | `<operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/admind-accepted-source-offline-rehearsal/accepted-source` (exists; do not overwrite) |
| Retained old release Python | `<operator-home>/.local/share/heterodyne/releases/5db4c0c27c51b4e3db5a5dee8ab94ef25d3af156/.venv/bin/python3` (historical unit evidence, not fresh process proof) |
| Proposed new exact checkout | `.../next-admind-reader-deployment-design/execution/exact717` — already prepared clean by controller; source-preparation.json retained; fresh identity check still required |
| New evidence directory | `.../next-admind-reader-deployment-design/execution/run717` — NOT created; exclusive0700, no reusing previous attempt |

The only verified prior root execution mechanism is `sudo -n bash N/reader-first-pinned-bootstrap.sh OLD_CHECKOUT install`, retained exit0 at2026-10-19T19:35:27Z. The bootstrap used root `mktemp /run/hz-install-boot.XXXXXX`, umask077, copied the source installer, verified SHA48b87826…, ran pinned installer flags and removed only that temporary bootstrap with a trap. Prior genuine authority was native `btq-r4hi2`/Marmot`yvfh`, digest10ccc837…, designcd60e2f…. Those facts are historical, not new consent or a persistent permission rule. Do not execute the old bootstrap for717: it pins5db and its old archive.

## Ordered execution gates

1. Retain genuine new Marmot/native deployment authority covering all five §3 items, including preconsented unchanged5db abort and contained rollback if Marmot is offline. No pending template or historical r4hi2 authority qualifies.
2. Use the separately prepared clean exact717 export; verify literal commit, installer/archive hashes and actual lock-compatible interpreter. Future018ebc/PR133 excluded. Existing parent run717 is0700; **do not create stopped-preparation or rollback-newest-preparation**.
3. Capture preflight unit/process/config/helper/install/guard identity and actual stop timeout, with private hashes. Run exact pinned platform_state.py record-show and wsd-lock commands from draft§4; require free/absent lock and known inactive WSD. Preserve the actual old WSD unit and every release it references, irrespective of new install-record release.
4. Before outage run the pinned bootstrap preview once. Strict exit0; review every would-line. Store and units checks/summary all ok; any would/drift there HOLDs. No new service account/helper/root configuration changes are authorized. Release/admid publication expected list must be approved. Dryrun does not establish final service-up acceptance or cache a complete release build.
5. Exclude every writer in draft§5A, including surviving tmux admin agent and directCLI/controller/hook/ask.sock/alert clients; no unproved pause APIs or session destruction. Unknown exclusion HOLDs before stop. Controller01:14:54Z found live admin/default tmux sessions despite an empty workstream socket;266 process observations included36 denied reads, so observed single DB opener does not establish writer absence. Never kill those unknown sessions. Capture bounded before Plan8 inventory of exact configured schedule manifests, schedule-effects.db/WAL/SHM, paired-backups/native-backup-cycle.state, health.json, maintenance/scheduler state, alert root and timers.
6. Write stop intent; issue exactly one `systemctl --user stop heterodyne-admind.service`. Capture MainPID/ActiveState/SubState/Result/ExecMainCode/ExecMainStatus. Default systemd timeout may SIGKILL outgoing5db; non-clean or unknown stop HOLDs before guard. No repeated stop or extra kill. Positive unchanged clean failure can restart old5db only under the preconsented branch.
7. Run guard as openclaw with exact command below; it creates previously absent output. Preserve coherent SQLite before/rehearsal results and protected config/units/helper/record bytes/mode metadata. Never initialize live Store, migrate projection, delete WAL or use DB-only cp.
8. Persist intent and run one root block using the disabled bootstrap's post-approval retained copy. Copy/hash/run/trap stay in same root lifetime. No fetch/adopt; literal openclaw. Require real install exit0 AND literal `ok: heterodyne-admind.service is up and runs the render`; skipped --no-wsd summary not sufficient.
9. Failure before positively proved first mutation: unchanged identity proof permits old5db start; unknown is post-mutation. Post-mutation: explicitly stop restart loop once, capture outcome, repeat all writer exclusions before snapshot/rollback. No blind retry.
10. Acceptance captures exact717 process/unit and stable restarts, newest liveDB/history/guard/identity, actual subscription/read-only card health, inactive WSD and unchanged old unit, no timers/Plan8 inventory surprises. No diagnostic send or feature enablement. Unexplained state is HOLD.

## Exact guard command (not executed)

```sh
PYTHONPATH=<operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/next-admind-reader-deployment-design/execution/exact717/src \
 <operator-home>/repos/heterodyne-metaharness-v2-btq-btq-qzkdk/.venv/bin/python \
 <operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/next-admind-reader-deployment-design/execution/exact717/deploy/prepare_admind_guard.py \
 --checkout <operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/next-admind-reader-deployment-design/execution/exact717 \
 --reviewed-commit 71713d8a3e35da7a297948c45351360bbc0de37c \
 --database <operator-home>/.local/state/heterodyne/admind/admind.db \
 --output <operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/next-admind-reader-deployment-design/execution/run717/stopped-preparation \
 --authority-file <operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/next-admind-reader-deployment-design/execution/run717/authority.json
```

## Preconsented rollback with newest LIVE DB

First stop any717 restart loop once and establish full client quiescence again. Take fresh coherent newest snapshot as evidence only; NEVER copy it into place. Rehearse exact old5db guard on the unchanged live DB path with old checkout/PYTHONPATH, old reviewed commit and absent `run717/rollback-newest-preparation`. Preserve all newest decisions/unknown effects. Only original-plan-reconcilable installer state and positive compatibility qualify rollback; otherwise contained HOLD, no invented authority.

After verifying retained old bootstrap SHA f6797dd5c2aae4ff8c1ebb89f57074cb9f7d137a7ec73601880563413bdbd071 and its exact5db installer/archive bindings, proposed command is:

```sh
sudo -n bash <operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/reader-first-pinned-bootstrap.sh \
 <operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/admind-accepted-source-offline-rehearsal/accepted-source install
```

New deployment authority must expressly cover this finite offline fallback; historical approval does not. Require exit0/literal up-render and actual restored identity. No old execute-reader-first-approved.py, staleDB restoration, uninstall shortcut, tmux destruction or arbitrary transaction repair. If not safe, remain contained with diagnostics pending genuine Marmot recovery; no terminal substitute.
