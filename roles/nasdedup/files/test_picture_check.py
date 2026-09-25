"""picture_check.judge on a labelled synthetic sample: 20 re-encodes, 20 edits (#1558).

The real labelled sample lives on the NAS; these stand in for its classes so the
thresholds cannot silently drift. Re-encodes: requantisation, noise, a slight blur, a
downscale round trip. Edits: spot removal, clone-out, tone curves, levels, HDR-style
local contrast.
"""

import math
import random
import unittest

import picture_check as pc

N = pc.N


def scene(seed):
    r = random.Random(seed)
    waves = [(r.uniform(0.05, 0.6), r.uniform(0.05, 0.6), r.uniform(0, 6), r.uniform(15, 45))
             for _ in range(6)]
    base = r.uniform(90, 160)
    px = []
    for y in range(N):
        for x in range(N):
            v = base + sum(a * math.sin(fx * x + fy * y + ph)
                           for fx, fy, ph, a in [(w[0], w[1], w[2], w[3]) for w in waves])
            px.append(v + r.uniform(-6, 6))
    return clamp(px)


def clamp(px):
    return [max(0, min(255, int(round(p)))) for p in px]


def box(px, k):
    out = []
    for y in range(N):
        for x in range(N):
            acc, n = 0, 0
            for dy in range(-k, k + 1):
                for dx in range(-k, k + 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < N and 0 <= xx < N:
                        acc += px[yy * N + xx]
                        n += 1
            out.append(acc / n)
    return out


# --- re-encodes ---------------------------------------------------------------------

def requant(px, seed):
    return clamp([(p // 3) * 3 + 1 for p in px])


def noise(px, seed):
    r = random.Random(seed)
    return clamp([p + r.gauss(0, 2.0) for p in px])


def soften(px, seed):
    b = box(px, 1)
    return clamp([0.85 * p + 0.15 * q for p, q in zip(px, b)])


def roundtrip(px, seed):
    # 2x2 downscale then nearest upscale, blended as a resampler would.
    out = list(px)
    for y in range(0, N, 2):
        for x in range(0, N, 2):
            m = (px[y * N + x] + px[y * N + x + 1] + px[(y + 1) * N + x] + px[(y + 1) * N + x + 1]) / 4
            for yy, xx in ((y, x), (y, x + 1), (y + 1, x), (y + 1, x + 1)):
                out[yy * N + xx] = 0.7 * px[yy * N + xx] + 0.3 * m
    return clamp(out)


def combo(px, seed):
    return noise(requant(px, seed), seed)


REENCODES = [requant, noise, soften, roundtrip, combo]


# --- edits --------------------------------------------------------------------------

def spot(px, seed):
    r = random.Random(seed)
    out = list(px)
    cx, cy = r.randrange(4, N - 12), r.randrange(4, N - 12)
    fill = sum(px) / len(px) + 40
    for y in range(cy, cy + 7):
        for x in range(cx, cx + 7):
            out[y * N + x] = fill
    return clamp(out)


def clone(px, seed):
    r = random.Random(seed)
    out = list(px)
    cx, cy = r.randrange(0, N - 10), r.randrange(0, N - 10)
    sx, sy = (cx + 20) % (N - 10), (cy + 27) % (N - 10)
    for y in range(10):
        for x in range(10):
            out[(cy + y) * N + cx + x] = px[(sy + y) * N + sx + x]
    return clamp(out)


def curve(px, seed):
    g = 0.7 if seed % 2 else 1.4
    return clamp([255 * (p / 255) ** g for p in px])


def levels(px, seed):
    return clamp([(p - 30) * 1.25 for p in px])


def tonemap(px, seed):
    b = box(px, 3)
    return clamp([q + 2.0 * (p - q) for p, q in zip(px, b)])


EDITS = [spot, clone, curve, levels, tonemap]


class JudgeSample(unittest.TestCase):
    def test_twenty_reencodes_accepted(self):
        for i in range(20):
            src = scene(i)
            f = REENCODES[i % len(REENCODES)]
            same, why = pc.judge(src, f(src, i))
            self.assertTrue(same, f"{f.__name__} seed {i}: {why}")

    def test_twenty_edits_rejected(self):
        for i in range(20):
            src = scene(100 + i)
            f = EDITS[i % len(EDITS)]
            same, why = pc.judge(src, f(src, i))
            self.assertFalse(same, f"{f.__name__} seed {i}: {why}")
            self.assertTrue(why.startswith("edited:"))

    def test_identical_and_flat(self):
        src = scene(7)
        self.assertTrue(pc.judge(src, src)[0])
        self.assertTrue(pc.judge([128] * (N * N), [128] * (N * N))[0])


if __name__ == "__main__":
    unittest.main()
