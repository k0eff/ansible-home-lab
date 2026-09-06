#!/usr/bin/env python3
"""Find footage shared between the exported videos, and say where and how much.

The file-level SHA-256 already answered "are these the same file". These exports are
concatenations — a trip arrives as dozens of camera chunks joined into one long file —
so two exports can hold the same footage without being the same file. That is the
question this answers, and it is the only kind of evidence that could justify touching
anything in video/exported, where there is no source material anywhere else on the NAS.

Each file carries 60 perceptual hashes taken along its own timeline. A frame in A that
also appears in B gives a pair of timestamps; a *shared segment* gives many such pairs
all with the same offset t_a - t_b. So the offsets are clustered, and the largest
cluster's span in A is how much footage the two files actually share.

Two things would otherwise fake a match and are excluded:
  - degenerate frames (black, a fade, a white title card) hash to all-zeros or
    all-ones and match everything;
  - a hash that turns up across many unrelated files is a generic frame, not evidence.
"""

import json
import os
import sys
from collections import Counter, defaultdict

HAM = 6                # of 64 bits
MIN_CLUSTER = 3        # fewer than three aligned samples is coincidence, not a segment
GENERIC_FILES = 5      # a hash seen in this many files describes nothing


def popcount(x):
    return bin(x).count("1")


def main():
    vids = json.load(open(sys.argv[1]))
    for v in vids:
        v["pts"] = [(s["t"], s["h"]) for s in v["seq"]
                    if s["h"] is not None and 0 < popcount(s["h"]) < 64]
        v["step"] = (v["duration"] * 0.94) / 59 if v["duration"] else 60

    # A hash that appears in many different files is a generic frame — a sky, a road,
    # a fade to grey. Counting per file, not per sample: one file may legitimately hold
    # the same shot twice.
    seen = defaultdict(set)
    for i, v in enumerate(vids):
        for _, h in v["pts"]:
            seen[h].add(i)
    generic = {h for h, s in seen.items() if len(s) >= GENERIC_FILES}

    for v in vids:
        v["pts"] = [(t, h) for t, h in v["pts"] if h not in generic]

    out = []
    for i in range(len(vids)):
        a = vids[i]
        for j in range(i + 1, len(vids)):
            b = vids[j]
            if not a["pts"] or not b["pts"]:
                continue
            hits = []
            for ta, ha in a["pts"]:
                for tb, hb in b["pts"]:
                    if popcount(ha ^ hb) <= HAM:
                        hits.append((ta, tb))
            if len(hits) < MIN_CLUSTER:
                continue

            # Cluster by offset. Tolerance is one sampling step of the coarser file:
            # two samples of the same segment cannot be closer in agreement than that.
            tol = max(a["step"], b["step"])
            best = None
            for ta0, tb0 in hits:
                d0 = ta0 - tb0
                grp = [(ta, tb) for ta, tb in hits if abs((ta - tb) - d0) <= tol]
                if best is None or len(grp) > len(best):
                    best = grp
            if len(best) < MIN_CLUSTER:
                continue

            # The shared stretch has to fit inside both files. Taking only A's span
            # produced coverages above 100% — a four-minute clip credited with nine
            # minutes of shared footage — which is the signature of a cluster that is
            # not one segment but scattered matches that happen to share an offset.
            span_a = max(t for t, _ in best) - min(t for t, _ in best) + tol
            span_b = max(t for _, t in best) - min(t for _, t in best) + tol
            shared = min(span_a, span_b, a["duration"], b["duration"])
            # A real segment is sampled densely. If the cluster spans a third of a file
            # but holds a handful of samples, the middle did not match and the ends
            # agreeing on an offset is arithmetic, not footage.
            expect_a = shared / a["step"]
            expect_b = shared / b["step"]
            density = len(best) / max(1.0, min(expect_a, expect_b))
            out.append({
                "density": round(density, 2),
                "span_a": span_a, "span_b": span_b,
                "a": a["path"], "b": b["path"],
                "a_dur": a["duration"], "b_dur": b["duration"],
                "a_size": a["size"], "b_size": b["size"],
                "a_res": a.get("res"), "b_res": b.get("res"),
                "aligned": len(best),
                "a_samples": len(a["pts"]), "b_samples": len(b["pts"]),
                "shared_s": shared,
                "cover_a": shared / a["duration"] if a["duration"] else 0,
                "cover_b": shared / b["duration"] if b["duration"] else 0,
                "offset": round(best[0][0] - best[0][1], 1),
            })

    # Anything below half the samples a real segment would produce is discarded here
    # rather than left in the report to be argued about later.
    out = [r for r in out if r["density"] >= 0.5]
    out.sort(key=lambda r: -max(r["cover_a"], r["cover_b"]))
    json.dump(out, open(sys.argv[2], "w"), indent=1)

    print(f"файлове с проби: {sum(1 for v in vids if v['pts'])}/{len(vids)}")
    print(f"общи хешове изхвърлени като родови: {len(generic)}")
    print(f"двойки със споделен материал: {len(out)}\n")

    def hh(s):
        return f"{int(s)//3600:d}:{int(s)%3600//60:02d}:{int(s)%60:02d}"

    for r in out[:40]:
        print(f"{max(r['cover_a'], r['cover_b'])*100:5.1f}% споделено   "
              f"{hh(r['shared_s'])}   {r['aligned']} проби   плътност {r['density']}")
        print(f"    A {r['a_size']/1024**3:6.2f} GB {hh(r['a_dur'])} {r['a_res'] or '':>10}  "
              f"{os.path.basename(r['a'])}")
        print(f"    B {r['b_size']/1024**3:6.2f} GB {hh(r['b_dur'])} {r['b_res'] or '':>10}  "
              f"{os.path.basename(r['b'])}")
        print(f"    покритие: A {r['cover_a']*100:.0f}%  B {r['cover_b']*100:.0f}%  "
              f"отместване {r['offset']}s\n")


if __name__ == "__main__":
    main()
