#!/usr/bin/env python3
"""Tiered duplicate finder for the QNAP share, built to stay out of the NAS's way.

Read-only: opens files for reading and never writes, moves or deletes anything on the
share. The only thing it produces is a SQLite database and a report.

Three properties matter more than speed here:

  Politeness   The NAS serves a household. Every batch of work is gated on NAS
               temperature, NAS load and a latency probe, and the process runs at the
               lowest CPU and I/O priority the OS offers. It yields rather than competes.

  Resumability Every completed unit is committed immediately. Killing the container,
               restarting Docker, or rebooting the host loses at most one batch.

  Cheap first  Reading 4.77 TB to hash it is 8 hours of disk time. Four tiers narrow the
               candidate set with almost no I/O before any full read happens: identical
               size, then a 4 KB sample, then a three-point sketch, then the full hash.
               Only files that survive each tier pay for the next.
"""

import argparse
import errno
import hashlib
import os
import queue
import signal
import sqlite3
import stat as stat_module
import sys
import threading
import time
import zlib
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Configuration. Everything is overridable by environment variable so the
# container needs no rebuild to be retuned.
# ---------------------------------------------------------------------------


def env(name, default, cast=str):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    if cast is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return cast(raw)


ROOT = env("NASDEDUP_ROOT", "/mnt/krasi")
STATE_DIR = env("NASDEDUP_STATE", "/state")
WORKERS = env("NASDEDUP_WORKERS", 8, int)
MIN_SIZE = env("NASDEDUP_MIN_SIZE", 1024 * 1024, int)      # ignore files under 1 MB

# Seek economics on spinning disks. A seek costs ~10 ms; reading 1 MB sequentially
# costs ~7 ms. Walking a file through all three tiers costs four extra seeks — about
# 40 ms, the price of reading roughly 6 MB. So for a small file that turns out to be a
# duplicate, skipping the tiers is cheaper.
#
# It is only cheaper when the file *is* a duplicate, though, and sharing a size with
# another file is weak evidence of that: most size collisions are coincidence, and
# losing the bet means reading the whole file where 4 KB would have settled it. Set to
# 0 (off) by default, and left available for a run over a directory already known to be
# duplicate-dense. Files inside a matched directory tree take this path regardless —
# there the evidence is the signature, not the size.
SEEK_SHORTCUT = env("NASDEDUP_SEEK_SHORTCUT", 0, int)
# CIFS with serverino synthesises inode numbers that do not fit SQLite's signed 64-bit
# INTEGER, and the insert fails with OverflowError mid-inventory. The value is only used
# to order reads, never as an identity, so folding it into the signed range costs
# nothing — two files colliding after the mask would merely be read next to each other.
INO_MASK = (1 << 63) - 1
# Concurrency for the full read. Textbook reasoning says parallel streams on one
# spindle turn sequential reads into seek storms, so this started at 2. Measured
# against this NAS on guaranteed-cold data — a 200 MB offset no earlier test had
# touched — the medians over four randomised trials each are:
#
#   1 thread 39 MB/s · 2 threads 42 · 4 threads 47 · 8 threads 52
#
# So depth helps, by about a third, and stops helping past 8. The single-spindle model
# does not describe what is on the other side: two drives, NCQ reordering a deeper
# queue into a shorter seek path, and an NFS mount with nconnect=4 that wants several
# requests in flight.
#
# An earlier run of this same benchmark reported 78/77/101/124 MB/s and a 60% gain.
# Those files had been read by previous experiments and were being served from the
# NAS's cache — re-reads run at ~270 MB/s here, which swamps the effect being measured.
# The cold numbers are the ones to trust: sequential throughput in this access pattern
# is 40-55 MB/s, not the 171 MB/s a single large dd suggests.
TIER3_WORKERS = env("NASDEDUP_TIER3_WORKERS", 8, int)

# Temperature gates. The only temperature warning this NAS has ever sent was on
# 2026-06-01: "M.2 PCIe SSD 2 temperature is over 67 ºC" — the reading itself
# (32341 ºC) was a sensor fault, but the 67 ºC threshold is real and is QNAP's own
# configured warning level. These targets sit far below it, with hysteresis so the
# job does not flap on and off around a single degree.
NVME_PAUSE = env("NASDEDUP_NVME_PAUSE", 55.0, float)
NVME_RESUME = env("NASDEDUP_NVME_RESUME", 48.0, float)
HDD_PAUSE = env("NASDEDUP_HDD_PAUSE", 45.0, float)
HDD_RESUME = env("NASDEDUP_HDD_RESUME", 40.0, float)
CPU_PAUSE = env("NASDEDUP_CPU_PAUSE", 70.0, float)
CPU_RESUME = env("NASDEDUP_CPU_RESUME", 45.0, float)

# Latency probe. Works with nothing but the read-only mount, so it is the floor of
# protection when SNMP is unavailable: if the NAS slows down for any reason, the
# stat() round trip lengthens and the job backs off.
LAT_SAMPLE = env("NASDEDUP_LAT_SAMPLE", 30, int)
LAT_PAUSE_FACTOR = env("NASDEDUP_LAT_PAUSE_FACTOR", 4.0, float)
LAT_RESUME_FACTOR = env("NASDEDUP_LAT_RESUME_FACTOR", 2.0, float)
# A ratio alone is not evidence. The first probe against a warm mount came back under a
# millisecond, became the baseline, and then an ordinary 40 ms CIFS stat read as 175x
# worse and paused the job before it had walked a single directory. Two guards: the
# baseline needs several samples before the gate arms, and it never goes below a floor,
# so a fast first probe cannot poison it. And the absolute latency has to be genuinely
# bad as well — 40 ms is a normal round trip no matter what the baseline says.
LAT_MIN_BASELINE = env("NASDEDUP_LAT_MIN_BASELINE", 25.0, float)
LAT_PAUSE_ABS = env("NASDEDUP_LAT_PAUSE_ABS", 400.0, float)
LAT_WARMUP = env("NASDEDUP_LAT_WARMUP", 5, int)

# Blackout windows, local time: (weekday 0=Mon, start_hour, start_min, end_hour, end_min).
# Every one of the 13 "Low drive speed" / "Skipped read performance test — the disk is
# busy" warnings in the mailbox landed on a Monday between 03:53 and 04:53. That is the
# NAS running its own weekly read-performance test, and a scan running through it is
# exactly what makes the test fail.
BLACKOUTS = [(0, 3, 30, 5, 30)]

SNMP_HOST = env("NASDEDUP_SNMP_HOST", "")
SNMP_USER = env("NASDEDUP_SNMP_USER", "")
SNMP_AUTH = env("NASDEDUP_SNMP_AUTH", "")
SNMP_PRIV = env("NASDEDUP_SNMP_PRIV", "")
SNMP_COMMUNITY = env("NASDEDUP_SNMP_COMMUNITY", "")
SNMP_INTERVAL = env("NASDEDUP_SNMP_INTERVAL", 60, int)

# QNAP NAS-MIB. CPU usage and the per-slot temperature tables.
OID_CPU = "1.3.6.1.4.1.24681.1.4.1.1.1.1.0"
OID_SYSTEMP = "1.3.6.1.4.1.24681.1.4.1.1.1.3.0"
OID_HD_TABLE = "1.3.6.1.4.1.24681.1.4.1.1.1.10.1.1.3"   # per-drive temperature column

SKIP_DIR_NAMES = {"@recycle", "@recently-snapshot", "bands", "mapped",
                  ".tmp.drivedownload", "@eadir", "#recycle"}
SKIP_DIR_SUFFIXES = (".sparsebundle", ".sparseimage", ".photoslibrary")

MOUNT_LOST = {errno.ENOTCONN, errno.ETIMEDOUT, errno.EHOSTDOWN, errno.EHOSTUNREACH,
              errno.ENETDOWN, errno.ENETUNREACH, errno.ENODEV, errno.ESTALE,
              errno.ECONNRESET, errno.ECONNABORTED, errno.EIO}

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS files (
    path   TEXT PRIMARY KEY,
    dirpath TEXT NOT NULL,                -- read order: group by directory, then inode
    ino    INTEGER,
    size   INTEGER NOT NULL,
    mtime  INTEGER,
    stage  INTEGER NOT NULL DEFAULT 0,   -- 0 inventoried, 1..3 tier done, 9 unique, -1 error
    tree   TEXT,                          -- signature of the duplicate tree it belongs to
    l1     TEXT,                          -- crc32 of the first 4 KB
    l2     TEXT,                          -- blake2b-128 of head+middle+tail
    l3     TEXT,                          -- sha256 of the whole file
    err    TEXT
);
CREATE INDEX IF NOT EXISTS ix_files_order ON files(dirpath, ino);
CREATE INDEX IF NOT EXISTS ix_files_tree  ON files(tree);
CREATE INDEX IF NOT EXISTS ix_files_stage ON files(stage);
CREATE INDEX IF NOT EXISTS ix_files_size  ON files(size, stage);
CREATE INDEX IF NOT EXISTS ix_files_l1    ON files(size, l1);
CREATE INDEX IF NOT EXISTS ix_files_l2    ON files(size, l1, l2);
CREATE INDEX IF NOT EXISTS ix_files_l3    ON files(l3);

CREATE TABLE IF NOT EXISTS dirs (
    path   TEXT PRIMARY KEY,
    parent TEXT,
    status TEXT NOT NULL,                 -- pending | done | skipped | error
    sig    TEXT,                          -- structural signature, computed with no I/O
    nfiles INTEGER DEFAULT 0,
    bytes  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_dirs_sig ON dirs(sig);
CREATE INDEX IF NOT EXISTS ix_dirs_status ON dirs(status);

CREATE TABLE IF NOT EXISTS meta   (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS health (
    ts INTEGER NOT NULL, metric TEXT NOT NULL, value REAL, note TEXT
);
CREATE INDEX IF NOT EXISTS ix_health_ts ON health(ts);
CREATE TABLE IF NOT EXISTS pauses (
    ts INTEGER NOT NULL, state TEXT NOT NULL, reason TEXT
);
"""


def log(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}", flush=True)


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0


# ---------------------------------------------------------------------------
# Process priority
# ---------------------------------------------------------------------------

def deprioritise():
    """Put this process below everything else on the box, CPU and disk alike."""
    notes = []
    try:
        os.nice(19)
        notes.append("nice=19")
    except OSError as exc:
        notes.append(f"nice failed: {exc}")

    # ionice idle class. There is no libc wrapper, so go through the syscall.
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        IOPRIO_WHO_PROCESS, IOPRIO_CLASS_IDLE = 1, 3
        value = IOPRIO_CLASS_IDLE << 13
        if libc.syscall(251, IOPRIO_WHO_PROCESS, 0, value) == 0:   # __NR_ioprio_set
            notes.append("ionice=idle")
        else:
            notes.append(f"ionice failed: errno {ctypes.get_errno()}")
    except Exception as exc:
        notes.append(f"ionice unavailable: {exc}")

    try:
        os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
        notes.append("sched=IDLE")
    except Exception:
        pass                                  # not fatal, nice already covers most of it
    log("priority: " + ", ".join(notes))


def assert_readonly(root, allow_rw=False):
    """Refuse to start unless the share is mounted read-only.

    This reads /proc/mounts rather than test-writing a file: proving the mount is
    read-only by trying to write to it would be the one thing this job must never do.
    A read-only SMB user, a `:ro` bind mount and this check are three independent
    layers, and the point of three layers is that no single mistake removes them all.
    """
    try:
        with open("/proc/mounts") as fh:
            mounts = [line.split() for line in fh]
    except OSError:
        log("read-only check: no /proc/mounts (not Linux) — skipping")
        return True

    target = os.path.realpath(root)
    best = None
    for parts in mounts:
        if len(parts) < 4:
            continue
        point = parts[1].replace("\\040", " ")
        if target == point or target.startswith(point.rstrip("/") + "/"):
            if best is None or len(point) > len(best[1]):
                best = (parts[0], point, parts[3])
    if best is None:
        log(f"read-only check: {root} is not a mount point of its own")
        return True

    device, point, opts = best
    flags = opts.split(",")
    if "ro" in flags:
        log(f"read-only check: {point} is mounted ro ({device})")
        return True

    msg = (f"REFUSING TO START: {point} is mounted read-write ({device}, {opts}). "
           f"Mount the share ro, or set NASDEDUP_ALLOW_RW=1 if you accept the risk.")
    if allow_rw:
        log("read-only check: " + msg.replace("REFUSING TO START", "WARNING") )
        return True
    log(msg)
    return False


# ---------------------------------------------------------------------------
# Health: what the NAS is doing right now
# ---------------------------------------------------------------------------

class Health:
    """Decides whether work may proceed, from three independent signals.

    SNMP is the best signal but needs the NAS configured for it. The latency probe
    needs nothing but the mount, so it is always on: if the NAS gets busy for any
    reason at all, round trips lengthen and this backs off. The blackout calendar
    covers the one load window that is known in advance.
    """

    def __init__(self, conn, db_lock):
        self.conn = conn
        self.db_lock = db_lock
        self.lock = threading.Lock()
        self.paused = False
        self.reason = ""
        self.baseline_latency = None
        self.latency = None
        self.lat_samples = 0
        self.temps = {}
        self.cpu = None
        self.snmp_ok = None
        self.usage_hook = None
        self.stop = threading.Event()

    # ---- persistence ----

    def record(self, metric, value, note=""):
        with self.db_lock:
            self.conn.execute("INSERT INTO health(ts,metric,value,note) VALUES (?,?,?,?)",
                              (int(time.time()), metric, value, note))

    def transition(self, paused, reason):
        with self.lock:
            if paused == self.paused:
                return
            self.paused, self.reason = paused, reason
        log(("PAUSE: " if paused else "RESUME: ") + reason)
        with self.db_lock:
            self.conn.execute("INSERT INTO pauses(ts,state,reason) VALUES (?,?,?)",
                              (int(time.time()), "pause" if paused else "resume", reason))
            self.conn.commit()

    # ---- signals ----

    @staticmethod
    def in_blackout(now=None):
        now = now or datetime.now()
        for wd, sh, sm, eh, em in BLACKOUTS:
            if now.weekday() != wd:
                continue
            start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
            end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
            if start <= now <= end:
                return f"blackout window {sh:02d}:{sm:02d}-{eh:02d}:{em:02d} " \
                       f"(NAS weekly drive test)"
        return None

    def probe_latency(self):
        """One stat() against the share, timed. Cheap, and needs no NAS privileges."""
        t = time.time()
        try:
            os.stat(ROOT)
        except OSError:
            return None
        dt = (time.time() - t) * 1000.0
        self.lat_samples += 1
        if self.baseline_latency is None:
            self.baseline_latency = max(dt, LAT_MIN_BASELINE)
        else:
            # Track the floor: the baseline should represent an idle NAS, so let it
            # fall quickly and rise only slowly.
            if dt < self.baseline_latency:
                self.baseline_latency = 0.7 * self.baseline_latency + 0.3 * dt
            else:
                self.baseline_latency = 0.995 * self.baseline_latency + 0.005 * dt
            self.baseline_latency = max(self.baseline_latency, LAT_MIN_BASELINE)
        self.latency = dt
        return dt

    def poll_snmp(self):
        if not SNMP_HOST:
            return
        try:
            from pysnmp.hlapi import (getCmd, nextCmd, SnmpEngine, CommunityData,
                                      UsmUserData, UdpTransportTarget, ContextData,
                                      ObjectType, ObjectIdentity, usmHMACSHAAuthProtocol,
                                      usmAesCfb128Protocol)
        except ImportError:
            if self.snmp_ok is None:
                log("SNMP: pysnmp not installed — falling back to the latency probe")
            self.snmp_ok = False
            return

        if SNMP_USER:
            auth = UsmUserData(SNMP_USER, SNMP_AUTH or None, SNMP_PRIV or None,
                               authProtocol=usmHMACSHAAuthProtocol,
                               privProtocol=usmAesCfb128Protocol)
        elif SNMP_COMMUNITY:
            auth = CommunityData(SNMP_COMMUNITY, mpModel=1)
        else:
            self.snmp_ok = False
            return

        target = UdpTransportTarget((SNMP_HOST, 161), timeout=4, retries=1)
        try:
            for oid, key in ((OID_CPU, "cpu"), (OID_SYSTEMP, "systemp")):
                err, status, _, binds = next(getCmd(
                    SnmpEngine(), auth, target, ContextData(),
                    ObjectType(ObjectIdentity(oid))))
                if err or status:
                    raise RuntimeError(str(err or status))
                raw = str(binds[0][1])
                val = float("".join(c for c in raw if c.isdigit() or c == ".") or 0)
                if key == "cpu":
                    self.cpu = val
                else:
                    self.temps["system"] = val
                self.record(key, val)

            temps = {}
            for (err, status, _, binds) in nextCmd(
                    SnmpEngine(), auth, target, ContextData(),
                    ObjectType(ObjectIdentity(OID_HD_TABLE)), lexicographicMode=False):
                if err or status:
                    break
                oid_str, raw = str(binds[0][0]), str(binds[0][1])
                slot = oid_str.rsplit(".", 1)[-1]
                digits = "".join(c for c in raw.split("C/")[0] if c.isdigit() or c == ".")
                if digits:
                    temps[f"drive{slot}"] = float(digits)
            for k, v in temps.items():
                self.temps[k] = v
                self.record(k, v)
            if self.snmp_ok is not True:
                log(f"SNMP: connected, {len(temps)} drive temperatures visible")
            self.snmp_ok = True
        except Exception as exc:
            if self.snmp_ok is not False:
                log(f"SNMP: unavailable ({exc}) — latency probe carries the throttling")
            self.snmp_ok = False

    # ---- decision ----

    def evaluate(self):
        blackout = self.in_blackout()
        if blackout:
            self.transition(True, blackout)
            return

        hot = []
        for name, temp in self.temps.items():
            if name.startswith("drive") or name == "nvme":
                limit = NVME_PAUSE if "nvme" in name else HDD_PAUSE
                back = NVME_RESUME if "nvme" in name else HDD_RESUME
                if temp >= limit:
                    hot.append(f"{name} {temp:.0f}°C ≥ {limit:.0f}°C")
                elif self.paused and temp > back:
                    hot.append(f"{name} {temp:.0f}°C still above resume {back:.0f}°C")
        if hot:
            self.transition(True, "temperature: " + ", ".join(hot))
            return

        if self.cpu is not None:
            if self.cpu >= CPU_PAUSE:
                self.transition(True, f"NAS CPU {self.cpu:.0f}% ≥ {CPU_PAUSE:.0f}%")
                return
            if self.paused and self.cpu > CPU_RESUME and "CPU" in self.reason:
                return

        if self.latency and self.baseline_latency and self.lat_samples >= LAT_WARMUP:
            ratio = self.latency / max(self.baseline_latency, LAT_MIN_BASELINE)
            if ratio >= LAT_PAUSE_FACTOR and self.latency >= LAT_PAUSE_ABS:
                self.transition(True, f"NAS latency {self.latency:.0f} ms is "
                                      f"{ratio:.1f}× the idle baseline")
                return
            if self.paused and ratio > LAT_RESUME_FACTOR and "latency" in self.reason:
                return

        self.transition(False, "all signals within limits")

    def monitor(self):
        last_snmp = 0.0
        last_usage = 0.0
        while not self.stop.is_set():
            try:
                self.probe_latency()
                if time.time() - last_usage >= 300 and self.usage_hook:
                    self.usage_hook()
                    last_usage = time.time()
                if time.time() - last_snmp >= SNMP_INTERVAL:
                    self.poll_snmp()
                    last_snmp = time.time()
                self.evaluate()
                with self.db_lock:
                    self.conn.commit()
            except Exception as exc:
                log(f"health monitor error (continuing): {exc}")
            self.stop.wait(LAT_SAMPLE)

    def wait_until_clear(self):
        """Block while paused. Returns False if the process is shutting down."""
        while not self.stop.is_set():
            with self.lock:
                if not self.paused:
                    return True
            self.stop.wait(15)
        return False

    def summary(self):
        bits = []
        if self.cpu is not None:
            bits.append(f"cpu {self.cpu:.0f}%")
        for k in sorted(self.temps):
            bits.append(f"{k} {self.temps[k]:.0f}°C")
        if self.latency:
            bits.append(f"lat {self.latency:.0f}ms")
            if self.baseline_latency:
                bits.append(f"base {self.baseline_latency:.0f}ms")
        return ", ".join(bits) or "no telemetry yet"


# ---------------------------------------------------------------------------
# The four tiers
# ---------------------------------------------------------------------------

SAMPLE_HEAD = 4096          # tier 1
CHUNK = 64 * 1024           # tier 2 reads three of these
READ_BLOCK = 1024 * 1024    # measured: 256 KB..16 MB all within noise


def tier1(path, size):
    """CRC32 of the first 4 KB. One read, no seek. Splits most groups outright.

    CRC32 is far too weak to prove two files identical — at 32 bits a collision is
    likely long before a million files — but that is not what it is asked to do. It
    only has to prove files *different*, and for that a cheap checksum is enough.
    """
    with open(path, "rb") as fh:
        return f"{zlib.crc32(fh.read(SAMPLE_HEAD)) & 0xFFFFFFFF:08x}"


def sample_points(size):
    """How many 64 KB windows to sample, scaled to the file's size.

    Three points settle a 5 MB photo. On a 4 GB video they cover 0.005% of it, and two
    recordings from the same camera can match at header, midpoint and tail while
    differing everywhere between. The cost of a point is one seek and 64 KB, so paying
    a few more on a large file is the difference between reading 1 MB to rule it out
    and reading 4 GB to find out.
    """
    if size <= 2 * CHUNK:
        return 1
    if size < 64 * 1024 * 1024:
        return 3
    if size < 1024 * 1024 * 1024:
        return 8
    return 16


def tier2(path, size):
    """BLAKE2b-128 over sample windows spread through the file.

    Catches container formats that share a header — MP4, JPEG, ZIP — but differ in the
    payload, which three fixed points at head, middle and tail can miss on large media.
    """
    n = sample_points(size)
    h = hashlib.blake2b(digest_size=16)
    h.update(size.to_bytes(8, "little"))
    h.update(str(n).encode())          # a file's sketch is only comparable at equal n
    with open(path, "rb") as fh:
        if n == 1:
            h.update(fh.read(CHUNK))
        else:
            step = max(1, (size - CHUNK) // (n - 1))
            for i in range(n):
                fh.seek(min(i * step, max(0, size - CHUNK)))
                h.update(fh.read(CHUNK))
    return h.hexdigest()


def tier3(path, size, health=None):
    """SHA-256 of the whole file, plus the tier 1 and 2 signatures from the same stream.

    Deriving l1 and l2 here costs nothing — the bytes are already passing through — and
    it is what keeps a file hashed by the tree pass comparable with files that come
    through the tiers. Without it a tree member has no l1, so nothing outside the tree
    can ever be matched against it.

    SHA-256 measured 1620 MB/s on Apple Silicon and 343 MB/s on the vm700 Xeon — both
    well above the 171 MB/s the NAS delivers over LAN, so the algorithm is never the
    bottleneck and there is no reason to trade collision resistance for speed.
    """
    full = hashlib.sha256()
    sketch = hashlib.blake2b(digest_size=16)
    sketch.update(size.to_bytes(8, "little"))
    n_points = sample_points(size)
    sketch.update(str(n_points).encode())
    if n_points == 1:
        windows = [0]
    else:
        step = max(1, (size - CHUNK) // (n_points - 1))
        windows = [min(i * step, max(0, size - CHUNK)) for i in range(n_points)]
    l1 = None
    pos = 0

    with open(path, "rb") as fh:
        while True:
            block = fh.read(READ_BLOCK)
            if not block:
                break
            full.update(block)
            end = pos + len(block)
            if l1 is None and pos == 0:
                l1 = f"{zlib.crc32(block[:SAMPLE_HEAD]) & 0xFFFFFFFF:08x}"
            # Feed the sketch exactly the byte ranges tier2 would have read.
            for start in windows:
                lo, hi = max(start, pos), min(start + CHUNK, end)
                if lo < hi:
                    sketch.update(block[lo - pos:hi - pos])
            pos = end
            if health and health.paused and not health.wait_until_clear():
                raise InterruptedError("shutting down mid-file")
    return l1 or "", sketch.hexdigest(), full.hexdigest()


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class Engine:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.db_lock = threading.Lock()
        self.health = Health(self.conn, self.db_lock)
        self.health.usage_hook = self.sample_usage
        self.stop = threading.Event()
        self.done = 0
        self.bytes_read = 0
        self.errors = 0

    # ---- inventory ----

    def inventory(self):
        """Walk the tree recording path, size and mtime. No file content is read."""
        with self.db_lock:
            seeded = self.conn.execute("SELECT COUNT(*) FROM dirs").fetchone()[0]
            if not seeded:
                self.conn.execute(
                    "INSERT INTO dirs(path,parent,status) VALUES (?,NULL,'pending')",
                    (ROOT,))
                self.conn.commit()
            pending = [r[0] for r in self.conn.execute(
                "SELECT path FROM dirs WHERE status IN ('pending','error')")]
        if not pending:
            log("inventory: already complete")
            return

        log(f"inventory: {len(pending)} directories to walk")
        q = queue.Queue()
        for p in pending:
            q.put(p)
        seen = set(pending)
        seen_lock = threading.Lock()
        inflight = [len(pending)]
        inflight_lock = threading.Lock()
        pending_rows = []
        counted = [0, 0]

        def flush(force=False):
            with self.db_lock:
                if not pending_rows and not force:
                    return
                rows, pending_rows[:] = list(pending_rows), []
                for sql, args in rows:
                    self.conn.execute(sql, args)
                self.conn.commit()

        def worker():
            while not self.stop.is_set():
                try:
                    path = q.get(timeout=2)
                except queue.Empty:
                    with inflight_lock:
                        if inflight[0] == 0:
                            return
                    continue
                try:
                    if not self.health.wait_until_clear():
                        return
                    try:
                        entries = list(os.scandir(path))
                    except OSError as exc:
                        pending_rows.append(
                            ("UPDATE dirs SET status='error' WHERE path=?", (path,)))
                        self.errors += 1
                        continue
                    for e in entries:
                        name_l = e.name.lower()
                        try:
                            st = e.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if stat_module.S_ISDIR(st.st_mode):
                            if name_l in SKIP_DIR_NAMES or \
                               name_l.endswith(SKIP_DIR_SUFFIXES):
                                pending_rows.append(
                                    ("INSERT OR REPLACE INTO dirs(path,parent,status) "
                                     "VALUES (?,?,'skipped')", (e.path, path)))
                                continue
                            with seen_lock:
                                if e.path in seen:
                                    continue
                                seen.add(e.path)
                            pending_rows.append(
                                ("INSERT OR IGNORE INTO dirs(path,parent,status) "
                                 "VALUES (?,?,'pending')", (e.path, path)))
                            with inflight_lock:
                                inflight[0] += 1
                            q.put(e.path)
                        elif stat_module.S_ISREG(st.st_mode) and st.st_size >= MIN_SIZE:
                            pending_rows.append(
                                ("INSERT OR REPLACE INTO files"
                                 "(path,dirpath,ino,size,mtime,stage) "
                                 "VALUES (?,?,?,?,?,0)",
                                 (e.path, path, st.st_ino & INO_MASK, st.st_size,
                                  int(st.st_mtime))))
                            counted[0] += 1
                            counted[1] += st.st_size
                    pending_rows.append(
                        ("UPDATE dirs SET status='done' WHERE path=?", (path,)))
                    self.done += 1
                    if self.done % 200 == 0:
                        flush()
                        log(f"inventory: {self.done} dirs, {counted[0]:,} files, "
                            f"{human(counted[1])} · {self.health.summary()}")
                finally:
                    with inflight_lock:
                        inflight[0] -= 1
                    q.task_done()

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(WORKERS)]
        for t in threads:
            t.start()
        for t in threads:
            while t.is_alive():
                t.join(timeout=1)
        flush(force=True)
        log(f"inventory complete: {counted[0]:,} files, {human(counted[1])}")

    # ---- narrowing ----

    def compute_dirsigs(self):
        """Give every directory a signature derived from its shape, reading nothing.

        The signature covers each child's name and size, and each subdirectory's own
        signature, so it describes the whole subtree. Two directories with the same
        signature hold the same file names at the same sizes, arranged the same way —
        which is what a copied folder looks like.

        This is the answer to walking the tiers file by file across a whole tree. A
        copied folder of 5,000 photos does not need 5,000 independent tier decisions
        scattered over the disk; it needs one decision and then one ordered pass.
        """
        with self.db_lock:
            dirs = self.conn.execute(
                "SELECT path, parent FROM dirs WHERE status='done'").fetchall()
            files = self.conn.execute(
                "SELECT dirpath, path, size FROM files").fetchall()
        children = {}
        for path, parent in dirs:
            children.setdefault(parent, []).append(path)
        own_files = {}
        for dirpath, path, size in files:
            own_files.setdefault(dirpath, []).append((os.path.basename(path), size))

        depth = {d: d.count("/") for d, _ in dirs}
        sig, nfiles, nbytes = {}, {}, {}
        for path in sorted(depth, key=lambda d: depth[d], reverse=True):
            h = hashlib.blake2b(digest_size=16)
            n, b = 0, 0
            for name, size in sorted(own_files.get(path, [])):
                h.update(f"f:{name}:{size}\n".encode("utf-8", "replace"))
                n += 1
                b += size
            for child in sorted(children.get(path, [])):
                h.update(f"d:{os.path.basename(child)}:{sig.get(child, '-')}\n"
                         .encode("utf-8", "replace"))
                n += nfiles.get(child, 0)
                b += nbytes.get(child, 0)
            sig[path], nfiles[path], nbytes[path] = h.hexdigest(), n, b

        with self.db_lock:
            self.conn.executemany(
                "UPDATE dirs SET sig=?, nfiles=?, bytes=? WHERE path=?",
                [(sig[d], nfiles[d], nbytes[d], d) for d in sig])
            self.conn.commit()
        log(f"signatures: {len(sig):,} directories fingerprinted, no bytes read")

    def mark_tree_candidates(self, min_files=2, min_bytes=16 * 1024 * 1024):
        """Find whole directories that look like copies of each other.

        Only the outermost match in each set is taken: if a tree is duplicated, its
        subdirectories are duplicated too, and marking those as well would verify the
        same bytes several times over.
        """
        with self.db_lock:
            rows = self.conn.execute("""
                SELECT sig, path, nfiles, bytes FROM dirs
                 WHERE sig IS NOT NULL AND nfiles >= ? AND bytes >= ?
                   AND sig IN (SELECT sig FROM dirs
                                WHERE sig IS NOT NULL AND nfiles >= ? AND bytes >= ?
                                GROUP BY sig HAVING COUNT(*) > 1)
                 ORDER BY sig, path""",
                (min_files, min_bytes, min_files, min_bytes)).fetchall()
        if not rows:
            log("trees: no directories share a structural signature")
            return 0

        by_sig = {}
        for sig_v, path, n, b in rows:
            by_sig.setdefault(sig_v, []).append((path, n, b))

        marked, groups, total = [], 0, 0
        for sig_v, members in by_sig.items():
            paths = sorted(m[0] for m in members)
            outer = [p for p in paths
                     if not any(p.startswith(q.rstrip("/") + "/") for q in paths)]
            if len(outer) < 2:
                continue
            groups += 1
            total += members[0][2] * (len(outer) - 1)
            for path in outer:
                marked.append((sig_v, path, path.rstrip("/") + "/"))

        if not marked:
            log("trees: every match was nested inside a larger one")
            return 0
        with self.db_lock:
            for sig_v, path, prefix in marked:
                self.conn.execute(
                    "UPDATE files SET tree=? WHERE dirpath=? OR dirpath LIKE ? || '%'",
                    (sig_v, path, prefix))
            self.conn.commit()
            in_trees = self.conn.execute(
                "SELECT COUNT(*), SUM(size) FROM files WHERE tree IS NOT NULL").fetchone()
        log(f"trees: {groups} duplicate directory sets, {len(marked)} directories, "
            f"{in_trees[0]:,} files ({human(in_trees[1] or 0)}) — these skip tiers 1-2 "
            f"and go straight to a single ordered SHA-256 pass")
        log(f"trees: ~{human(total)} reclaimable if each set keeps one copy "
            f"(pending full verification)")
        return groups

    def mark_unique_sizes(self):
        """Anything whose size is unique cannot have a duplicate. Costs no I/O."""
        with self.db_lock:
            cur = self.conn.execute("""
                UPDATE files SET stage=9
                 WHERE stage=0
                   AND size IN (SELECT size FROM files WHERE stage IN (0,9)
                                 GROUP BY size HAVING COUNT(*)=1)""")
            self.conn.commit()
        log(f"tier 0: {cur.rowcount:,} files excluded on size alone, no bytes read")

    def shortcut_to_full(self):
        """Send two classes of file straight to the full hash, skipping the tiers.

        Members of a duplicate tree, because the directory signature already did the
        narrowing that tiers 1 and 2 exist to do — and doing it again would scatter
        reads across a tree that can instead be read in one ordered sweep.

        Small files, because for them the tiers cost more head movement than they save.
        """
        with self.db_lock:
            tree = self.conn.execute(
                "UPDATE files SET stage=5 WHERE stage=0 AND tree IS NOT NULL").rowcount
            small = self.conn.execute("""
                UPDATE files SET stage=5
                 WHERE stage=0 AND size < ?
                   AND size IN (SELECT size FROM files WHERE stage IN (0,5)
                                 GROUP BY size HAVING COUNT(*) > 1)""",
                (SEEK_SHORTCUT,)).rowcount
            self.conn.commit()
        if tree or small:
            log(f"shortcut: {tree:,} files in duplicate trees and {small:,} files under "
                f"{human(SEEK_SHORTCUT)} go straight to the full hash")

    def candidates(self, tier):
        """Files that survived the previous tier and still share a signature.

        Always ordered by directory then inode. Files created together sit together on
        disk, so reading them in that order turns a random-access workload into a
        largely sequential one — the single biggest thing that can be done for a
        spinning disk from this side of the network.
        """
        if tier == 1:
            sql = """SELECT path, size FROM files
                      WHERE stage=0 AND size IN (
                            SELECT size FROM files WHERE stage IN (0,1,2,3)
                             GROUP BY size HAVING COUNT(*) > 1)
                      ORDER BY dirpath, ino"""
        elif tier == 2:
            sql = """SELECT f.path, f.size FROM files f
                      WHERE f.stage=1 AND EXISTS (
                            SELECT 1 FROM files g WHERE g.size=f.size AND g.l1=f.l1
                             AND g.path<>f.path AND g.stage>=1)
                      ORDER BY f.dirpath, f.ino"""
        else:
            sql = """SELECT f.path, f.size FROM files f
                      WHERE f.stage=5
                         OR (f.stage=2 AND EXISTS (
                                SELECT 1 FROM files g WHERE g.size=f.size AND g.l1=f.l1
                                 AND g.l2=f.l2 AND g.path<>f.path AND g.stage>=2))
                      ORDER BY f.dirpath, f.ino"""
        with self.db_lock:
            return self.conn.execute(sql).fetchall()

    def eliminate(self, tier):
        """Retire files that lost their last partner at this tier.

        Without this a file that tier 1 separated stays at stage 1 forever, the
        completion check reads it as unfinished, and the container restarts a finished
        job for as long as Docker is willing to keep restarting it.
        """
        sql = {
            1: """UPDATE files SET stage=9 WHERE stage=1 AND NOT EXISTS (
                      SELECT 1 FROM files g WHERE g.size=files.size AND g.l1=files.l1
                       AND g.path<>files.path AND g.stage>=1)""",
            2: """UPDATE files SET stage=9 WHERE stage=2 AND NOT EXISTS (
                      SELECT 1 FROM files g WHERE g.size=files.size AND g.l1=files.l1
                       AND g.l2=files.l2 AND g.path<>files.path AND g.stage>=2)""",
            3: """UPDATE files SET stage=9 WHERE stage IN (3,5) AND NOT EXISTS (
                      SELECT 1 FROM files g WHERE g.l3=files.l3
                       AND g.path<>files.path AND g.stage>=3)""",
        }[tier]
        with self.db_lock:
            cur = self.conn.execute(sql)
            self.conn.commit()
        if cur.rowcount:
            log(f"tier {tier}: {cur.rowcount:,} files ruled out, no further reads")

    def sample_usage(self):
        """Disk usage of the share, recorded alongside the health signals."""
        try:
            st = os.statvfs(ROOT)
        except OSError:
            return None
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        pct = used / total * 100 if total else 0
        self.health.record("disk_used_bytes", used)
        self.health.record("disk_free_bytes", free)
        self.health.record("disk_used_pct", pct)
        return used, free, pct

    def run_tier(self, tier):
        rows = self.candidates(tier)
        if not rows:
            log(f"tier {tier}: nothing to do")
            return
        total_bytes = sum(r[1] for r in rows)
        cost = {1: len(rows) * SAMPLE_HEAD,
                2: sum(sample_points(r[1]) * CHUNK for r in rows),
                3: total_bytes}[tier]
        log(f"tier {tier}: {len(rows):,} candidates, ~{human(cost)} to read, "
            f"{TIER3_WORKERS if tier == 3 else WORKERS} threads, ordered by disk layout")

        fn = {1: tier1, 2: tier2, 3: tier3}[tier]
        col = {1: "l1", 2: "l2", 3: "l3"}[tier]
        q = queue.Queue()
        for r in rows:
            q.put(r)
        rows_out = []
        counter = [0, 0]
        lock = threading.Lock()

        def worker():
            while not self.stop.is_set():
                try:
                    path, size = q.get_nowait()
                except queue.Empty:
                    return
                try:
                    if not self.health.wait_until_clear():
                        return
                    try:
                        if tier == 3:
                            d1, d2, d3 = fn(path, size, self.health)
                            row = ("UPDATE files SET l1=COALESCE(l1,?), "
                                   "l2=COALESCE(l2,?), l3=?, stage=3 WHERE path=?",
                                   (d1, d2, d3, path))
                        else:
                            digest = fn(path, size)
                            row = (f"UPDATE files SET {col}=?, stage=? WHERE path=?",
                                   (digest, tier, path))
                        with lock:
                            rows_out.append(row)
                            counter[0] += 1
                            counter[1] += size if tier == 3 else 0
                    except InterruptedError:
                        return
                    except OSError as exc:
                        with lock:
                            rows_out.append((
                                "UPDATE files SET stage=-1, err=? WHERE path=?",
                                (f"{exc.errno}: {exc}", path)))
                        self.errors += 1
                    if counter[0] % 200 == 0:
                        self.flush_rows(rows_out, lock)
                        log(f"tier {tier}: {counter[0]:,}/{len(rows):,} "
                            f"({human(counter[1])} read) · {self.health.summary()}")
                finally:
                    q.task_done()

        n_workers = TIER3_WORKERS if tier == 3 else WORKERS
        threads = [threading.Thread(target=worker, daemon=True)
                   for _ in range(n_workers)]
        for t in threads:
            t.start()
        for t in threads:
            while t.is_alive():
                t.join(timeout=1)
        self.flush_rows(rows_out, lock, force=True)
        log(f"tier {tier} complete: {counter[0]:,} hashed")
        self.eliminate(tier)

    def flush_rows(self, rows_out, lock, force=False):
        with lock:
            if not rows_out and not force:
                return
            batch, rows_out[:] = list(rows_out), []
        with self.db_lock:
            for sql, args in batch:
                self.conn.execute(sql, args)
            self.conn.commit()

    # ---- report ----

    def report(self, out_path):
        with self.db_lock:
            groups = self.conn.execute("""
                SELECT l3, COUNT(*) n, MIN(size) size FROM files
                 WHERE l3 IS NOT NULL GROUP BY l3 HAVING n > 1
                 ORDER BY (COUNT(*)-1)*MIN(size) DESC""").fetchall()
        lines = ["# Дубликати, потвърдени с пълен SHA-256", ""]
        total = 0
        for l3, n, size in groups:
            total += (n - 1) * size
        lines.append(f"Групи: {len(groups):,}  ")
        lines.append(f"Освободимо, ако от всяка група остане по едно копие: "
                     f"**{human(total)}**")
        lines.append("")
        lines.append("| Копия | Размер | Освобождава | Файлове |")
        lines.append("|---|---|---|---|")
        for l3, n, size in groups[:200]:
            with self.db_lock:
                paths = [r[0] for r in self.conn.execute(
                    "SELECT path FROM files WHERE l3=? ORDER BY path", (l3,))]
            shown = "<br>".join(f"`{p}`" for p in paths[:6])
            if len(paths) > 6:
                shown += f"<br>_+{len(paths)-6} още_"
            lines.append(f"| {n} | {human(size)} | {human((n-1)*size)} | {shown} |")
        with open(out_path, "w") as fh:
            fh.write("\n".join(lines))
        log(f"report: {out_path} — {human(total)} reclaimable across "
            f"{len(groups):,} groups")
        return total

    # ---- status ----

    def status(self):
        with self.db_lock:
            for label, sql in (
                    ("директории", "SELECT status, COUNT(*) FROM dirs GROUP BY status"),
                    ("файлове", "SELECT stage, COUNT(*) FROM files GROUP BY stage")):
                rows = self.conn.execute(sql).fetchall()
                print(f"{label}: " + ", ".join(f"{a}={b}" for a, b in rows))
            tot = self.conn.execute(
                "SELECT COUNT(*), SUM(size) FROM files").fetchone()
            print(f"инвентар: {tot[0] or 0:,} файла, {human(tot[1] or 0)}")
            for ts, state, reason in self.conn.execute(
                    "SELECT ts,state,reason FROM pauses ORDER BY ts DESC LIMIT 8"):
                when = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
                print(f"  {when}  {state:6}  {reason}")


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run",
                    choices=["run", "inventory", "status", "report", "health"])
    ap.add_argument("--tier", type=int, choices=[1, 2, 3],
                    help="run one tier only")
    args = ap.parse_args()

    os.makedirs(STATE_DIR, exist_ok=True)
    engine = Engine(os.path.join(STATE_DIR, "dedup.db"))

    if args.command == "status":
        engine.status()
        return 0

    if args.command == "report":
        engine.report(os.path.join(STATE_DIR, "duplicates.md"))
        return 0

    if not assert_readonly(ROOT, env("NASDEDUP_ALLOW_RW", False, bool)):
        return 2

    deprioritise()
    monitor = threading.Thread(target=engine.health.monitor, daemon=True)
    monitor.start()

    def shutdown(signum, _frame):
        log(f"signal {signum} — finishing the current batch and checkpointing")
        engine.stop.set()
        engine.health.stop.set()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    if args.command == "health":
        while not engine.stop.is_set():
            log(f"{'PAUSED' if engine.health.paused else 'ok    '}  "
                f"{engine.health.summary()}  {engine.health.reason}")
            time.sleep(15)
        return 0

    log(f"root={ROOT}  state={STATE_DIR}  workers={WORKERS}  min_size={human(MIN_SIZE)}")
    engine.health.probe_latency()
    engine.health.poll_snmp()
    engine.health.evaluate()

    if args.command in ("run", "inventory"):
        engine.inventory()
    if args.command == "inventory":
        return 0

    engine.mark_unique_sizes()
    engine.compute_dirsigs()
    engine.mark_tree_candidates()
    engine.shortcut_to_full()
    if not args.tier:
        engine.run_tier(3)          # duplicate trees first, in one ordered sweep
    for tier in ([args.tier] if args.tier else [1, 2, 3]):
        if engine.stop.is_set():
            break
        engine.run_tier(tier)

    if engine.stop.is_set():
        return 0                                   # asked to stop; state is on disk

    # A file whose only same-size partner was eliminated by a later tier is orphaned:
    # mark_unique_sizes() has already run, so nothing retires it, and it is not a
    # candidate for any tier either. One such file left the whole job reporting itself
    # unfinished forever, which under systemd Restart=on-failure is a restart loop.
    with engine.db_lock:
        swept = engine.conn.execute("""
            UPDATE files SET stage=9
             WHERE stage IN (0,1,2,5) AND NOT EXISTS (
                   SELECT 1 FROM files g WHERE g.size=files.size
                    AND g.path<>files.path AND g.stage IN (0,1,2,3,5))""").rowcount
        engine.conn.commit()
    if swept:
        log(f"reconcile: {swept:,} orphaned files retired — their size partners were "
            f"eliminated by a later tier")

    engine.report(os.path.join(STATE_DIR, "duplicates.md"))
    with engine.db_lock:
        left = engine.conn.execute(
            "SELECT COUNT(*) FROM files WHERE stage IN (0,1,2,5)").fetchone()[0]
    if left:
        log(f"{left:,} files still mid-pipeline — rerun to continue")
        return 1

    log("pipeline complete — staying up to watch disk usage, load and temperature")
    if env("NASDEDUP_EXIT_WHEN_DONE", False, bool):
        return 0
    while not engine.stop.is_set():
        usage = engine.sample_usage()
        if usage:
            used, free, pct = usage
            log(f"watch: {human(used)} used ({pct:.1f}%), {human(free)} free · "
                f"{engine.health.summary()}")
        engine.stop.wait(600)
    return 0


if __name__ == "__main__":
    sys.exit(main())
