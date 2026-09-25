#!/usr/bin/env python3
"""Check, read-only, that every file in a duplicate directory tree is proven by SHA-256.

nasdedup.py logs "~N reclaimable ... (pending full verification)" on every run, whether
or not tier 3 has already hashed those trees — the line counts every file that carries a
tree signature, not the ones still waiting. So a run that says "nothing to do" for tiers
1-3 can print 97.5 GB "pending" in the same breath, and the log alone cannot tell which is
true (#1553).

This answers it from dedup.db. For every duplicate tree set it lines up the copies by
their path relative to the tree root and checks, file by file:

  verified  stage 3, a SHA-256 on record, and the same SHA-256 in every other copy
  pending   no SHA-256 yet (stage 0/1/2/5) — tier 3 has not reached it
  mismatch  hashed, but a copy at the same relative path hashes differently
  orphan    no copy at the same relative path in some other tree of the set
  error     tier 3 failed to read it (stage -1)

The database is opened with mode=ro: this never writes, and it never reads the share.
Exit 0 only when every file in every set is verified.

    verify_trees.py [--db PATH] [--list N]
"""

import argparse
import os
import sqlite3
import sys
from collections import Counter, defaultdict

STATE_DIR = os.environ.get("NASDEDUP_STATE", "/opt/nasdedup/state")
PENDING_STAGES = (0, 1, 2, 5)


def open_readonly(db_path):
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def tree_roots(conn):
    """sig -> the outermost directories carrying it, i.e. the copies nasdedup marked."""
    rows = conn.execute("""
        SELECT sig, path FROM dirs
         WHERE sig IN (SELECT DISTINCT tree FROM files WHERE tree IS NOT NULL)""")
    by_sig = defaultdict(list)
    for sig, path in rows:
        by_sig[sig].append(path)
    roots = {}
    for sig, paths in by_sig.items():
        paths.sort()
        roots[sig] = [p for p in paths
                      if not any(p.startswith(q.rstrip("/") + "/") for q in paths)]
    return roots


def root_of(path, roots):
    best = None
    for r in roots:
        if path.startswith(r.rstrip("/") + "/") and (best is None or len(r) > len(best)):
            best = r
    return best


def classify(conn):
    """Return (per-set summary rows, list of non-verified files)."""
    roots = tree_roots(conn)
    files = conn.execute("""
        SELECT tree, path, size, stage, l3 FROM files
         WHERE tree IS NOT NULL ORDER BY tree, path""").fetchall()

    # sig -> relpath -> [(root, path, size, stage, l3)]
    grid = defaultdict(lambda: defaultdict(list))
    unrooted = []
    for sig, path, size, stage, l3 in files:
        root = root_of(path, roots.get(sig, []))
        if root is None:
            unrooted.append((sig, path, size, stage, l3))
            continue
        grid[sig][os.path.relpath(path, root)].append((root, path, size, stage, l3))

    sets, problems = [], []
    for sig in sorted(set(grid) | {u[0] for u in unrooted}):
        counts, nbytes = Counter(), 0
        ncopies = len(roots.get(sig, []))
        for rel, copies in grid[sig].items():
            hashes = {c[4] for c in copies if c[3] == 3 and c[4]}
            for root, path, size, stage, l3 in copies:
                nbytes += size
                if stage == -1:
                    state = "error"
                elif stage in PENDING_STAGES or not l3:
                    state = "pending"
                elif len(hashes) > 1 or stage != 3:
                    state = "mismatch"
                elif len(copies) < ncopies:
                    state = "orphan"
                else:
                    state = "verified"
                counts[state] += 1
                if state != "verified":
                    problems.append((state, sig, path, size, stage))
        for sig_u, path, size, stage, _ in (u for u in unrooted if u[0] == sig):
            counts["orphan"] += 1
            nbytes += size
            problems.append(("orphan", sig_u, path, size, stage))
        sets.append((sig, ncopies, sum(counts.values()), nbytes, counts))
    return sets, problems


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.path.join(STATE_DIR, "dedup.db"))
    ap.add_argument("--list", type=int, default=50,
                    help="how many non-verified files to print (0 = all)")
    args = ap.parse_args(argv)

    conn = open_readonly(args.db)
    sets, problems = classify(conn)
    total = Counter()
    for _, _, _, _, counts in sets:
        total.update(counts)
    nfiles = sum(s[2] for s in sets)
    nbytes = sum(s[3] for s in sets)
    print(f"tree sets: {len(sets):,}  files: {nfiles:,}  bytes: {human(nbytes)}")
    for state in ("verified", "pending", "mismatch", "orphan", "error"):
        print(f"  {state:<9} {total[state]:,}")
    shown = problems if args.list == 0 else problems[:args.list]
    for state, sig, path, size, stage in shown:
        print(f"{state}\tstage={stage}\t{human(size)}\t{sig}\t{path}")
    if len(shown) < len(problems):
        print(f"... {len(problems) - len(shown):,} more (--list 0 for all)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
