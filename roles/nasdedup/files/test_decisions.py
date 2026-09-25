"""The retention constants must match the owner's recorded rulings (#1554).

D-0001 (decisions/D-0001.json at the repo root): the baba Krastinka originals are never
touched. Both the generator and the independent checker carry their own NEVER list; if a
path is dropped from either, this fails before a quarantine script is ever generated.
"""

import json
import os
import unittest

import build_quarantine
import validate_quarantine

HERE = os.path.dirname(os.path.abspath(__file__))
DECISIONS_DIR = os.path.join(HERE, "..", "..", "..", "decisions")

# The D-0001 ruling's paths. Adding a path is allowed; losing one is the failure.
NEVER_D0001 = {
    "video/exported/baba Krastinka/",
    "Pictures/Pictures-by-years/semeini snimki/baba/",
}


class NeverList(unittest.TestCase):
    def test_generator_keeps_every_never_path(self):
        self.assertLessEqual(NEVER_D0001, set(build_quarantine.NEVER))

    def test_checker_keeps_every_never_path(self):
        self.assertLessEqual(NEVER_D0001, set(validate_quarantine.NEVER))


class DecisionIds(unittest.TestCase):
    def test_every_cited_decision_is_recorded_with_a_quote(self):
        for did in build_quarantine.DECISIONS:
            with open(os.path.join(DECISIONS_DIR, did + ".json"), encoding="utf8") as f:
                row = json.load(f)
            self.assertEqual(row["id"], did)
            self.assertTrue(row["quote"].strip(), did)

    def test_d0001_ruling_names_each_never_path(self):
        with open(os.path.join(DECISIONS_DIR, "D-0001.json"), encoding="utf8") as f:
            answer = json.load(f)["answer"]
        for p in NEVER_D0001:
            self.assertIn(p, answer)


if __name__ == "__main__":
    unittest.main()
