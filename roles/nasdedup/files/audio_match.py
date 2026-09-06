#!/usr/bin/env python3
"""Check whether two videos carry the same soundtrack at a given offset.

Picture containment is not enough to call a source clip redundant. A phone montage app
routinely drops the original sound and lays music over the whole edit, and here the
original sound is a grandmother speaking — the part of the recording that cannot be
replaced. So the audio is compared separately, and a clip is only redundant if both the
picture and the sound survive in the longer file.

Comparison is on the loudness envelope, not the waveform: 20 ms frames of mean absolute
amplitude, correlated over a small range of lags. Re-encoding changes every sample but
leaves the envelope intact, whereas a different soundtrack — music, or silence — has no
correlation with speech at any lag.
"""

import math
import subprocess
import sys

RATE = 8000
FRAME = 160            # 20 ms
LAGS = 150             # +/- 3 s of slack around the offset the picture gave us


def envelope(path, start, dur):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-i", path, "-t", f"{dur:.2f}",
         "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
        capture_output=True).stdout
    env = []
    for k in range(len(raw) // (2 * FRAME)):
        acc = 0
        base = k * 2 * FRAME
        for s in range(FRAME):
            v = raw[base + 2 * s] | (raw[base + 2 * s + 1] << 8)
            if v >= 32768:
                v -= 65536
            acc += abs(v)
        env.append(math.log1p(acc / FRAME))
    return env


def corr(a, b):
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return 0.0
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / math.sqrt(va * vb)


def main():
    src, con, offset = sys.argv[1], sys.argv[2], float(sys.argv[3])
    start = float(sys.argv[4]) if len(sys.argv) > 4 else 30.0
    dur = float(sys.argv[5]) if len(sys.argv) > 5 else 60.0

    es = envelope(src, start, dur)
    ec = envelope(con, start + offset - LAGS * FRAME / RATE, dur + 2 * LAGS * FRAME / RATE)
    if not es or not ec:
        print("  няма звук за сравнение")
        return
    best, blag = -1.0, 0
    for lag in range(0, min(2 * LAGS, max(1, len(ec) - len(es)))):
        c = corr(es, ec[lag:lag + len(es)])
        if c > best:
            best, blag = c, lag
    shift = (blag - LAGS) * FRAME / RATE
    verdict = ("същият звук" if best >= 0.75 else
               "вероятно същият" if best >= 0.5 else "РАЗЛИЧЕН ЗВУК")
    print(f"  корелация {best:+.2f} при изместване {shift:+.2f}s  →  {verdict}")


if __name__ == "__main__":
    main()
