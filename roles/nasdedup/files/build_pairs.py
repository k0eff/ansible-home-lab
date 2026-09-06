#!/usr/bin/env python3
"""Build the list of picture pairs worth looking at perceptually.

Byte hashing has already decided every case it can. What it cannot see is the same
photograph stored twice in different encodings — a re-save, an export at another
quality, a rotation baked in. Those share a filename far more often than not, so the
candidate set is name-groups whose members are not already proven identical.

Each member is compared against the largest file in its group rather than against every
other member. The largest is the copy most likely to be the original, the comparison
answers the question actually being asked — is this a lesser copy of that — and it keeps
the work linear in the group instead of quadratic.
"""

import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

R = "/mnt/krasi/"
PREFIX = sys.argv[1] if len(sys.argv) > 1 else "Pictures/"
OUT = sys.argv[2] if len(sys.argv) > 2 else "pairs.json"

# Google Photos was decided on its own terms: those files are re-encodes by definition,
# were verified against their local originals with the three-layer check, and the
# confirmed ones are already in the quarantine list. Re-examining them here would ask a
# question that has an answer.
SKIP = re.compile(r"^Pictures/Google Photos/")

c = sqlite3.connect("dedup.db")
by = defaultdict(list)
for p, sz, h in c.execute(
        "SELECT path,size,l3 FROM files WHERE path LIKE ?", (R + PREFIX + "%",)):
    by[os.path.basename(p).lower()].append((p, sz or 0, h))

pairs = []
for name, v in by.items():
    if len(v) < 2:
        continue
    hs = [h for _, _, h in v if h]
    if len(hs) == len(v) and len(set(hs)) == 1:
        continue                       # already proven identical; nothing to look at
    v.sort(key=lambda t: -t[1])
    best = v[0]
    for p, sz, h in v[1:]:
        if h and best[2] and h == best[2]:
            continue                   # this one matches the keeper byte for byte
        if SKIP.match(p.replace(R, "")) or SKIP.match(best[0].replace(R, "")):
            continue
        pairs.append([p, best[0], sz, best[1]])

json.dump(pairs, open(OUT, "w"))
print(f"{len(pairs)} двойки от {sum(1 for v in by.values() if len(v) > 1)} имена")
