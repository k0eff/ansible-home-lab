#!/usr/bin/env python3
"""Build the quarantine script from the scan results and the owner's retention rules.

Produces moves, never deletions: everything goes into one dated folder with its full
path preserved underneath, so any of it can be put back by reversing the move. The NAS
has no second copy, which is the entire reason this is mv and not rm.

Every line that comes out has evidence behind it — either a full SHA-256 that matches a
file which stays, or the three-layer perceptual check for Google Photos re-encodes,
where byte hashing structurally cannot help because Google re-compresses on upload.
"""

import bisect
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

R = "/mnt/krasi/"
DB = "dedup.db"
GP = "gp_verified.json"


def h(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or u == "TB":
            return f"{n:.1f} {u}"
        n /= 1024


COPY = re.compile(r"(-|_|\s)(copy|копие)|\(\d+\)$|(-|_|\s)\d{1,2}$", re.I)
CAMERA = re.compile(r"^(IMG|VID|MVI|DSC|DSCN|GX|GOPR|GP|P|PXL|MOV|DJI|SAM)[-_]?\d+$", re.I)
STAMP = re.compile(r"^\d{4}[-._]?\d{2}[-._]?\d{2}([-._\s]\d{2}[-._:]?\d{2}([-._:]?\d{2})?)?$")
RANDOM = re.compile(r"^[a-z0-9]{16,}$", re.I)
ISOSUF = re.compile(r"\d{4}-\d{2}-\d{2}T\d{6}")
WORD = re.compile(r"[A-Za-zА-Яа-я]{3,}")


def score(name):
    """Higher means a more descriptive filename — the owner's rule for same-folder pairs."""
    stem = os.path.splitext(name)[0]
    s = 0.0
    words = [w for w in WORD.findall(stem)
             if w.lower() not in ("mp4", "mov", "jpg", "avi", "the", "and")]
    s += 10 * len(words)
    s += min(len(stem), 60) * 0.05
    if COPY.search(stem):
        s -= 25
    if CAMERA.match(stem):
        s -= 15
    if STAMP.match(stem):
        s -= 8
    if RANDOM.match(stem):
        s -= 30
    # An ISO timestamp appended by an export tool is machine noise, not description.
    # Without this the scorer kept "20231220_192120 - 2024-12-03T200342.679685Z.mp4"
    # over the clean "20231220_192120.mp4", which is the opposite of the rule.
    if ISOSUF.search(stem):
        s -= 20
    if re.search(r"\d{4}[-.]\d{2}[-.]\d{2}", stem):
        s += 3
    return s


SENS = re.compile(r"litecoin|digitalcoin|doge|wallet|thunderbird|\.eml$|/important/|crypto", re.I)
# The two folders holding the baba Krastinka originals. Not a rule among rules — a
# filter applied to the finished list, so that no rule added later can reach inside
# them by accident, whatever evidence it thinks it has. The owner's instruction is
# that the originals are not touched at all, and this is what enforces it.
NEVER = [
    "video/exported/baba Krastinka/",
    "Pictures/Pictures-by-years/semeini snimki/baba/",
]
BABA = re.compile(r"krastinka|кръстинка|baba", re.I)
BABA_KEEP = [re.compile(r"^Pictures/Pictures-by-years/semeini snimki/baba/"),
             re.compile(r"^video/exported/baba Krastinka/")]
ICY = re.compile(r"015-ICYGEN", re.I)
EXPORTED = re.compile(r"^video/exported/")

# Pairs in video/exported that are the same footage in two encodings, so the bytes never
# match and the duplicate pass cannot see them. Each entry is (goes, stays) and is here
# only because the owner ruled on it after the evidence: identical duration, mean Hamming
# distance under one bit of 64 across 213 sampled moments, and an audio envelope
# correlation of +1.00. The lower-bitrate copy goes; nothing here is inferred.
OWNER_TRANSCODE = [
    ("video/exported/2019-09-07--Pavel-bania--h264.mp4",      # h264 High, 16.0 Mbps
     "video/exported/2019-09-07--Pavel-bania.mp4"),           # hevc Main, 34.6 Mbps
]


def main():
    c = sqlite3.connect(DB)
    size = {p.replace(R, ""): sz for p, sz in c.execute("SELECT path,size FROM files")}

    groups = defaultdict(list)
    for l3, p, sz in c.execute("SELECT l3,path,size FROM files WHERE l3 IS NOT NULL"):
        groups[l3].append((p.replace(R, ""), sz))
    dups = [sorted(v) for v in groups.values() if len(v) > 1]

    moves = []          # (reason, path)
    protect = set()     # baba copies promoted to survivors — no later rule may move them

    def take(reason, paths):
        for p in paths:
            moves.append((reason, p))

    for v in dups:
        paths = [p for p, _ in v]
        if any(SENS.search(p) for p in paths):
            continue                                  # crypto, mail, important/ — never
        # video/exported held the only copy of everything in it, so it was excluded
        # wholesale until the owner ruled that byte-identical copies there are cleaned
        # like anywhere else. Every group in `dups` is a full SHA-256 match, and both
        # groups that fall here were re-hashed independently of the database before a
        # single line was written. Anything short of a byte match is still untouched:
        # a re-encode of the same footage is a different file and stays.
        if any(EXPORTED.match(p) for p in paths) and len(set(paths)) < 2:
            continue

        if any(BABA.search(p) for p in paths):
            keep = [p for p in paths if any(r.search(p) for r in BABA_KEEP)]
            # The owner's rule is two copies in two different folders. For the video
            # clips both blessed folders hold one, so the rule is met by the blessed
            # set alone. For three of the photographs only one blessed folder has a
            # copy, and moving the other would leave a single copy of irreplaceable
            # material — so a second survivor is promoted rather than quarantined.
            if keep:
                rest = [p for p in paths if p not in keep]
                while len(keep) < 2 and rest:
                    protect.add(rest[0])
                    keep.append(rest.pop(0))
                take("baba-third-copy", rest)
            continue

        if any(ICY.search(p) for p in paths):
            outside = [p for p in paths if not ICY.search(p)]
            inside = [p for p in paths if ICY.search(p)]
            if outside:
                take("icygen-copy-exists-outside", inside)
            else:
                ranked = sorted(inside, key=lambda p: -score(os.path.basename(p)))
                take("icygen-keep-one", ranked[1:])
            continue

        if len({p.rsplit("/", 1)[0] for p in paths}) == 1:
            ranked = sorted(((score(os.path.basename(p)), p) for p in paths), reverse=True)
            if ranked[0][0] - ranked[1][0] >= 8:      # a clear winner, otherwise skip
                take("same-folder-keeps-descriptive-name", [p for _, p in ranked[1:]])
            continue

        tops = {"/".join(p.split("/")[:2]) for p in paths}
        if tops == {"Pictures/Pictures-by-years", "Pictures/Samsung Fold 4"}:
            take("phone-dump-superseded-by-curated-tree",
                 [p for p in paths if p.startswith("Pictures/Samsung Fold 4")])

    for goes, stays in OWNER_TRANSCODE:
        if goes in size and stays in size:
            moves.append(("owner-ruled-transcode", goes))

    for r in json.load(open(GP)):
        if r["verdict"] == "confirmed":
            g = r["gp"].replace(R, "")
            if g in protect or any(orig.startswith(n) for n in NEVER
                                   for orig in [r["orig"].replace(R, "")]):
                continue
            moves.append(("gphotos-reencode-verified", g))

    dirs = {p for p, in c.execute("SELECT DISTINCT dirpath FROM files")}
    nm = sorted({d[:d.index("/node_modules/") + len("/node_modules")]
                 if "/node_modules/" in d else d
                 for d in dirs if "/node_modules" in d})
    for d in [x for x in nm
              if x.endswith("node_modules")
              and not any(x.startswith(q.rstrip("/") + "/") for q in nm if q != x)]:
        moves.append(("node_modules-regenerable", d.replace(R, "")))

    # node_modules entries are directories, so they have no row in `size` and would
    # report as 0 B in the summary. Their weight is the sum of what is under them.
    dirsize = defaultdict(int)
    nmdirs = sorted(q for reason, q in moves if reason == "node_modules-regenerable")
    for p, sz in size.items():
        i = bisect.bisect_right(nmdirs, p) - 1
        if i >= 0 and p.startswith(nmdirs[i] + "/"):
            dirsize[nmdirs[i]] += sz

    blocked = [p for _, p in moves if any(p.startswith(n) for n in NEVER)]
    moves = [(r, p) for r, p in moves if not any(p.startswith(n) for n in NEVER)]

    seen, items = set(), []
    for reason, p in moves:
        if p in seen:
            continue
        seen.add(p)
        items.append((reason, p, size.get(p, dirsize.get(p, 0))))

    by = defaultdict(lambda: [0, 0])
    for reason, p, s in items:
        by[reason][0] += s
        by[reason][1] += 1

    out = sys.stdout
    w = out.write
    w("#!/bin/sh\n")
    w("# Карантина, не изтриване.\n#\n")
    w("# Всеки файл се мести в папка с дата, със запазен пълен път отдолу. Връщането е\n")
    w("# обратното преместване. Нищо не се трие — NAS-ът няма архив, и точно затова.\n#\n")
    for reason, (b, n) in sorted(by.items(), key=lambda x: -x[1][0]):
        w(f"#   {h(b):>10}  {n:>7,}  {reason}\n")
    w(f"#   {'-' * 10}\n")
    w(f"#   {h(sum(x[0] for x in by.values())):>10}  "
      f"{sum(x[1] for x in by.values()):>7,}  ОБЩО\n#\n")
    w("# Недосегаеми (спрени от NEVER, независимо от правило):\n")
    for n in NEVER:
        w(f"#   {n}\n")
    w(f"# спрени редове: {len(blocked)}\n#\n")
    w("# От video/exported: групи с пълно съвпадение на SHA-256, плюс прекодиранията,\n")
    w("# по които собственикът се произнесе поотделно.\n")
    for reason, q, _ in items:
        if q.startswith("video/exported/"):
            w(f"#   {q}\n")
    w("#\n# НЕ включва: прекодирания (различни байтове = различен файл), крипто, поща,\n")
    w("# backup/important/, .hdr файловете, Photomatix рендерите, и двойките\n")
    w("# Google Photos с по-слабо доказателство.\n\n")
    w('set -eu\n')
    w('SRC="${SRC:-/mnt/krasi}"\n')
    w('Q="${Q:-$SRC/@quarantine-$(date +%Y-%m-%d)}"\n')
    w('DRY="${DRY:-1}"\n')
    w('[ "$DRY" = "1" ] && echo "ПРОБНО ПУСКАНЕ. DRY=0 за истинско местене." >&2\n')
    w('if [ "$DRY" != "1" ]; then\n')
    w('  touch "$SRC/.wtest" 2>/dev/null || { echo "СПИРАМ: $SRC е read-only." >&2; exit 2; }\n')
    w('  rm -f "$SRC/.wtest"\n')
    w('fi\n')
    w('moved=0; missing=0\n')
    w('mv1() {\n')
    w('  s="$SRC/$1"; d="$Q/$1"\n')
    w('  [ -e "$s" ] || { echo "ЛИПСВА: $1" >&2; missing=$((missing+1)); return 0; }\n')
    w('  if [ "$DRY" = "1" ]; then echo "би преместил: $1"\n')
    w('  else mkdir -p "$(dirname "$d")" && mv -n "$s" "$d" && moved=$((moved+1)); fi\n')
    w('}\n\n')
    for reason, p, s in items:
        w(f"mv1 {json.dumps(p, ensure_ascii=False)}\n")
    w('\necho "преместени: $moved   липсващи: $missing" >&2\n')


if __name__ == "__main__":
    main()
