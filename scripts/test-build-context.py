#!/usr/bin/env python3
"""Check that the repository's layout and the files that point into it agree, offline.

    python3 -I scripts/test-build-context.py

The image is built from this repository, so a moved or renamed folder breaks the build an hour in, after the
longest part of it. These tests catch that in seconds: every file the Containerfile copies must exist, no copied
source may be hidden by .containerignore, every path the CI workflows pass to a tool must exist, every folder the
README lists must exist, and every relative link in the Markdown files must lead somewhere.
"""
import fnmatch
import re
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [f for f in out.decode().split("\0") if f and (ROOT / f).exists()]


def containerfile_sources():
    """Yield (line number, source) for every COPY or ADD that reads from the repository, not from another stage."""
    lines = (ROOT / "Containerfile").read_text().splitlines()
    i = 0
    while i < len(lines):
        start, line = i + 1, lines[i]
        while line.rstrip().endswith("\\") and i + 1 < len(lines):
            i += 1
            line = line.rstrip()[:-1] + " " + lines[i].strip()
        i += 1
        if not re.match(r"\s*(COPY|ADD)\s", line):
            continue
        words = shlex.split(line, comments=True)[1:]
        flags = [w for w in words if w.startswith("--")]
        args = [w for w in words if not w.startswith("--")]
        if any(f.startswith("--from") for f in flags) or len(args) < 2:
            continue
        for src in args[:-1]:
            if not src.startswith(("http://", "https://")):
                yield start, src


class BuildContextTests(unittest.TestCase):
    def test_every_file_the_containerfile_copies_exists(self):
        missing = [f"line {n}: {s}" for n, s in containerfile_sources()
                   if not list(ROOT.glob(s.rstrip("/")))]
        self.assertEqual(missing, [], "COPY sources that are not in the repository")

    def test_the_containerfile_copies_something(self):
        self.assertGreater(len(list(containerfile_sources())), 50)

    def test_nothing_the_image_needs_is_hidden_by_containerignore(self):
        patterns = [p.strip() for p in (ROOT / ".containerignore").read_text().splitlines()
                    if p.strip() and not p.startswith("#") and not p.startswith("!")]
        hidden = []
        for n, src in containerfile_sources():
            for p in patterns:
                flat = p.rstrip("/")
                if "/" not in flat and not flat.startswith("**") and fnmatch.fnmatch(src.rstrip("/"), flat):
                    hidden.append(f"line {n}: {src} matches {p}")
        self.assertEqual(hidden, [])

    def test_the_paths_the_workflows_pass_to_shellcheck_exist(self):
        bad = []
        for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
            for line in wf.read_text().splitlines():
                m = re.search(r"\b(shellcheck|bash -n)\s+(.*)", line)
                if not m or "run:" not in line and not line.strip().startswith("run"):
                    continue
                for word in shlex.split(m.group(2).split("&&")[0].split("||")[0], comments=True):
                    if word.startswith("-") or word in ("bash", "-n"):
                        continue
                    if not (ROOT / word).exists():
                        bad.append(f"{wf.name}: {word}")
        self.assertEqual(bad, [])

    def test_the_folders_the_readme_lists_exist(self):
        text = (ROOT / "README.md").read_text()
        block = re.search(r"## Repository\s+```\n(.*?)```", text, re.S)
        self.assertIsNotNone(block, "README has no Repository section")
        missing = []
        for line in block.group(1).splitlines():
            for name in re.findall(r"(?:^|\s{2,})([A-Za-z0-9_.][\w.\-/]*)(?=\s{2,}|\s*$)", line):
                if name.endswith("/") or "." in name or "-" in name or name == "Containerfile":
                    if not (ROOT / name.rstrip("/")).exists():
                        missing.append(name)
        self.assertEqual(missing, [], "README lists paths that are not there")

    def test_relative_markdown_links_lead_somewhere(self):
        broken = []
        for f in tracked():
            if not f.endswith(".md") or "/target/" in f or f.startswith("website/"):
                continue
            base = (ROOT / f).parent
            for target in re.findall(r"\]\(([^)\s]+)\)", (ROOT / f).read_text(errors="ignore")):
                target = target.split("#")[0]
                if not target or target.startswith(("http://", "https://", "mailto:", "/")):
                    continue
                if not (base / target).exists():
                    broken.append(f"{f}: {target}")
        self.assertEqual(broken, [], "links to files that are not there")


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=1))
