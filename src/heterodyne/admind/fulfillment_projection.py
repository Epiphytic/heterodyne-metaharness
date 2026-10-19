"""Opt-in fulfillment projection. No constructor migration and no receipt/history rewrites.

The literal finite DDL is checked before suppression. Old writers invalidate stamps through SQLite
triggers, independently of whether a new daemon is running. Hashing uses a separate WAL snapshot.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import logging
import secrets
import sqlite3
import time
from typing import TYPE_CHECKING, cast

import msgspec

from heterodyne.admind import fulfillment as f

if TYPE_CHECKING:
    from heterodyne.admind.store import Store

SCHEME = "fulfillment-projection-v2"
MAX_GENERATION = 9223372036854775806
CHECK = "CHECK(typeof(generation)='integer' AND generation>=0 AND generation<=9223372036854775806)"
TABLES = (
    "CREATE TABLE fulfillment_projection_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
    "epoch TEXT NOT NULL, scheme TEXT NOT NULL, scan_cursor TEXT)",
    "CREATE TABLE fulfillment_generations (ask_id TEXT PRIMARY KEY, generation INTEGER NOT NULL "
    + CHECK
    + ")",
    "CREATE TABLE fulfillment_verifications (ask_id TEXT PRIMARY KEY, epoch TEXT NOT NULL, "
    "generation INTEGER NOT NULL " + CHECK + ", state TEXT NOT NULL "
    "CHECK(state IN ('valid','conflict','too-large')))",
    "CREATE TABLE fulfillment_dirty (ask_id TEXT PRIMARY KEY, generation INTEGER NOT NULL)",
    "CREATE INDEX fulfillment_verification_epoch ON fulfillment_verifications(epoch,ask_id)",
)


def bump_keys(keys: str) -> str:
    """Invalidate the finite distinct OLD/NEW/victim key set exactly once per trigger."""
    eligible = (
        f"SELECT ask_id FROM ({keys}) AS targets WHERE ask_id IS NOT NULL "  # noqa: S608 - literal DDL only
        "AND EXISTS(SELECT 1 FROM ask_fulfillments f WHERE f.ask_id=targets.ask_id)"
    )
    return (
        f" INSERT INTO fulfillment_generations SELECT ask_id,0 FROM ({eligible}) WHERE true "  # noqa: S608
        "ON CONFLICT(ask_id) DO UPDATE SET generation=generation+1;"
        f" UPDATE ask_fulfillments SET valid=0 WHERE ask_id IN ({eligible});"
        f" DELETE FROM fulfillment_verifications WHERE ask_id IN ({eligible});"
        " INSERT INTO fulfillment_dirty SELECT ask_id,generation FROM fulfillment_generations "
        f"WHERE ask_id IN ({eligible}) ON CONFLICT(ask_id) DO UPDATE SET generation=excluded.generation;"
    )


def bump(key: str) -> str:
    return bump_keys(f"SELECT {key} AS ask_id")


def trigger(name: str, event: str, body: str) -> str:
    return f"CREATE TRIGGER {name} {event} BEGIN{body} END"


ASK_FIELDS = (
    "ask_id,kind,poster,title,body,pr_url,head_sha,bead,digest,question_form,choices,links,created_at,status"
)
VICTIMS = (
    "SELECT ask_id FROM ask_answers WHERE message_id=NEW.message_id OR (ask_id=NEW.ask_id AND seq=NEW.seq)"
)


def answer_bumps(*, update: bool = False) -> str:
    # UNION deduplicates NEW, OLD and both REPLACE uniqueness victims before any mutation.
    keys = f"SELECT NEW.ask_id AS ask_id UNION {VICTIMS}"
    if update:
        keys += " UNION SELECT OLD.ask_id"
    return bump_keys(keys)


HISTORY = " SELECT RAISE(ABORT,'fulfillment-history-protected');"
TRIGGERS = (
    trigger("fq2_asks_insert", "BEFORE INSERT ON asks", bump("NEW.ask_id")),
    trigger(
        "fq2_asks_update",
        "BEFORE UPDATE OF " + ASK_FIELDS + " ON asks",
        bump_keys("SELECT OLD.ask_id AS ask_id UNION SELECT NEW.ask_id"),
    ),
    trigger("fq2_asks_delete", "BEFORE DELETE ON asks", bump("OLD.ask_id")),
    trigger("fq2_answers_insert", "BEFORE INSERT ON ask_answers", answer_bumps()),
    trigger(
        "fq2_answers_update",
        "BEFORE UPDATE ON ask_answers",
        answer_bumps(update=True),
    ),
    trigger("fq2_answers_delete", "BEFORE DELETE ON ask_answers", bump("OLD.ask_id")),
    trigger("fq2_receipt_insert", "AFTER INSERT ON ask_fulfillments", bump("NEW.ask_id")),
    trigger(
        "fq2_receipt_immutable",
        "BEFORE UPDATE OF ask_id,request_digest,receipt_id,receipt,namespace,"
        "operation_id,core_digest ON ask_fulfillments",
        HISTORY,
    ),
    trigger("fq2_receipt_delete", "BEFORE DELETE ON ask_fulfillments", HISTORY),
    trigger(
        "fq2_store_id_update",
        "BEFORE UPDATE OF key,value ON kv WHEN OLD.key='ask_store_id' OR NEW.key='ask_store_id'",
        HISTORY,
    ),
    trigger("fq2_store_id_delete", "BEFORE DELETE ON kv WHEN OLD.key='ask_store_id'", HISTORY),
    trigger(
        "fq2_store_id_insert",
        "BEFORE INSERT ON kv WHEN NEW.key='ask_store_id' AND "
        "EXISTS(SELECT 1 FROM kv WHERE key='ask_store_id')",
        HISTORY,
    ),
)
DDL = TABLES + TRIGGERS


def schema_hash(db: sqlite3.Connection) -> str:
    rows = db.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).fetchall()
    return hashlib.sha256(f.canonical(rows)).hexdigest()


def validate(db: sqlite3.Connection) -> tuple[str, str | None]:
    f.validate_schema_db(db)
    present = db.execute("SELECT 1 FROM sqlite_master WHERE name='fulfillment_projection_meta'").fetchone()
    if not present:
        raise f.Invalid("fulfillment-migration-required")
    for statement in DDL:
        name = statement.split()[2]
        row = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if row != (statement,):
            raise f.Invalid("fulfillment-projection-unavailable")
    if db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name GLOB 'fq2_*'"
    ).fetchone() != (12,):
        raise f.Invalid("fulfillment-projection-unavailable")
    rows = db.execute("SELECT epoch,scheme,scan_cursor FROM fulfillment_projection_meta LIMIT 2").fetchall()
    if len(rows) != 1 or rows[0][1] != SCHEME or not f.HEX.fullmatch(str(rows[0][0])):
        raise f.Invalid("fulfillment-projection-unavailable")
    return str(rows[0][0]), rows[0][2]


def migrate(store: Store, *, apply: bool = False) -> dict[str, object]:
    """Caller is the explicitly quiesced deployment CLI; this does not confer deployment authority."""
    with store.lock:
        if store.db.in_transaction:  # migration cannot join an existing read/write transaction
            raise f.Invalid("migration-transaction-active")
        result = migrate_connection(store.db, apply=apply)
        if apply:
            store.configure_verification_notifications()
        return result


def migrate_connection(db: sqlite3.Connection, *, apply: bool = False) -> dict[str, object]:
    db.execute("BEGIN EXCLUSIVE" if apply else "BEGIN")
    try:
        f.validate_schema_db(db)
        before = schema_hash(db)
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE name LIKE 'fulfillment_%' "
            "AND name='fulfillment_projection_meta'"
        ).fetchone()
        if exists:
            validate(db)
            changed = False
        else:
            # Reject fragments, not CREATE IF NOT EXISTS repairs of an unknown projection.
            names = [s.split()[2] for s in DDL]
            marks = ",".join("?" for _ in names)
            if db.execute(f"SELECT 1 FROM sqlite_master WHERE name IN ({marks}) LIMIT 1", names).fetchone():  # noqa: S608 - finite literal SQL identifiers only
                raise f.Invalid("fulfillment-projection-unavailable")
            changed = apply
            if apply:
                for statement in DDL:
                    db.execute(statement)
                db.execute(
                    "INSERT INTO fulfillment_projection_meta VALUES (1,?,?,NULL)",
                    (secrets.token_hex(32), SCHEME),
                )
                db.execute("INSERT INTO fulfillment_generations SELECT ask_id,0 FROM ask_fulfillments")
                db.execute(
                    "INSERT INTO fulfillment_dirty SELECT ask_id,generation FROM fulfillment_generations"
                )
                db.execute("UPDATE ask_fulfillments SET valid=0")
        after = schema_hash(db)
        counts = dict(
            db.execute(
                "SELECT 'receipts',COUNT(*) FROM ask_fulfillments UNION ALL "
                "SELECT 'pending',COUNT(*) FROM ask_fulfillment_intents WHERE status='pending'"
            )
        )
        db.execute("COMMIT")
        return {
            "schema": "fulfillment-migration-v1",
            "changed": changed,
            "migration_required": not bool(exists) and not apply,
            "before_schema_sha256": before,
            "after_schema_sha256": after,
            "counts": counts,
        }
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


SUPPRESSED = (
    "EXISTS(SELECT 1 FROM ask_fulfillments f JOIN fulfillment_generations g "
    "ON g.ask_id=f.ask_id JOIN fulfillment_verifications v ON v.ask_id=f.ask_id "
    "JOIN fulfillment_projection_meta m ON m.singleton=1 "
    "WHERE f.ask_id=asks.ask_id AND f.valid=1 AND v.state='valid' "
    "AND v.epoch=m.epoch AND v.generation=g.generation)"
)
FALLBACK = (
    "status IN ('open','answered','deciding','uncertain') OR "
    "EXISTS(SELECT 1 FROM ask_fulfillments f WHERE f.ask_id=asks.ask_id)"
)


def capacity_predicate(db: sqlite3.Connection) -> str:
    try:
        validate(db)
    except (f.Invalid, sqlite3.Error):
        return FALLBACK
    return f"({FALLBACK}) AND NOT ({SUPPRESSED})"


def bounded_binding(db: sqlite3.Connection, ask_id: str) -> dict[str, object]:
    fields = "kind,poster,title,body,pr_url,head_sha,bead,digest,question_form,choices,links,created_at"
    row = db.execute(f"SELECT {fields} FROM asks WHERE ask_id=?", (ask_id,)).fetchone()  # noqa: S608 - finite literal SQL identifiers only
    if row is None:
        raise f.Invalid("missing")
    answers = db.execute(
        "SELECT seq,kind,operator,message_id,text,at,choice FROM ask_answers "
        "WHERE ask_id=? ORDER BY seq LIMIT 51",
        (ask_id,),
    ).fetchall()
    if len(answers) > 50:
        raise f.Invalid("too-large")
    answer_keys = ("seq", "kind", "operator", "message_id", "text", "at", "choice")
    values = [dict(zip(answer_keys, r, strict=True)) for r in answers]
    identity = db.execute("SELECT value FROM kv WHERE key='ask_store_id'").fetchone()
    return {
        "ask_store_id": None if identity is None else identity[0],
        "ask_id": ask_id,
        "created_at": row[-1],
        "ask_sha256": f.digest("ask", dict(zip(fields.split(","), row, strict=True))),
        "answers_sha256": f.digest("answers", values),
        "answer_ids": [r["message_id"] for r in values if r["kind"] == "answer"],
    }


def measure(db: sqlite3.Connection, ask_id: str) -> int:
    # SQLite computes byte lengths before Python materializes any potentially large text.
    sizes = db.execute(
        "SELECT length(CAST(receipt AS BLOB)) FROM ask_fulfillments WHERE ask_id=?", (ask_id,)
    ).fetchone()
    if sizes is None or sizes[0] > 131072:
        raise f.Invalid("too-large")
    n = db.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM ask_answers WHERE ask_id=? LIMIT 51)", (ask_id,)
    ).fetchone()[0]
    if n > 50:
        raise f.Invalid("too-large")
    # json_quote counts the exact escaped representation, not merely unescaped UTF-8.
    expressions = "+".join(
        f"length(CAST(json_quote({k}) AS BLOB))" for k in ASK_FIELDS.split(",") if k != "status"
    )
    ask_size = db.execute(f"SELECT {expressions} FROM asks WHERE ask_id=?", (ask_id,)).fetchone()  # noqa: S608 - finite literal SQL identifiers only
    if ask_size is None:
        raise f.Invalid("missing")
    answer_columns = ("seq", "kind", "operator", "message_id", "text", "at", "choice")
    expression = "+".join(f"length(CAST(json_quote({k}) AS BLOB))" for k in answer_columns)
    answer_size = db.execute(
        f"SELECT coalesce(SUM({expression}+128),0) FROM ask_answers WHERE ask_id=?",  # noqa: S608 - finite literal columns only
        (ask_id,),
    ).fetchone()[0]
    size = int(ask_size[0]) + int(answer_size) + 4096
    if size > 8388608:
        raise f.Invalid("too-large")
    return size + int(sizes[0])


def verify_binding(db: sqlite3.Connection, ask_id: str) -> str:
    """Verify one already size-bounded unit; malformed retained bytes grant no suppression."""
    record = db.execute("SELECT receipt FROM ask_fulfillments WHERE ask_id=?", (ask_id,)).fetchone()
    if record is None or not isinstance(record[0], str):
        raise f.Invalid("stored-receipt-unavailable")
    raw = record[0].encode()
    f.strict_json(raw, limit=131072)
    original = msgspec.json.decode(raw, type=dict[str, object])
    binding = original.get("binding")
    actual = bounded_binding(db, ask_id)
    row = db.execute("SELECT kind,status FROM asks WHERE ask_id=?", (ask_id,)).fetchone()
    good = isinstance(binding, dict) and all(
        cast(dict[str, object], binding).get(k) == actual[k]
        for k in ("ask_store_id", "ask_id", "created_at", "ask_sha256", "answers_sha256")
    )
    good = good and row in (("question", "answered"), ("merge", "answered"))
    if good:
        attested = msgspec.convert(binding, type=f.Binding, strict=True)
        f.validate_binding(attested, [])
        good = set(attested.consumed_answers) <= set(msgspec.convert(actual["answer_ids"], type=list[str]))
    return "valid" if good else "conflict"


def stamp_current(db: sqlite3.Connection, ask_id: str) -> None:
    """Caller holds the apply/confirm writer transaction and has just validated this binding.

    Receipt insertion's invalidation trigger runs first. Publish only the resulting exact current
    generation, atomically with the immutable receipt; legacy writes still invalidate it immediately.
    """
    epoch, _ = validate(db)
    generation = db.execute(
        "SELECT generation FROM fulfillment_generations WHERE ask_id=?", (ask_id,)
    ).fetchone()
    if generation is None:
        raise f.Invalid("fulfillment-projection-unavailable")
    db.execute(
        "INSERT OR REPLACE INTO fulfillment_verifications VALUES (?,?,?,'valid')",
        (ask_id, epoch, generation[0]),
    )
    db.execute("UPDATE ask_fulfillments SET valid=1 WHERE ask_id=?", (ask_id,))
    db.execute("DELETE FROM fulfillment_dirty WHERE ask_id=? AND generation=?", (ask_id, generation[0]))


def verify_pass(store: Store) -> int:
    """One <=20-ID, <=20MiB pass. Snapshot/hash is independent of the live Store RLock."""
    started = time.monotonic()
    results: list[tuple[str, int | None, str]] = []
    with store.read_snapshot() as db:
        epoch, cursor = validate(db)
        dirty = [
            str(r[0]) for r in db.execute("SELECT ask_id FROM fulfillment_dirty ORDER BY ask_id LIMIT 10")
        ]

        def remaining(after: str) -> list[str]:
            return [
                str(r[0])
                for r in db.execute(
                    "SELECT f.ask_id FROM ask_fulfillments f WHERE f.ask_id>? "
                    "AND NOT EXISTS(SELECT 1 FROM fulfillment_verifications v "
                    "WHERE v.ask_id=f.ask_id AND v.epoch=?) ORDER BY f.ask_id LIMIT 10",
                    (after, epoch),
                )
            ]

        rest = remaining(cursor or "")
        if not rest and cursor is not None:
            rest = remaining("")
        # Alternate queues: even two maximum-sized units fit the 20MiB budget. Each gets a first
        # unit before either can consume the pass, and overlaps are processed only once.
        selected: list[str] = []
        for i in range(max(len(dirty), len(rest))):
            for queue in (rest, dirty):
                if i < len(queue) and queue[i] not in selected:
                    selected.append(queue[i])
        used = 0
        for ask_id in selected:
            if time.monotonic() - started >= 10:
                break
            generation: int | None = None
            try:
                gen = db.execute(
                    "SELECT generation FROM fulfillment_generations WHERE ask_id=?", (ask_id,)
                ).fetchone()
                if gen is None:
                    raise f.Invalid("missing-generation")
                generation = int(gen[0])
                size = measure(db, ask_id)
                if used + size > 20971520:
                    break
                used += size
                state = verify_binding(db, ask_id)
            except (
                f.Invalid,
                msgspec.DecodeError,
                ValueError,
                TypeError,
                UnicodeError,
                sqlite3.Error,
            ) as exc:
                state = "too-large" if str(exc) == "too-large" else "conflict"
            results.append((ask_id, generation, state))
    with store.transaction(notify_fulfillment=False):
        current, _ = validate(store.db)
        if current != epoch:
            return 0
        for ask_id, gen, state in results:
            if gen is None or store.db.execute(
                "SELECT generation FROM fulfillment_generations WHERE ask_id=?", (ask_id,)
            ).fetchone() != (gen,):
                continue
            store.db.execute(
                "INSERT OR REPLACE INTO fulfillment_verifications VALUES (?,?,?,?)",
                (ask_id, epoch, gen, state),
            )
            store.db.execute(
                "UPDATE ask_fulfillments SET valid=? WHERE ask_id=?", (int(state == "valid"), ask_id)
            )
            store.db.execute("DELETE FROM fulfillment_dirty WHERE ask_id=? AND generation=?", (ask_id, gen))
        if rest:
            done = {r[0] for r in results}
            last = cursor
            for ask_id in rest:
                if ask_id not in done:
                    break  # Never advance over an unprocessed epoch-queue prefix.
                last = ask_id
            store.db.execute(
                "UPDATE fulfillment_projection_meta SET scan_cursor=? WHERE singleton=1", (last,)
            )
    return len(results)


async def worker(store: Store) -> None:
    """Dedicated single thread; cancellation drains the bounded running pass before closing."""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="fulfillment")
    wake = asyncio.Event()
    loop = asyncio.get_running_loop()

    def changed() -> None:
        if not loop.is_closed():
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:
                pass  # Shutdown raced notification; durable dirty work survives the next startup.

    store.fulfillment.verification_changed = changed
    try:
        while True:
            wake.clear()
            future = asyncio.get_running_loop().run_in_executor(executor, verify_pass, store)
            try:
                count = await asyncio.shield(future)
                delay = 1 if count else 30
            except asyncio.CancelledError:
                # Drain the running pass, but preserve shutdown even if it fails.
                # Otherwise the supervisor retries forever with cancellation pending.
                while not future.done():
                    try:
                        await asyncio.shield(future)
                    except asyncio.CancelledError:
                        continue  # Repeated shutdown requests cannot cancel the running pass.
                    except Exception:  # noqa: BLE001 - retrieve failure below without replacing cancellation
                        break
                try:
                    future.result()
                except (Exception, asyncio.CancelledError):  # noqa: BLE001 - preserve original cancellation
                    logging.getLogger(__name__).warning("fulfillment-verification-failed-during-shutdown")
                raise
            except Exception:  # noqa: BLE001 - bounded background-pass boundary, cancellation above
                # No retained payload/exception text enters the diagnostic.
                logging.getLogger(__name__).warning("fulfillment-verification-unavailable")
                # A malformed retained row must not terminate the daemon or bypass fixed backoff.
                await asyncio.sleep(30)
                continue
            # Notifications coalesce during the mandatory gap; they cannot bypass it.
            await asyncio.sleep(1)
            if count:
                continue
            try:
                await asyncio.wait_for(wake.wait(), timeout=delay - 1)
            except TimeoutError:
                pass
    finally:
        store.fulfillment.verification_changed = lambda: None
        executor.shutdown(wait=True)
