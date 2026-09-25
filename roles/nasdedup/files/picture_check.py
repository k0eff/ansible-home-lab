#!/usr/bin/env python3
"""Tell a re-encode from an edited copy of the same photograph (#1558).

The 9x8 difference hash in verify_gphotos.py answers "is this the same shot?", and
nothing finer. An edit keeps the composition, so the hash kept confirming pairs where
one side was a Photomatix render, a spot-removal retouch or the edited twin of a
.picasaoriginals file — 4,497 'confirmed' pairs, 14.64 GB, most of them not duplicates.

A re-encode changes almost nothing you can see: tone, contrast and every local detail
survive, only compression noise moves. An edit changes one of those on purpose. So
both sides are decoded to a 64x64 greyscale grid and compared three ways:

  tone     mean and spread of the whole picture. Auto-contrast, levels, curves and
           "I'm feeling lucky" move these; re-compression does not.
  blocks   the grid is split into 8x8 blocks and each is compared after the global
           tone is normalised away. A healed spot, a cloned-out object or a red-eye fix
           leaves one block far off while the rest match.
  detail   local contrast (each pixel against its 5x5 neighbourhood). Tone-mapping and
           HDR renders exaggerate it across the whole frame; re-encoding softens it a
           little at most.

Pure python on raw bytes, so it runs where ffmpeg runs and nothing needs installing.
"""

import subprocess

N = 64                      # decode grid, N x N greyscale
BLOCK = 8                   # block edge in grid pixels -> (N/BLOCK)^2 blocks
MEAN_MAX = 6.0              # grey levels of 255
SPREAD_TOL = 0.08           # std ratio must be within 1 +/- this
BLOCK_MAX = 0.35            # RMS difference of one block, in units of the global std
DETAIL_TOL = 0.20           # local-contrast ratio must be within 1 +/- this


def decode(path, seek=None):
    """N*N greyscale bytes of one frame, or None."""
    cmd = ["ffmpeg", "-v", "quiet"]
    if seek:
        cmd += ["-ss", f"{seek:.2f}"]
    cmd += ["-i", path, "-frames:v", "1",
            "-vf", f"scale={N}:{N}:flags=area,format=gray", "-f", "rawvideo", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=180).stdout
    except Exception:
        return None
    return raw[:N * N] if len(raw) >= N * N else None


def _stats(px):
    m = sum(px) / len(px)
    s = (sum((p - m) ** 2 for p in px) / len(px)) ** 0.5
    return m, s


def _detail(px):
    """Mean absolute difference of each pixel from its 5x5 box mean."""
    total, count = 0.0, 0
    for y in range(2, N - 2):
        for x in range(2, N - 2):
            box = 0
            for dy in range(-2, 3):
                row = (y + dy) * N
                box += sum(px[row + x - 2:row + x + 3])
            total += abs(px[y * N + x] - box / 25.0)
            count += 1
    return total / count


def judge(a, b):
    """(same, reason) for two N*N greyscale grids of the same shot."""
    a, b = list(a), list(b)
    ma, sa = _stats(a)
    mb, sb = _stats(b)
    if sa < 1 or sb < 1:
        return True, "flat frame, nothing to compare"
    if abs(ma - mb) > MEAN_MAX:
        return False, f"edited: brightness moved {mb - ma:+.1f}/255"
    if abs(sb / sa - 1) > SPREAD_TOL:
        return False, f"edited: contrast x{sb / sa:.2f}"

    na = [(p - ma) / sa for p in a]
    nb = [(p - mb) / sb for p in b]
    worst, where = 0.0, None
    for by in range(0, N, BLOCK):
        for bx in range(0, N, BLOCK):
            acc = 0.0
            for y in range(by, by + BLOCK):
                for x in range(bx, bx + BLOCK):
                    d = na[y * N + x] - nb[y * N + x]
                    acc += d * d
            rms = (acc / (BLOCK * BLOCK)) ** 0.5
            if rms > worst:
                worst, where = rms, (bx // BLOCK, by // BLOCK)
    if worst > BLOCK_MAX:
        return False, f"edited: block {where} differs {worst:.2f}"

    da, db = _detail(a), _detail(b)
    if da > 0.5 and abs(db / da - 1) > DETAIL_TOL:
        return False, f"edited: local contrast x{db / da:.2f}"
    return True, f"fine check: worst block {worst:.2f}"
