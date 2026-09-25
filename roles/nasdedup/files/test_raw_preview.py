"""raw_preview on three known pairs: CR2 = JPEG, PSD = CR2, CR2 ≠ CR2.

The containers are built byte for byte the way Canon and Photoshop lay them out; the
"JPEG" inside carries its 9x8 greyscale picture right after SOI and an APP0 marker, and
a stub decoder reads it back, so the test needs no ffmpeg and checks extraction and the
verdict, not ffmpeg.
"""

import json
import os
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import raw_preview  # noqa: E402

GRADIENT = bytes((c * 28 + r * 3) % 256 for r in range(8) for c in range(9))
REVERSED = bytes(255 - x for x in GRADIENT)


def fake_jpeg(gray, pad=0):
    return b"\xff\xd8\xff\xe0" + gray + b"\x00" * pad + b"\xff\xd9"


def stub_decode(jpeg):
    return jpeg[4:76]


def cr2(big, small):
    """Little-endian TIFF: IFD0 strip = full-size JPEG, IFD1 JPEGInterchangeFormat = thumb."""
    head = 16
    ifd0 = head
    ifd1 = ifd0 + 2 + 2 * 12 + 4
    big_off = ifd1 + 2 + 2 * 12 + 4
    small_off = big_off + len(big)
    b = bytearray(b"II*\x00" + struct.pack("<I", ifd0) + b"CR\x02\x00\x00\x00\x00\x00")
    b += struct.pack("<H", 2)
    b += struct.pack("<HHII", 0x0111, 4, 1, big_off)
    b += struct.pack("<HHII", 0x0117, 4, 1, len(big))
    b += struct.pack("<I", ifd1)
    b += struct.pack("<H", 2)
    b += struct.pack("<HHII", 0x0201, 4, 1, small_off)
    b += struct.pack("<HHII", 0x0202, 4, 1, len(small))
    b += struct.pack("<I", 0)
    return bytes(b) + big + small


def psd(jpeg):
    res = b"8BIM" + struct.pack(">H", 1036) + b"\x00\x00"       # empty Pascal name
    body = b"\x00" * 28 + jpeg
    res += struct.pack(">I", len(body)) + body + (b"\x00" if len(body) % 2 else b"")
    b = b"8BPS" + struct.pack(">H", 1) + b"\x00" * 6 + struct.pack(">HIIHH", 3, 8, 9, 8, 3)
    b += struct.pack(">I", 0)                                    # colour mode data
    b += struct.pack(">I", len(res)) + res
    return b + struct.pack(">I", 0) + b"\x00" * 16


class RawPreviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def put(self, name, data):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    def test_three_known_pairs(self):
        thumb = fake_jpeg(REVERSED)                # the small thumb must lose to the big one
        a_cr2 = self.put("IMG_0001.CR2", cr2(fake_jpeg(GRADIENT, pad=500), thumb))
        a_jpg = self.put("IMG_0001.JPG", fake_jpeg(GRADIENT))
        a_psd = self.put("IMG_0001.psd", psd(fake_jpeg(GRADIENT)))
        other = self.put("IMG_0002.CR2", cr2(fake_jpeg(REVERSED, pad=500), thumb))

        self.assertEqual(raw_preview.compare(a_cr2, a_jpg, stub_decode)[0], "confirmed")
        self.assertEqual(raw_preview.compare(a_psd, a_cr2, stub_decode)[0], "confirmed")
        v, detail, dist = raw_preview.compare(a_cr2, other, stub_decode)
        self.assertEqual((v, dist), ("rejected", 64), detail)

    def test_no_preview_stays_undecided(self):
        a = self.put("x.CR2", b"II*\x00" + b"\x00" * 64)
        b = self.put("y.jpg", fake_jpeg(GRADIENT))
        self.assertEqual(raw_preview.compare(a, b, stub_decode)[0], "undecided")

    def test_report_counts_and_moves_nothing(self):
        a = self.put("A.CR2", cr2(fake_jpeg(GRADIENT, pad=10), fake_jpeg(REVERSED)))
        b = self.put("A.JPG", fake_jpeg(GRADIENT))
        c = self.put("C.PSD", psd(fake_jpeg(REVERSED)))
        rows = [
            {"gp": a, "orig": b, "gp_size": 1, "orig_size": 1, "verdict": "undecided"},
            {"gp": c, "orig": b, "gp_size": 1, "orig_size": 1, "verdict": "undecided"},
            {"gp": b, "orig": b, "gp_size": 1, "orig_size": 1, "verdict": "undecided"},
            {"gp": a, "orig": b, "gp_size": 1, "orig_size": 1, "verdict": "confirmed"},
        ]
        before = sorted(os.listdir(self.tmp.name))
        src = os.path.join(self.tmp.name, "in.json")
        out = os.path.join(self.tmp.name, "out.json")
        json.dump(rows, open(src, "w"))
        orig = raw_preview.compare
        with mock.patch.object(sys, "argv", ["raw_preview.py", src, out]), \
                mock.patch.object(raw_preview, "compare",
                                  lambda x, y: orig(x, y, stub_decode)):
            raw_preview.main()
        verdicts = [r["verdict"] for r in json.load(open(out))]
        self.assertEqual(verdicts, ["confirmed", "rejected"])  # non-RAW and decided rows skipped
        after = set(os.listdir(self.tmp.name)) - {"in.json", "out.json", "out.json.part"}
        self.assertEqual(sorted(after), [x for x in before if x != "in.json"])


if __name__ == "__main__":
    unittest.main()
