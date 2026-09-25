import os
import sqlite3
import tempfile
import unittest

import nasdedup
import verify_trees


class VerifyTreesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "dedup.db")
        conn = sqlite3.connect(self.db)
        conn.executescript(nasdedup.SCHEMA)
        for path in ("/a", "/a/x", "/b", "/b/x"):
            conn.execute("INSERT INTO dirs(path, parent, status, sig) VALUES (?,?,?,?)",
                         (path, os.path.dirname(path), "done", "S"))
        self.conn = conn

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def add(self, path, stage, l3, size=10, tree="S"):
        self.conn.execute(
            "INSERT INTO files(path, dirpath, size, stage, tree, l3) VALUES (?,?,?,?,?,?)",
            (path, os.path.dirname(path), size, stage, tree, l3))
        self.conn.commit()

    def run_check(self):
        return verify_trees.classify(verify_trees.open_readonly(self.db))

    def test_all_verified(self):
        for root in ("/a", "/b"):
            self.add(f"{root}/1.jpg", 3, "h1")
            self.add(f"{root}/x/2.jpg", 3, "h2")
        sets, problems = self.run_check()
        self.assertEqual(problems, [])
        self.assertEqual(sets[0][4]["verified"], 4)
        self.assertEqual(verify_trees.main(["--db", self.db]), 0)

    def test_pending_mismatch_orphan_error(self):
        self.add("/a/1.jpg", 3, "h1")
        self.add("/b/1.jpg", 3, "OTHER")
        self.add("/a/x/2.jpg", 5, None)
        self.add("/b/x/2.jpg", 3, "h2")
        self.add("/a/only.jpg", 3, "h3")
        self.add("/b/bad.jpg", -1, None)
        _, problems = self.run_check()
        got = {(state, path) for state, _, path, _, _ in problems}
        self.assertEqual(got, {("mismatch", "/a/1.jpg"), ("mismatch", "/b/1.jpg"),
                               ("pending", "/a/x/2.jpg"), ("orphan", "/a/only.jpg"),
                               ("error", "/b/bad.jpg")})
        self.assertEqual(verify_trees.main(["--db", self.db]), 1)

    def test_readonly(self):
        self.add("/a/1.jpg", 3, "h1")
        conn = verify_trees.open_readonly(self.db)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM files")


if __name__ == "__main__":
    unittest.main()
