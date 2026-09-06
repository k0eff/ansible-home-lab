#!/usr/bin/env python3
"""Fingerprint each exported video along its timeline, so shared source clips show up.

These exports are concatenations: a trip is filmed as dozens of GoPro chunks and joined
into one long file. Two exports can therefore hold the same footage without being the
same file — one might carry 45 clips and another 43 of those same 45. Comparing whole
files answers the wrong question, and the file-level SHA-256 already did that job.

So each video is sampled at fixed intervals and each sample becomes a 64-bit perceptual
hash. The result is a sequence per file. Shared footage then appears as a run of
matching hashes at some offset in both sequences — which also reveals *where* and *how
much* overlaps, not merely that something does.

Sampling is by keyframe seek (-ss before -i), so the cost is one seek and one frame
decode per sample rather than decoding hours of 4K. Nothing is deleted or written to
the share; the script only reads.
"""

import json
import os
import subprocess
import sys

SAMPLES = int(os.environ.get("VF_SAMPLES", "60"))
MARGIN = 0.03          # skip the first and last 3%: titles and fades match everything


def dhash_at(path, when):
    cmd = ["ffmpeg", "-v", "quiet", "-ss", f"{when:.2f}", "-i", path,
           "-frames:v", "1", "-vf", "scale=9:8:flags=area,format=gray",
           "-f", "rawvideo", "-"]
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


def main():
    probe = json.load(open(sys.argv[1]))
    out = []
    for i, rec in enumerate(probe, 1):
        dur = rec.get("duration") or 0
        if dur < 60:
            continue
        lo, hi = dur * MARGIN, dur * (1 - MARGIN)
        step = (hi - lo) / (SAMPLES - 1)
        seq = []
        for k in range(SAMPLES):
            t = lo + k * step
            seq.append({"t": round(t, 1), "h": dhash_at(rec["path"], t)})
        out.append({"path": rec["path"], "duration": dur, "size": rec["size"],
                    "res": rec.get("res"), "seq": seq})
        done = sum(1 for s in seq if s["h"] is not None)
        print(f"  {i}/{len(probe)}  {done}/{SAMPLES} проби  "
              f"{os.path.basename(rec['path'])[:60]}", file=sys.stderr, flush=True)
        json.dump(out, open(sys.argv[2], "w"))
    json.dump(out, open(sys.argv[2], "w"))
    print(f"готово: {len(out)} файла")


if __name__ == "__main__":
    main()
