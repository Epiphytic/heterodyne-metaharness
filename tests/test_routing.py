import asyncio
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from harness.routing import RoutingConflict, enqueue, route_inbound, route_with_retry, drain_spool


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "harness.sqlite3"
        with sqlite3.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE runs(id TEXT, group_id TEXT, state TEXT);
                CREATE TABLE inbox(id TEXT PRIMARY KEY,run_id TEXT,text TEXT,created_at REAL,state TEXT);
                INSERT INTO runs VALUES('run-1','group-1','working');
            """)

    def send(self, group="group-1", message="message-1"):
        return enqueue(group, message, "please continue", "human", db_path=self.path)

    def messages(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT run_id,text,state FROM inbox").fetchall()

    def test_duplicate_is_consumed_once(self):
        self.assertTrue(self.send())
        self.assertTrue(self.send())
        self.assertEqual(self.messages(), [("run-1", "[Marmot sender human]\nplease continue", "pending")])

    def test_unowned_falls_through(self):
        self.assertFalse(self.send("other"))
        self.assertEqual(self.messages(), [])

    def test_ambiguous_ownership_fails_closed(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO runs VALUES('run-2','group-1','blocked')")
        with self.assertRaises(RoutingConflict):
            self.send()
        self.assertEqual(self.messages(), [])

    def test_terminal_owner_falls_through(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE runs SET state='completed'")
        self.assertFalse(self.send())

    def test_missing_database_is_unowned_without_creation(self):
        absent = self.path.parent / "absent.db"
        self.assertFalse(enqueue("g", "m", "hi", "human", db_path=absent))
        self.assertFalse(absent.exists())

    def test_authentication_and_self_filter(self):
        event = dict(account_id_hex="bot", sender_account_id_hex="human",
                     group_id_hex="group-1", message_id_hex="msg", text="continue")
        for changes in ({"sender_account_id_hex": "outsider"},
                        {"sender_account_id_hex": "bot"}, {"account_id_hex": "other"}):
            self.assertFalse(route_inbound(event | changes, account_id="bot",
                                          allowed_senders={"human", "bot"}, db_path=self.path))
        self.assertEqual(self.messages(), [])
        self.assertTrue(route_inbound(event, account_id="bot", allowed_senders={"human"}, db_path=self.path))

    def test_failed_run_remains_routable_including_commands(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE runs SET state='failed'")
        self.assertTrue(enqueue("group-1", "stop-msg", "/stop", "human", db_path=self.path))
        self.assertIn("/stop", self.messages()[0][1])

    def test_database_failure_retains_and_retries_turn(self):
        with patch("harness.routing.route_inbound", side_effect=[sqlite3.OperationalError("locked"), True]) as route:
            with patch("harness.routing.asyncio.sleep") as sleep:
                result = asyncio.run(route_with_retry({}, account_id="bot", allowed_senders={"human"}))
        self.assertTrue(result)
        self.assertEqual(route.call_count, 2)
        sleep.assert_awaited_once_with(30)

    def test_installer_is_idempotent_and_keeps_backup(self):
        from install_routing import install, HOOK
        adapter = self.path.parent / "adapter.py"
        original = "async def dispatch(self, event):\n        try:\n            if not await self._should_run_turn(event):\n                return\n        except Exception:\n            raise\n"
        adapter.write_text(original)
        self.assertTrue(install(adapter))
        installed = adapter.read_text()
        self.assertEqual(installed.count(HOOK), 1)
        self.assertFalse(install(adapter))
        self.assertEqual(adapter.read_text(), installed)
        self.assertEqual(adapter.with_name("adapter.py.before-workstream-routing").read_text(), original)
        self.assertTrue(adapter.with_name("_workstream_routing.py").exists())

    def test_spool_survives_database_failure_and_restarts_once(self):
        event = dict(account_id_hex="bot", sender_account_id_hex="human",
                     group_id_hex="group-1", message_id_hex="msg", text="continue")
        with patch("harness.routing.enqueue", side_effect=sqlite3.OperationalError("unavailable")):
            with self.assertRaises(sqlite3.OperationalError):
                route_inbound(event, account_id="bot", allowed_senders={"human"}, db_path=self.path)
        spool = self.path.parent / "incoming-spool"
        self.assertEqual(len(list(spool.glob("*.json"))), 1)
        self.assertEqual(spool.stat().st_mode & 0o777, 0o700)
        self.assertEqual(next(spool.glob("*.json")).stat().st_mode & 0o777, 0o600)
        self.assertEqual(drain_spool(self.path), {"delivered": 1, "unowned": 0, "errors": 0})
        self.assertEqual(len(self.messages()), 1)
        self.assertTrue(route_inbound(event, account_id="bot", allowed_senders={"human"}, db_path=self.path))
        self.assertEqual(len(self.messages()), 1)
        self.assertEqual(list(spool.glob("*.json")), [])

    def test_spool_unowned_is_retained_on_restart_but_healthy_fallthrough_cleans(self):
        event = dict(account_id_hex="bot", sender_account_id_hex="human",
                     group_id_hex="unowned", message_id_hex="msg", text="continue")
        with patch("harness.routing.enqueue", side_effect=sqlite3.OperationalError("unavailable")):
            with self.assertRaises(sqlite3.OperationalError):
                route_inbound(event, account_id="bot", allowed_senders={"human"}, db_path=self.path)
        self.assertEqual(drain_spool(self.path)["unowned"], 1)
        self.assertFalse(route_inbound(event, account_id="bot", allowed_senders={"human"}, db_path=self.path))
        self.assertEqual(list((self.path.parent / "incoming-spool").glob("*.json")), [])

    def test_unauthorized_input_never_spooled(self):
        event = dict(account_id_hex="bot", sender_account_id_hex="outsider",
                     group_id_hex="group-1", message_id_hex="msg", text="continue")
        self.assertFalse(route_inbound(event, account_id="bot", allowed_senders={"human"}, db_path=self.path))
        self.assertFalse((self.path.parent / "incoming-spool").exists())

    def test_replay_does_not_move_message_to_new_owner(self):
        self.send()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE runs SET state='completed'")
            db.execute("INSERT INTO runs VALUES('run-2','group-1','working')")
        self.assertTrue(self.send())
        self.assertEqual(len(self.messages()), 1)
        self.assertEqual(self.messages()[0][0], "run-1")


if __name__ == "__main__":
    unittest.main()
