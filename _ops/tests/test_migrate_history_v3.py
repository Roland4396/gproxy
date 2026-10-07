"""Projection unit tests. Actual v4-schema integration is a separate gate."""
import importlib.util
from pathlib import Path
import sqlite3
import unittest

SPEC = importlib.util.spec_from_file_location("history", Path(__file__).parents[1] / "migrate_history_v3.py")
history = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(history)


class ProjectionTests(unittest.TestCase):
    def test_fixed_decimal_uses_v4_bankers_rounding(self):
        self.assertEqual(history.fixed("1.0000000005"), 1000000000)
        self.assertEqual(history.fixed("1.0000000015"), 1000000002)
        self.assertEqual(history.fixed("-1.0000000015"), -1000000002)
        self.assertIsNone(history.fixed(None))
        with self.assertRaises(ValueError):
            history.fixed("10000000000")

    def test_missing_period_is_never_inferred(self):
        self.assertIsNone(history.millis(None))
        self.assertEqual(history.millis(1791411621), 1791411621000)

    def test_scope_keeps_models_and_unknown_is_not_all(self):
        self.assertEqual(history.scope({"kind": "model_prefixes", "models": ["claude", "gpt"]}), {"model_prefixes": ["claude", "gpt"]})
        self.assertEqual(history.scope({"kind": "models", "models": ["a"]}), {"models": ["a"]})
        self.assertEqual(history.scope({"kind": "all"}), "all")
        self.assertEqual(history.scope(None), "unknown")
        with self.assertRaises(ValueError):
            history.scope({"kind": "invented"})

    def test_headers_keep_repeated_values_and_distinguish_empty_from_missing(self):
        self.assertEqual(history.headers('{"set-cookie":["a=1","b=2"]}'), '[["set-cookie","a=1"],["set-cookie","b=2"]]')
        self.assertEqual(history.headers("{}"), "[]")
        self.assertIsNone(history.headers(None))
        with self.assertRaises(ValueError):
            history.headers('{"x":1}')

    def test_snapshot_does_not_change_percent_or_turn_seconds_into_tiny_milliseconds(self):
        entry = {"id": "3p-weekly", "source_id": "subscription", "label": None,
                 "subject": "account", "value": {"kind": "window", "used_percent": "100",
                 "period_start": None, "period_end": 1791411621, "unlimited": False}}
        out = history.quota_snapshot(entry)
        self.assertEqual(out["used_percent"], "100")
        self.assertEqual(out["period_end_ms"], 1791411621000)
        self.assertIsNone(out["period_start_ms"])
        self.assertFalse(out["unlimited"])

    def test_empty_capture_bytes_are_not_missing_and_cancelled_stream_is_partial(self):
        source, target = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        source.executescript("""
            CREATE TABLE usage_rows (id INTEGER,request_id TEXT,at INTEGER,latency_ms INTEGER,
                upstream_started_at_ms INTEGER,ended TEXT,user_id INTEGER,user_key_id INTEGER,
                upstream_model TEXT,operation TEXT);
            INSERT INTO usage_rows VALUES(1,'req',2,10,1000,'interrupted',1,2,'claude','stream_generate_content');
            CREATE TABLE wire_logs (id INTEGER,request_id TEXT,at INTEGER,provider_id INTEGER,
                credential_id INTEGER,upstream_url TEXT,request_method TEXT,request_headers TEXT,
                response_headers TEXT,request_body BLOB,response_body BLOB,response_status INTEGER);
            INSERT INTO wire_logs VALUES(7,'req',1,4,9,'http://local/v1?x=1','POST','{}','{}',X'',X'64617461',200);
        """)
        columns = """id TEXT,started_at_ms INTEGER,initiator_request_id TEXT,kind TEXT,
            provider_id TEXT,credential_id TEXT,user_id TEXT,api_key_id TEXT,model TEXT,operation TEXT,
            request_method TEXT,request_url TEXT,request_query TEXT,request_headers TEXT,response_headers TEXT,
            request_body BLOB,response_body BLOB,request_body_encoding TEXT,response_body_encoding TEXT,
            request_framing TEXT,response_framing TEXT,request_body_state TEXT,response_body_state TEXT,
            response_status INTEGER,client_ip TEXT,state TEXT,error TEXT,ended_at_ms INTEGER,metrics TEXT"""
        target.execute(f"CREATE TABLE upstream_records ({columns})")
        target.execute("CREATE TABLE capture_links (downstream_id TEXT,upstream_id TEXT)")
        result = history.captures(source, target, {"usage_rows", "wire_logs"})
        self.assertEqual(result, {"upstream_records": 1})
        row = target.execute("SELECT id,request_body,request_body_state,response_body,response_body_state,state,request_query,ended_at_ms FROM upstream_records").fetchone()
        self.assertEqual(row, ("v3-wire_logs-7", b"", "complete", b"data", "partial", "failed", "x=1", 1010))
        self.assertEqual(target.execute("SELECT * FROM capture_links").fetchone(), ("v3-usage_rows-1", "v3-wire_logs-7"))
        source.close()
        target.close()

    def test_snapshot_path_cannot_be_the_live_destination(self):
        with self.assertRaisesRegex(ValueError, "different"):
            history.migrate(Path("same.db"), Path("same.db"))


if __name__ == "__main__":
    unittest.main()
