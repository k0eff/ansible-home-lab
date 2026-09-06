#!/usr/bin/env python3
"""Triple-check that a Google Photos file is a re-encode of a local original.

Filename equality is a hint, not proof, and byte hashing cannot help at all: Google
re-compresses on upload, so every byte differs and SHA-256 correctly calls them
different files. Three independent layers decide instead.

A first version of this used the capture timestamp as the second layer and rejected
seven of sixty sample pairs on it. Every one of those was a false rejection: Google
rewrites creation_time to the *upload* time, so a clip filmed at 11:42:03 came back
carrying 12:20:06, and one carried 1970-01-01. The metadata layer had to be rebuilt
around fields Google actually preserves.

  1. name       what suggested the pair. Weak on its own — every camera makes IMG_1234.
  2. shape      duration for video, aspect ratio for stills. Both survive re-encoding;
                absolute dimensions do not, because Google downscales.
  3. picture    a perceptual hash of the decoded frame. The only layer that compares
                what you would actually see, and the only one the re-encode cannot fool.

EXIF DateTimeOriginal is read where present and used as corroboration, never as grounds
for rejection — it is missing or rewritten too often on the Google side to be trusted.

Everything runs through ffmpeg, so nothing needs installing. A frame is scaled to 9x8
greyscale and turned into a 64-bit difference hash: each bit records whether a pixel is
brighter than its right-hand neighbour, which is stable under rescaling and
requantisation but not under being a different photograph.
"""

import json
import os
import re
import struct
import subprocess
import sys
from collections import Counter

HAMMING_MAX = 8           # of 64 bits. Re-encoding moves a few; a different shot ~32.
DURATION_TOL = 1.5        # seconds
ASPECT_TOL = 0.02         # 2%


def probe(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, timeout=90).stdout
        d = json.loads(out or b"{}")
    except Exception:
        return None
    fmt = d.get("format", {})
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    try:
        dur = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    w, hgt = v.get("width") or 0, v.get("height") or 0
    return {"duration": dur, "w": w, "h": hgt,
            "aspect": (w / hgt) if w and hgt else 0,
            "is_video": bool(v.get("nb_frames") or dur > 0.5)}


EXIF_DTO = 0x9003
EXIF_SUB = 0x8769


def exif_datetime(path):
    """DateTimeOriginal from a JPEG, parsed rather than pattern-matched.

    Grepping the header for anything shaped like a date would happily return DateTime
    (last modification) from one file and DateTimeOriginal from the other, and compare
    two different facts. Only tag 0x9003 counts.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(131072)
    except OSError:
        return None
    i = head.find(b"Exif\x00\x00")
    if i < 0:
        return None
    tiff = i + 6
    if len(head) < tiff + 8:
        return None
    bo = "<" if head[tiff:tiff + 2] == b"II" else ">"
    try:
        off = struct.unpack_from(bo + "I", head, tiff + 4)[0]
        for _ in range(3):                       # IFD0, then the Exif sub-IFD
            base = tiff + off
            count = struct.unpack_from(bo + "H", head, base)[0]
            nxt = None
            for e in range(count):
                p = base + 2 + e * 12
                tag, typ, cnt = struct.unpack_from(bo + "HHI", head, p)
                if tag == EXIF_DTO and typ == 2:
                    vo = struct.unpack_from(bo + "I", head, p + 8)[0]
                    s = head[tiff + vo: tiff + vo + 19].decode("ascii", "ignore")
                    return s if re.match(r"\d{4}:\d{2}:\d{2} ", s) else None
                if tag == EXIF_SUB:
                    nxt = struct.unpack_from(bo + "I", head, p + 8)[0]
            if nxt is None:
                return None
            off = nxt
    except Exception:
        return None
    return None


def dhash(path, duration):
    cmd = ["ffmpeg", "-v", "quiet"]
    if duration and duration > 2:
        # Seek in: opening frames are often black or a fade, which makes unrelated
        # clips look identical.
        cmd += ["-ss", f"{duration * 0.25:.2f}"]
    cmd += ["-i", path, "-frames:v", "1",
            "-vf", "scale=9:8:flags=area,format=gray", "-f", "rawvideo", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=180).stdout
    except Exception:
        return None
    if len(raw) < 72:
        return None
    bits = 0
    for row in range(8):
        b = row * 9
        for col in range(8):
            bits = (bits << 1) | (1 if raw[b + col] > raw[b + col + 1] else 0)
    return bits


def compare(gp, orig):
    pg, po = probe(gp), probe(orig)
    if not pg or not po:
        return "unreadable", "ffprobe failed on one of the pair", None

    reasons = []

    # Layer 2 — shape. Duration for video, aspect ratio for stills.
    if pg["duration"] > 0.5 and po["duration"] > 0.5:
        d = abs(pg["duration"] - po["duration"])
        if d > DURATION_TOL:
            return "rejected", (f"duration differs by {d:.1f}s "
                                f"({pg['duration']:.1f} vs {po['duration']:.1f})"), None
        reasons.append(f"duration {pg['duration']:.1f}s")
    elif pg["aspect"] and po["aspect"]:
        # Transposed dimensions are the same photograph, not a different one. The
        # original keeps it landscape with an EXIF orientation flag; Google bakes the
        # rotation in and stores it portrait. Eight of sixty sample pairs were rejected
        # on this before it was accounted for — 3072x4096 against 4096x3072, same pixel
        # count, rotated. ffmpeg applies the flag when decoding, so the picture hash
        # compares the two correctly; only this check needed to stop objecting.
        rel = abs(pg["aspect"] - po["aspect"]) / max(pg["aspect"], po["aspect"])
        inv = abs(pg["aspect"] - 1 / po["aspect"]) / max(pg["aspect"], 1 / po["aspect"])
        if min(rel, inv) > ASPECT_TOL:
            return "rejected", (f"aspect differs: {pg['w']}x{pg['h']} "
                                f"vs {po['w']}x{po['h']}"), None
        rot = " (rotated)" if inv < rel else ""
        reasons.append(f"aspect {pg['w']}x{pg['h']} vs {po['w']}x{po['h']}{rot}")

    # Layer 3 — the picture. Always computed, even when layer 2 was inconclusive.
    hg, ho = dhash(gp, pg["duration"]), dhash(orig, po["duration"])
    if hg is None or ho is None:
        return "undecided", "could not decode a frame from one of the pair", None
    dist = bin(hg ^ ho).count("1")
    if dist > HAMMING_MAX:
        return "rejected", f"picture differs, hamming {dist}/64", dist
    reasons.append(f"picture hamming {dist}/64")

    # Corroboration only. Google rewrites capture time on upload, so a mismatch here
    # says nothing and must never reject a pair.
    eg, eo = exif_datetime(gp), exif_datetime(orig)
    if eg and eo:
        reasons.append("EXIF capture matches" if eg == eo
                       else f"EXIF differs ({eg} vs {eo}) — Google rewrites this, ignored")

    strong = dist <= 4 and len(reasons) >= 2
    return ("confirmed" if strong else "probable"), " + ".join(reasons), dist


def main():
    """Results are written as they are produced, and a re-run resumes from them.

    The first version held every verdict in memory and wrote the file once at the end.
    A reboot thirty-five thousand pairs into a thirty-nine thousand pair run destroyed
    ten hours of work that was, by then, entirely decided — none of it had ever reached
    the disk. One line per pair, appended and flushed, costs nothing and cannot lose
    more than the pair being measured when the power goes.
    """
    pairs = json.load(open(sys.argv[1]))
    out_path = sys.argv[2]
    part = out_path + ".part"

    done = {}
    if os.path.exists(part):
        with open(part) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue            # a half-written last line after a hard stop
                done[(r["gp"], r["orig"])] = r
        print(f"  подновяване: {len(done)} готови двойки", file=sys.stderr, flush=True)

    out = []
    with open(part, "a") as fh:
        for i, (gp, orig, gsz, osz) in enumerate(pairs, 1):
            r = done.get((gp, orig))
            if r is None:
                v, detail, dist = compare(gp, orig)
                r = {"gp": gp, "orig": orig, "gp_size": gsz, "orig_size": osz,
                     "verdict": v, "detail": detail, "hamming": dist}
                fh.write(json.dumps(r) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            out.append(r)
            if i % 25 == 0:
                print(f"  {i}/{len(pairs)}", file=sys.stderr, flush=True)

    json.dump(out, open(out_path, "w"), indent=1)
    for v, n in Counter(r["verdict"] for r in out).most_common():
        b = sum(r["gp_size"] for r in out if r["verdict"] == v)
        print(f"{v}: {n} двойки, {b/1024**3:.1f} GB в Google Photos копията")


if __name__ == "__main__":
    main()
