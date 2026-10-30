## Round 3 verdict for PR145: no critical flaws, good enough to implement as a PoC

I ran the built-in `code-review` skill at medium effort over `76c07c0...2421256`, read-only. Nothing was edited, no services were started and the queue wasn't touched. The skill doesn't run a separate verify pass at this level, so I read the code behind the top three findings myself.

**The R1/R2 root WAL/SHM link gap is closed.**
- **Live files:** the database, WAL and SHM are opened by descriptor with `O_NOFOLLOW`. Each is checked for owner, link count and write bits.
- **Copy:** they are copied with bounded reads into a root-owned, private attempt directory that is anchored to an open descriptor (`deployment_cessation.py:282-288`).
- **SQLite:** it only ever opens the private copies, and it rebuilds the SHM itself in private.
- **Publish:** before publishing, the code re-checks that the destination directory is still the same one, then links the snapshot into place (`:294-303`).

**The other three focus areas also hold:**
- **Card prep/bind parity:** prep (`deployment_card.py:51`) and bind (`deployment_outage_authority.py` `_bindings`) both go through `_approval_card` → `_check_card`.
- **CLD_DUMPED:** a core-dumped exit never counts as a clean stop (`deployment_cessation.py:455` treats only exit codes 1 and 2 as clean).
- **TLS handshake:** the watchdog is attached to the socket before the handshake starts, so a slow-trickle handshake gets interrupted. DNS lookup is still not covered, and the docstring says so.

### Medium issues: fix soon, none block a PoC
1. **`src/heterodyne/wsd/backup_copy.py:44`**: `source.resolve()` turns the descriptor-anchored path back into a normal path, and SQLite then opens it by name. The "SQLite reads only anchored copies" guarantee therefore depends on the parent directories not being renamed between staging and open. Only root can rename them, since the attempt directory is root-owned 0700, so this is a weakness and not an exploit. Fix: skip `resolve()` for anchored sources.
2. **`src/heterodyne/actions/deployment_cessation.py:302-307`**: the snapshot is published inside the `TemporaryDirectory`/`ExitStack` blocks. If cleanup afterwards fails (for example the temp directory can't be removed), the caller gets `cessation-stopped-snapshot-unavailable` but the snapshot stays published. Every retry then fails with `FileExistsError`.
3. **`src/heterodyne/actions/deployment_authority.py:162`**: the extra renderer-parity check uses the process-wide redaction state, not the admind directories it was given. It's backed up by the explicit `redact_with_dirs` check on line 160, so it's weaker than it looks rather than wrong. It can still refuse a valid card if that process-wide state differs.

### Lower priority
- **`deployment_cessation.py:190`**: the staged copy (up to 1 GiB database plus 256 MiB WAL) is written without checking free space first, so a large database could fill the protected filesystem.
- **`deployment_outage_authority.py:66`**: a native body that isn't a string raises `AttributeError` and crashes, instead of returning `AuthorityHold('malformed-native-context')`.
- **Cleanups:**
  - Binaries are re-hashed and sockets reconnected on every `_show()`, which is repeated work inside the timing window (`:386`).
  - The directory walk and bounded-copy code duplicate existing helpers (`:115`, `:193`).
  - `operation_canonical` stores the same bytes as `published_original` (`deployment_card.py:54`).
  - `live.check()` runs twice (`:200`).

As you directed, I didn't flag the open HOLD items: dormant owning-factory/current-semantic-consent enrollment, "stopping one unit doesn't prove every writer stopped," and the missing historical settlement. They are still unproven and still block the real release. They don't block a PoC.
