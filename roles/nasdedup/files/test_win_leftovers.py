import os
import tempfile
import unittest

import win_leftovers


def snapshot(root):
    out = {}
    for dirpath, _, files in os.walk(root):
        for name in files:
            p = os.path.join(dirpath, name)
            out[p] = (os.path.getsize(p), os.path.getmtime(p))
    return out


class WinLeftoversTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        for rel, size in (
            ("ICYGEN/C/Windows/System32/ntdll.dll", 100),
            ("ICYGEN/C/Program Files (x86)/App/app.exe", 50),
            ("ICYGEN/C/pagefile.sys", 30),
            ("ICYGEN/C/Users/krasi/Documents/thesis.docx", 7),
            ("ICYGEN/C/Users/krasi/AppData/Local/Microsoft/Windows/cache.db", 9),
            ("ICYGEN/C/Documents and Settings/Admin/Desktop/Program Files/x.txt", 3),
            ("ICYGEN/C/Photos/2010/a.jpg", 5),
        ):
            path = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(b"x" * size)

    def tearDown(self):
        self.tmp.cleanup()

    def test_user_rule(self):
        self.assertTrue(win_leftovers.is_user_path("C/Users/k/AppData/Windows"))
        self.assertTrue(win_leftovers.is_user_path("C\\Documents and Settings\\A\\Program Files"))
        self.assertEqual(win_leftovers.classify("C/users/k/Program Files"), "user")
        self.assertEqual(win_leftovers.classify("C/Windows"), "system")
        self.assertIsNone(win_leftovers.classify("C/Photos"))

    def test_scan_marks_and_sizes(self):
        rows = {r["path"]: r for r in win_leftovers.scan(self.root, depth=4)}
        self.assertEqual(rows["ICYGEN/C/Windows"]["kind"], "system")
        self.assertEqual(rows["ICYGEN/C/Windows"]["size"], 100)
        self.assertEqual(rows["ICYGEN/C/Program Files (x86)"]["size"], 50)
        self.assertEqual(rows["ICYGEN/C/pagefile.sys"]["size"], 30)
        self.assertEqual(rows["ICYGEN/C/Users"]["kind"], "user")
        self.assertEqual(rows["ICYGEN/C/Documents and Settings"]["kind"], "user")
        # nothing below a profile root is ever a system candidate
        system = [p for p, r in rows.items() if r["kind"] == "system"]
        self.assertFalse(any(win_leftovers.is_user_path(p) for p in system))
        self.assertNotIn("ICYGEN/C/Photos", rows)

    def test_read_only(self):
        before = snapshot(self.root)
        list(win_leftovers.scan(self.root, depth=4))
        win_leftovers.main([self.root, "--depth", "4"])
        self.assertEqual(snapshot(self.root), before)


if __name__ == "__main__":
    unittest.main()
