**Verdict: APPROVE as a PoC, with one new pre-activation must-fix.**

I found nothing critical in the production code that is wired up today. The newest commit (696086623e) only touches tests: it adds type narrowings and asserts, and I found no weakened test checks. Prior R4 findings still stand and are not repeated below.

The one serious new finding is #1. Every generated Plan8 timer unit points at the wrong state directory, one `wsd` level too deep. If timers were installed today, every scheduled wake and every babysitter run would fail to find its inputs. It has to be fixed before timer activation. It is not on any live path yet, because nothing in `src` calls `schedule_units.render`.

This was a read-only review of 35176c8..6960866. I made no edits and ran no tests or probes.

7 findings, most severe first:

1. `src/heterodyne/wsd/schedule_units.py:46`: The Plan8 service units set HETERODYNE_STATE_DIR to manifest.state.path, which is the already-suffixed `<root>/wsd`. In the spawned `scheduled-wake` and `babysitter` processes, resolve() appends `wsd` again, so they look in `<root>/wsd/wsd`. The existing wsd unit (wsd/unit.py) passes the root, paths.state_dir(env), instead.

   Failure scenario: The installer renders units from a valid manifest. Each timer run calls _settings() and gets state_dir=<root>/wsd/wsd and socket=<root>/wsd/wsd/<sock>. load_manifest's os.open raises FileNotFoundError, so every wake prints 'scheduling input or local transport unavailable' and exits 1, and no BACKSTOP or reconcile is ever admitted. The babysitter fails the same way, and its alert dir would be <root>/wsd/alerts rather than the shared <root>/alerts. test_render_retains_arbitrary_approved_cadence_and_pinned_release never checks the Environment= lines.

2. `src/heterodyne/wsd/effect_owner.py:122`: The copy closure only catches (OSError, ValueError) from retain(), and retain() only catches (OSError, ValueError, KeyError, TypeError, sqlite3.Error) per bundle. An AttributeError or OverflowError from an unrelated bundle therefore fails the whole copy future, and _settle durably records a sealed, valid cycle as 'failed'.

   Failure scenario: A same-UID artifact dir has a private manifest.json whose 'copies' is a 2-entry dict or a list of non-dicts, so verify_bundle's copy.get raises AttributeError. Or a verified manifest has a very negative completed_utc, so datetime.fromtimestamp raises OverflowError in the bucket loop. After the new bundle is sealed, retain raises, copy() propagates the error, and _settle writes finish_backup(cycle,'failed'). Every later cycle is then marked local_failed although its bundle exists. This contradicts the 'unknown artifacts hold' and 'prune failure preserves sealed result' contract.

3. `src/heterodyne/wsd/backup_cycles.py:183`: On every backup cycle, retain() fully re-verifies every retained bundle: SHA-256 of both DB copies plus PRAGMA integrity_check. It runs on the copy worker before _settle, so the cycle stays pending throughout, and it never checks the cancel event.

   Failure scenario: With the about 20 bundles the daily/weekly buckets keep and a journal of a few hundred MB, each cycle re-reads and integrity-checks gigabytes. Meanwhile pending_backup stays set, so the next native companion call gets 'unresolved'. EffectOwner.close() (copier.shutdown(wait=True)) blocks until hashing finishes, which can run past TimeoutStopSec=150 and end in SIGKILL. Caching the verified manifest sha and comparing it, or verifying only deletion candidates, would bound the work.

4. `src/heterodyne/wsd/backup_cycles.py:51`: The 256-entry inventory cap counts bundles from earlier installations or generations. retain() never prunes them (verify_bundle rejects foreign identity), so each generation change leaves its retained set behind permanently.

   Failure scenario: Each generation leaves about 1 + 7×2 + 4×2 retained bundles. After roughly 11 generation bumps, plus any corrupt-attempt leftovers, the directory reaches 256 entries. copy_bundle then raises 'backup inventory held' on every cycle, and every paired backup is recorded local_failed until an operator cleans up by hand. Nothing in health or observer names the cause.

5. `src/heterodyne/wsd/daemon.py:676`: _heartbeat reads /proc/sys/kernel/random/boot_id once and loops with no handling of its own. Any exception other than OSError or ValueError from _publish_health (for example a msgspec.ValidationError from convert, or RuntimeError from effects.refresh() submitting to a shut-down writer) ends the health task silently while the daemon keeps serving.

   Failure scenario: One unexpected exception in the snapshot path kills health_task and nothing observes it, because serve() only gathers it at shutdown. health.json stops updating and the babysitter reports 'stale' indefinitely, but the daemon neither restarts nor trips self.fatal. That is a persistent false outage signal, with no recovery short of a manual restart.

6. `src/heterodyne/wsd/daemon.py:630`: `wsctl scheduled-hold` sets self.maintenance in memory only. The flag is absent from HealthSnapshot and from the wake predicate, so an intentional hold looks the same as a broken timer path.

   Failure scenario: The operator runs scheduled-hold for maintenance. Every scheduled wake is now refused with reason 'maintenance', so last_pickup and last_reconcile stop advancing. After 2*cadence+10 s the observer opens a wake_overdue failure episode and writes a local alert. Nothing in health.json says the hold is deliberate, and the hold silently ends at the next daemon restart.

7. `src/heterodyne/wsd/effect_owner.py:136`: EffectOwner.close() sets cancel, which aborts an in-flight copy_bundle. _settle then durably records the cycle as 'failed' (high_water=cycle), so a normal daemon stop or restart during a backup permanently turns that cycle into local_failed.

   Failure scenario: A native backup's companion copy is mid-snapshot when the operator restarts wsd. cancel.set() makes the progress callback raise TimeoutError, copy_bundle discards the attempt, and _settle writes finish_backup(cycle,'failed'). After restart, a retry of the same cycle returns 'local_failed' rather than re-copying. A clean stop is treated like a crash, and that native cycle never gets a local companion.
