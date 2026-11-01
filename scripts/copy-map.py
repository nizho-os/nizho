#!/usr/bin/env python3
"""Print which repository file ends up at which path in the image, by reading the Containerfile's COPY lines.

    python3 -I scripts/copy-map.py [repository-root]            # JSON: {"/usr/libexec/x": "sha256...", ...}
    python3 -I scripts/copy-map.py --compare OLD-ROOT NEW-ROOT   # exit 1 if the two layouts install different files

Only COPY and ADD from the repository are read (not --from=stage, not downloads). Use --compare after moving
folders around: if the maps are equal, the files copied into the image are unchanged. It does not run the RUN steps.
"""
import fnmatch
import hashlib
import json
import re
import shlex
import sys
from pathlib import Path


def ignore_rules(root):
    path = root / ".containerignore"
    rules = []
    if path.exists():
        rules = [l.strip() for l in path.read_text().splitlines() if l.strip() and not l.startswith(("#", "!"))]
    return rules


def ignored(rel, rules):
    parts = rel.split("/")
    for rule in rules:
        r = rule.rstrip("/")
        if r.startswith("**/"):
            if r[3:] in parts[:-1] or fnmatch.fnmatch(parts[-1], r[3:]):
                return True
        elif "/" not in r:
            if fnmatch.fnmatch(parts[0], r) and len(parts) == 1:
                return True
        elif fnmatch.fnmatch(rel, r) or rel.startswith(r + "/"):
            return True
    return False


def copy_lines(root):
    lines = (root / "Containerfile").read_text().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        while line.rstrip().endswith("\\") and i < len(lines):
            line = line.rstrip()[:-1] + " " + lines[i].strip()
            i += 1
        if not re.match(r"\s*(COPY|ADD)\s", line):
            continue
        words = shlex.split(line, comments=True)[1:]
        flags = [w for w in words if w.startswith("--")]
        args = [w for w in words if not w.startswith("--")]
        if any(f.startswith("--from") for f in flags) or len(args) < 2:
            continue
        if any(a.startswith(("http://", "https://")) for a in args[:-1]):
            continue
        yield args[:-1], args[-1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_map(root):
    root = Path(root).resolve()
    rules = ignore_rules(root)
    result = {}
    for sources, dest in copy_lines(root):
        into_dir = dest.endswith("/") or len(sources) > 1
        for src in sources:
            matches = sorted(root.glob(src.rstrip("/")))
            if not matches:
                raise SystemExit(f"COPY source not found: {src}")
            for m in matches:
                rel = m.relative_to(root).as_posix()
                if m.is_dir():
                    for f in sorted(m.rglob("*")):
                        frel = f.relative_to(root).as_posix()
                        if f.is_file() and not ignored(frel, rules):
                            result[dest.rstrip("/") + "/" + f.relative_to(m).as_posix()] = digest(f)
                elif not ignored(rel, rules):
                    target = dest.rstrip("/") + "/" + m.name if into_dir else dest
                    result[target] = digest(m)
    return result


def main(argv):
    if len(argv) == 4 and argv[1] == "--compare":
        old, new = build_map(argv[2]), build_map(argv[3])
        gone, added = sorted(set(old) - set(new)), sorted(set(new) - set(old))
        changed = sorted(p for p in set(old) & set(new) if old[p] != new[p])
        for label, items in (("no longer installed", gone), ("newly installed", added), ("different content", changed)):
            for p in items:
                print(f"{label}: {p}")
        print(f"{len(old)} files before, {len(new)} after; {len(gone)} gone, {len(added)} new, {len(changed)} changed")
        return 1 if gone or added or changed else 0
    json.dump(build_map(argv[1] if len(argv) > 1 else "."), sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
