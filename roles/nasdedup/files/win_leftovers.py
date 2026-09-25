#!/usr/bin/env python3
"""#1559: read-only report of leftovers from earlier Windows installs under given directories.

Owner, 2026-09-02: «сканираш конкретни директории за останали файлове от предишни windows
инсталации - такива които няма да ми трябват». ICYGEN backups are sensitive: only install/system
folders are candidates, never profile data.

The scan never writes, moves or deletes anything under the roots; it only stats. Every reported
entry is marked "system" (install/system leftover) or "user" (profile data, excluded). A path is
"user" when any component is a profile root (Users, Documents and Settings, Home) — that rule wins
over every system name, so e.g. Users/x/AppData/.../Windows is never a candidate.

    win_leftovers.py ROOT [ROOT ...] [--depth N] [--json OUT]
"""
import argparse
import json
import os
import stat
import sys

# Top-level folders/files a Windows install or its tooling leaves behind (lowercase).
SYSTEM_NAMES = {
    "windows", "windows.old", "program files", "program files (x86)", "programdata",
    "$recycle.bin", "recycler", "recycled", "system volume information", "recovery",
    "$windows.~bt", "$windows.~ws", "$winreagent", "$sysreset", "$getcurrent",
    "perflogs", "msocache", "config.msi", "boot", "efi", "intel", "amd", "nvidia", "drivers",
    "esd", "onedrivetemp", "pagefile.sys", "hiberfil.sys", "swapfile.sys", "dumpstack.log",
    "dumpstack.log.tmp", "bootmgr", "bootnxt", "bootsect.bak", "ntldr", "ntdetect.com",
    "boot.ini", "io.sys", "msdos.sys", "autoexec.bat", "config.sys",
}
# Profile roots: anything at or below them is user data (lowercase).
USER_ROOTS = {"users", "documents and settings", "home"}


def is_user_path(rel):
    """Tested rule: a relative path is user data if any component is a profile root."""
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    return any(p.lower() in USER_ROOTS for p in parts)


def classify(rel):
    """'user' | 'system' | None for a path relative to a scan root."""
    if is_user_path(rel):
        return "user"
    name = os.path.basename(rel.rstrip("/")).lower()
    return "system" if name in SYSTEM_NAMES else None


def tree_size(path):
    """Bytes under path (lstat, symlinks not followed); unreadable entries are skipped."""
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_size
    total = 0
    for dirpath, dirnames, filenames in os.walk(path, onerror=lambda e: None):
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def scan(root, depth=3):
    """Yield {root, path, kind, size}. System hits are not descended into; user roots are
    reported once (kind='user', size None — profile data is not measured or walked)."""
    root = os.path.abspath(root)

    def walk(cur, level):
        try:
            entries = sorted(os.scandir(cur), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            rel = os.path.relpath(entry.path, root)
            kind = classify(rel)
            if kind == "user":
                yield {"root": root, "path": rel, "kind": "user", "size": None}
                continue
            if kind == "system":
                yield {"root": root, "path": rel, "kind": "system", "size": tree_size(entry.path)}
                continue
            if level < depth and entry.is_dir(follow_symlinks=False):
                yield from walk(entry.path, level + 1)

    yield from walk(root, 1)


def human(n):
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024 or unit == "T":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--depth", type=int, default=3, help="how deep to look for system folders")
    ap.add_argument("--json", help="also write the rows as JSON here")
    args = ap.parse_args(argv)
    rows = [r for root in args.roots for r in scan(root, args.depth)]
    total = 0
    for r in rows:
        size = "-" if r["size"] is None else human(r["size"])
        print(f"{r['kind']}\t{size}\t{os.path.join(r['root'], r['path'])}")
        total += r["size"] or 0
    print(f"# system leftovers: {sum(r['kind'] == 'system' for r in rows)} "
          f"({human(total)}); user paths excluded: {sum(r['kind'] == 'user' for r in rows)}",
          file=sys.stderr)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rows, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
