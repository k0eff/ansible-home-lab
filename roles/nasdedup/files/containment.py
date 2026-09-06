#!/usr/bin/env python3
"""Decide whether one video is contained, whole, inside another.

The question is not "do these share a moment" — the sixty-sample pass already answered
that, and answered it too loosely: a handful of frames agreeing on an offset is
arithmetic. The question here is whether *every second* of the source appears in the
container at one fixed offset, because only that would make the source redundant.

Both files are hashed at a fixed rate, so a shared stretch is a constant index shift.
Candidate shifts are proposed by exact hash agreement — cheap, and a re-encode still
reproduces many frames bit for bit at this resolution — and then each candidate is
verified against the *whole* source, counting how many of its samples find a partner
within a small Hamming distance.

Two failure modes are reported rather than hidden:
  - a source that matches over only part of its length is not contained, however
    convincing the matching part looks;
  - a source whose own frames are nearly all identical (a static shot) matches almost
    anything, so its self-similarity is measured and printed alongside the verdict.
"""

import json
import os
import sys
from collections import Counter

HAM = 6
GOOD = 0.98            # fraction of source samples that must find a partner


def popcount(x):
    return bin(x).count("1")


def selfsim(h):
    """How much a clip repeats itself. A static shot scores near 1 and proves nothing."""
    if len(h) < 4:
        return 1.0
    step = max(1, len(h) // 200)
    s = h[::step]
    hits = sum(1 for i in range(len(s)) for j in range(i + 1, len(s))
               if popcount(s[i] ^ s[j]) <= HAM)
    pairs = len(s) * (len(s) - 1) / 2
    return hits / pairs if pairs else 1.0


def check(src, con):
    hs, hc = src["h"], con["h"]
    if not hs or not hc:
        return None
    pos = {}
    for j, v in enumerate(hc):
        pos.setdefault(v, []).append(j)

    votes = Counter()
    for i, v in enumerate(hs):
        for j in pos.get(v, ())[:64]:
            votes[j - i] += 1
    if not votes:
        return {"cover": 0.0, "offset": None, "votes": 0}

    best = None
    for off, nv in votes.most_common(12):
        ok = 0
        for i, v in enumerate(hs):
            j = i + off
            if 0 <= j < len(hc) and popcount(v ^ hc[j]) <= HAM:
                ok += 1
        cover = ok / len(hs)
        if best is None or cover > best["cover"]:
            best = {"cover": cover, "offset": off, "votes": nv, "matched": ok}
    return best


def main():
    con = json.load(open(sys.argv[1]))
    print(f"контейнер: {os.path.basename(con['path'])}  "
          f"{con['n']} проби, {con['n']/con['fps']/60:.1f} мин\n")
    rows = []
    for f in sys.argv[2:]:
        src = json.load(open(f))
        r = check(src, con)
        ss = selfsim(src["h"])
        rows.append((os.path.basename(src["path"]), src["n"], r, ss))

    for name, n, r, ss in sorted(rows, key=lambda x: -(x[2]["cover"] if x[2] else 0)):
        if r is None:
            print(f"  —      {name}: няма проби")
            continue
        off_s = r["offset"] / con["fps"] if r["offset"] is not None else 0
        flag = "ЦЯЛОСТНО" if r["cover"] >= GOOD else "частично"
        warn = "  ⚠ статичен клип, съвпада с всичко" if ss > 0.5 else ""
        print(f"  {r['cover']*100:5.1f}%  {flag:9} {name:28} "
              f"{n/con['fps']/60:5.1f} мин  при {int(off_s)//60}:{int(off_s)%60:02d}"
              f"  самоприлика {ss:.2f}{warn}")


if __name__ == "__main__":
    main()
