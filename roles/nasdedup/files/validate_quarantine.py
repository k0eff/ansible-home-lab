#!/usr/bin/env python3
"""Check the generated quarantine script against every rule the owner has set.

The generator and this checker are deliberately separate programs reading the same
database. The generator decides; this one only asks whether anything the owner ruled
out has slipped through, so a mistake in one has to be repeated independently in the
other to survive.

The last check is the one that matters most: nothing may lose its last copy. That is
not the same as "some member of every SHA-256 group stays", because a Google Photos
copy is a re-encode — it has its own hash, and a group made only of Google copies
disappearing is correct as long as the local original it was verified against stays.
"""

import json
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict

R = "/mnt/krasi/"
NEVER = ["video/exported/baba Krastinka/",
         "Pictures/Pictures-by-years/semeini snimki/baba/"]
BABA = re.compile(r"krastinka|кръстинка|baba", re.I)
SENS = re.compile(r"litecoin|digitalcoin|doge|wallet|thunderbird|\.eml$|/important/|crypto", re.I)


def main():
    script = sys.argv[1] if len(sys.argv) > 1 else "quarantine.sh"
    moved = {json.loads(l[4:].strip()) for l in open(script) if l.startswith("mv1 ")}

    c = sqlite3.connect("dedup.db")
    sha, groups = {}, defaultdict(list)
    for p, h in c.execute("SELECT path,l3 FROM files WHERE l3 IS NOT NULL"):
        p = p.replace(R, "")
        sha[p] = h
        groups[h].append(p)
    gp = {r["gp"].replace(R, ""): r["orig"].replace(R, "")
          for r in json.load(open("gp_verified.json"))}

    ok = True

    def chk(cond, msg):
        nonlocal ok
        ok &= bool(cond)
        print(("✓ " if cond else "✗ ") + msg)

    print(f"премествания: {len(moved)}")
    chk(not [p for p in moved if any(p.startswith(n) for n in NEVER)],
        "нищо от недосегаемите папки")
    chk(not [g for g, o in gp.items()
             if g in moved and any(o.startswith(n) for n in NEVER)],
        "нито едно Google Photos копие с оригинал в защитена папка")
    chk(not [v for v in groups.values()
             if len(v) > 1
             and any(BABA.search(unicodedata.normalize("NFC", p)) for p in v)
             and len([p for p in v if p not in moved]) < 2],
        "всяка baba група запазва поне 2 копия")
    chk(not [p for p in moved if SENS.search(p)], "нула чувствителни файлове")

    # A transcode has no SHA twin by definition, so the survivor is named, not looked up.
    TRANSCODE = {"video/exported/2019-09-07--Pavel-bania--h264.mp4":
                 "video/exported/2019-09-07--Pavel-bania.mp4"}
    for goes, stays in TRANSCODE.items():
        if goes in moved:
            chk(stays not in moved and stays in sha or stays not in moved,
                f"оцелява по-високият битрейт: {stays.split('/')[-1]}")

    exp = sorted(p for p in moved if p.startswith("video/exported/"))
    print(f"  от video/exported ({len(exp)}):")
    for p in exp:
        alive = ([TRANSCODE[p]] if p in TRANSCODE
                 else [q for q in groups.get(sha.get(p), []) if q not in moved])
        print(f"    {p}")
        print(f"      SHA {(sha.get(p) or '')[:16]}…  остава: {alive}")
        chk(alive, f"има оцеляло копие за {p.split('/')[-1]}")

    lost = []
    for v in groups.values():
        if any(p not in moved for p in v):
            continue
        origs = {gp[p] for p in v if p in gp}
        if not [o for o in origs if o not in moved]:
            lost.append(v)
    chk(not lost, "нищо не губи последното си копие")
    for v in lost[:10]:
        print(f"    ГУБИ СЕ: {v}")

    print("\nВСИЧКО ОК" if ok else "\nИМА ПРОБЛЕМ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
