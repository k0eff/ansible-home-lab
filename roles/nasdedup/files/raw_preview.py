#!/usr/bin/env python3
"""Decide the RAW pairs the picture layer left undecided, through their embedded previews.

verify_gphotos.py hashes a decoded frame, and ffmpeg cannot decode a Canon CR2 or a
Photoshop PSD, so every pair with one of those on either side came back "undecided" —
29,098 pairs, 510 GB. No RAW decoder is needed to settle them: both formats carry a
full-size or near-full-size JPEG the camera or Photoshop rendered, and that JPEG is what
anyone would see. It is cut out of the container by parsing the container, never by
guessing, and hashed exactly the way the other layer hashes a frame.

  CR2   a TIFF. IFD0's strip is the full-size JPEG the camera wrote; IFD1's
        JPEGInterchangeFormat is a smaller one. The larger wins.
  PSD   image resource 1036 (or 1033 in old files) is a JFIF thumbnail after a
        28-byte header.
  else  a JPEG is decoded as it is; any other file falls back to the largest
        complete FFD8…FFD9 run inside it.

Read-only. Nothing is moved, renamed or written beside the photographs: the only output
is the report, and every verdict is appended and flushed so a re-run resumes.

    raw_preview.py verified.json out.json      # verify_gphotos output, or build_pairs output
"""

import json
import os
import struct
import subprocess
import sys
from collections import Counter

HAMMING_MAX = 8            # same threshold as verify_gphotos: re-encode moves a few bits
RAW_EXT = (".cr2", ".psd", ".psb")
HEAD_BYTES = 64 * 1024 * 1024


def _tiff_jpegs(data):
    """Every JPEG a TIFF-shaped RAW points at, as (offset, length)."""
    if data[:2] not in (b"II", b"MM"):
        return []
    bo = "<" if data[:2] == b"II" else ">"
    found = []
    try:
        off = struct.unpack_from(bo + "I", data, 4)[0]
        seen = set()
        while off and off not in seen and off + 2 <= len(data):
            seen.add(off)
            count = struct.unpack_from(bo + "H", data, off)[0]
            tags = {}
            for e in range(count):
                p = off + 2 + e * 12
                tag, typ, cnt = struct.unpack_from(bo + "HHI", data, p)
                if typ == 3 and cnt == 1:
                    val = struct.unpack_from(bo + "H", data, p + 8)[0]
                else:
                    val = struct.unpack_from(bo + "I", data, p + 8)[0]
                tags[tag] = val
            if 0x0111 in tags and 0x0117 in tags:          # StripOffsets / StripByteCounts
                found.append((tags[0x0111], tags[0x0117]))
            if 0x0201 in tags and 0x0202 in tags:          # JPEGInterchangeFormat / Length
                found.append((tags[0x0201], tags[0x0202]))
            off = struct.unpack_from(bo + "I", data, off + 2 + count * 12)[0]
    except struct.error:
        pass
    return found


def _psd_jpegs(data):
    if data[:4] != b"8BPS":
        return []
    found = []
    try:
        p = 26
        p += 4 + struct.unpack_from(">I", data, p)[0]      # colour mode data
        end = p + 4 + struct.unpack_from(">I", data, p)[0]
        p += 4
        while p + 12 <= end:
            if data[p:p + 4] != b"8BIM":
                break
            rid = struct.unpack_from(">H", data, p + 4)[0]
            nlen = data[p + 6]
            q = p + 6 + ((1 + nlen + 1) & ~1)              # Pascal name, padded to even
            size = struct.unpack_from(">I", data, q)[0]
            body = q + 4
            if rid in (1033, 1036) and size > 28:
                found.append((body + 28, size - 28))
            p = body + ((size + 1) & ~1)
    except (struct.error, IndexError):
        pass
    return found


def _scan_jpegs(data):
    found, i = [], data.find(b"\xff\xd8\xff")
    while i >= 0:
        j = data.find(b"\xff\xd9", i + 3)
        if j < 0:
            break
        found.append((i, j + 2 - i))
        i = data.find(b"\xff\xd8\xff", j + 2)
    return found


def extract_preview(path):
    """The largest valid embedded JPEG, as bytes, or None."""
    # PSDs run to gigabytes, but the image resources and a CR2's previews sit near the
    # start; the pixel data after them is never needed.
    with open(path, "rb") as fh:
        data = fh.read(HEAD_BYTES)
    if data[:3] == b"\xff\xd8\xff":
        return data
    cands = _psd_jpegs(data) or _tiff_jpegs(data) or _scan_jpegs(data)
    best = None
    for off, ln in cands:
        blob = data[off:off + ln]
        if len(blob) == ln and blob[:2] == b"\xff\xd8" and (best is None or ln > len(best)):
            best = blob
    return best


def gray9x8(jpeg):
    """72 greyscale bytes of the JPEG scaled to 9x8, decoded by ffmpeg from stdin."""
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "quiet", "-f", "image2pipe", "-i", "-", "-frames:v", "1",
             "-vf", "scale=9:8:flags=area,format=gray", "-f", "rawvideo", "-"],
            input=jpeg, capture_output=True, timeout=180).stdout
    except Exception:
        return None
    return raw[:72] if len(raw) >= 72 else None


def dhash(gray):
    bits = 0
    for row in range(8):
        b = row * 9
        for col in range(8):
            bits = (bits << 1) | (1 if gray[b + col] > gray[b + col + 1] else 0)
    return bits


def compare(a, b, decode=gray9x8):
    ja, jb = extract_preview(a), extract_preview(b)
    if ja is None or jb is None:
        return "undecided", "no embedded preview in one of the pair", None
    ga, gb = decode(ja), decode(jb)
    if ga is None or gb is None:
        return "undecided", "embedded preview did not decode", None
    dist = bin(dhash(ga) ^ dhash(gb)).count("1")
    if dist > HAMMING_MAX:
        return "rejected", f"preview differs, hamming {dist}/64", dist
    return "confirmed", f"preview hamming {dist}/64", dist


def raw_pairs(rows):
    """Pairs with a RAW side, from verify_gphotos output (undecided only) or build_pairs."""
    for r in rows:
        if isinstance(r, dict):
            if r.get("verdict") != "undecided":
                continue
            a, b, sa, sb = r["gp"], r["orig"], r.get("gp_size", 0), r.get("orig_size", 0)
        else:
            a, b, sa, sb = r
        if a.lower().endswith(RAW_EXT) or b.lower().endswith(RAW_EXT):
            yield a, b, sa or 0, sb or 0


def main():
    pairs = list(raw_pairs(json.load(open(sys.argv[1]))))
    out_path = sys.argv[2]
    part = out_path + ".part"
    done = {}
    if os.path.exists(part):
        with open(part) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                done[(r["a"], r["b"])] = r
    out = []
    with open(part, "a") as fh:
        for i, (a, b, sa, sb) in enumerate(pairs, 1):
            r = done.get((a, b))
            if r is None:
                v, detail, dist = compare(a, b)
                r = {"a": a, "b": b, "a_size": sa, "b_size": sb,
                     "verdict": v, "detail": detail, "hamming": dist}
                fh.write(json.dumps(r) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            out.append(r)
            if i % 100 == 0:
                print(f"  {i}/{len(pairs)}", file=sys.stderr, flush=True)
    json.dump(out, open(out_path, "w"), indent=1)
    print(f"RAW двойки: {len(out)} (нищо не е преместено)")
    for v, n in Counter(r["verdict"] for r in out).most_common():
        gb = sum(r["a_size"] for r in out if r["verdict"] == v) / 1024 ** 3
        print(f"{v}: {n} двойки, {gb:.1f} GB")


if __name__ == "__main__":
    main()
