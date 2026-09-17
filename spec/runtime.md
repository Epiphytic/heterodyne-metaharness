# Runtime repair

Follow [maintenance ownership](maintenance.md) and [change lifecycle](changes.md).
Probe the exact executable used by each service, recording Python, linked SQLite,
source ID and distribution provenance. Version-only checks cannot establish whether
a distribution backport is patched. An integrity check does not prove absence of a race.

Repair the affected Hermes venv using native managed-runtime staging and cutover helpers;
retain the existing source checkout, lock and plugin dependencies. Gate cutover on a
fixed SQLite build, native import smoke, unchanged existing package versions/origins,
and an operator quiet window covering every consumer of that venv. Staging may add
locked extras; review inventory differences before promotion. Do not modify system
SQLite or unrelated runtimes based solely on another interpreter's warning.

Before cutover, retain a private fsynced runtime archive and coherent SQLite backups
outside active recall and published brain repositories. Never remove WAL files or
restore a live database. Preserve native session identities; restart affected runtime
consumers into their existing sessions without replaying model tasks. Verify database
integrity/foreign keys, plugins, service runtime and native identity after deployment.

Persist stage and cutover intent. Repeated successful application performs no cutover.
An interrupted cutover requires operator reconciliation, not automatic replay. Keep
rollback artifacts until a separately reviewed retention action. Record execution
results before historical completion; scripts alone are not deployment evidence.
