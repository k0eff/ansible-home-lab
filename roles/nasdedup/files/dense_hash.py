#!/usr/bin/env python3
"""Hash a whole video at a fixed rate in one decode pass.

Seeking per sample is what the earlier fingerprint did, and it is right when you only
want sixty points from a file. It is the wrong tool for proving containment, because
proof needs every second of the source accounted for, and thousands of seeks cost far
more than one sequential decode.

ffmpeg does the whole job in a single pipe: decode, drop to a fixed frame rate, scale
to 9x8 greyscale, emit raw bytes. Each 72-byte frame becomes one 64-bit difference
hash. Output is a JSON list, in order, one entry per sampled instant.
"""

import json
import subprocess
import sys

FPS = 2                     # samples per second, so phase error is at most 0.25 s


def main():
    path, out = sys.argv[1], sys.argv[2]
    fps = float(sys.argv[3]) if len(sys.argv) > 3 else FPS
    p = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", path,
         "-vf", f"fps={fps},scale=9:8:flags=area,format=gray",
         "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    hashes, buf = [], b""
    while True:
        chunk = p.stdout.read(72 * 512)
        if not chunk:
            break
        buf += chunk
        n = len(buf) // 72
        for k in range(n):
            f = buf[k * 72:(k + 1) * 72]
            bits = 0
            for row in range(8):
                b = row * 9
                for col in range(8):
                    bits = (bits << 1) | (1 if f[b + col] > f[b + col + 1] else 0)
            hashes.append(bits)
        buf = buf[n * 72:]
    p.wait()
    err = p.stderr.read().decode("utf-8", "replace")[:400]
    json.dump({"path": path, "fps": fps, "n": len(hashes), "h": hashes},
              open(out, "w"))
    print(f"{len(hashes)} проби ({len(hashes)/fps/60:.1f} мин)  {path.split('/')[-1]}"
          + (f"  ffmpeg: {err}" if err.strip() else ""))


if __name__ == "__main__":
    main()
