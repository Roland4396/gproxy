import sqlite3
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_v4_snapshot import equal, table_digest


class ArchiveDigestTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE a (x,y)")
        self.db.execute("CREATE TABLE b (x,y)")

    def test_multiset_preserves_duplicates_and_order_independence(self):
        self.db.executemany("INSERT INTO a VALUES (?,?)", [(1, "x"), (2, b"x"), (1, "x")])
        self.db.executemany("INSERT INTO b VALUES (?,?)", [(1, "x"), (1, "x"), (2, b"x")])
        self.assertEqual(table_digest(self.db, "a"), table_digest(self.db, "b"))
        self.db.execute("DELETE FROM b WHERE rowid=1")
        self.assertNotEqual(table_digest(self.db, "a"), table_digest(self.db, "b"))

    def test_blob_text_and_null_are_distinct(self):
        self.db.execute("INSERT INTO a VALUES (?,?)", (None, b"x"))
        self.db.execute("INSERT INTO b VALUES (?,?)", (None, "x"))
        self.assertNotEqual(table_digest(self.db, "a"), table_digest(self.db, "b"))

    def test_error_does_not_include_compared_secret_values(self):
        with self.assertRaisesRegex(AssertionError, "parity mismatch: secret") as caught:
            equal("private-left", "private-right", "secret")
        self.assertNotIn("private", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
