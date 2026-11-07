#!/usr/bin/env python3
"""Prepare the exact organization-tagged Archon source in an external cache."""
import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
tag = (root / 'integrations/archon/source-tag').read_text().strip()
revision = (root / 'integrations/archon/source-revision').read_text().strip()
if not tag or len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
    raise SystemExit('Invalid Archon source tag or revision pin')
cache = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'nizho-archon/source' / f'{tag}-{revision}'
if not cache.exists():
    cache.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q', str(cache)], check=True)
    subprocess.run([
        'git', '-C', str(cache), 'fetch', '--depth', '1',
        'https://github.com/nizho-os/archon.git', f'refs/tags/{tag}',
    ], check=True)
    subprocess.run(['git', '-C', str(cache), 'checkout', '--detach', 'FETCH_HEAD'], check=True)
    actual = subprocess.check_output(['git', '-C', str(cache), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != revision:
        raise SystemExit(f'Archon tag {tag} resolved to {actual}, expected {revision}')
else:
    head = subprocess.check_output(['git', '-C', str(cache), 'rev-parse', 'HEAD'], text=True).strip()
    if head != revision:
        raise SystemExit('Prepared source pin mismatch')
    dirty = subprocess.run(['git', '-C', str(cache), 'diff', '--quiet']).returncode
    untracked = subprocess.check_output(
        ['git', '-C', str(cache), 'ls-files', '--others', '--exclude-standard'], text=True
    )
    if dirty or untracked:
        raise SystemExit('Prepared source was modified; refusing a non-reproducible build')
link = root / 'integrations/archon/source'
if link.is_symlink():
    link.unlink()
elif link.exists():
    raise SystemExit('Refusing to overwrite existing source directory')
link.symlink_to(cache, target_is_directory=True)
print(f'Archon remains independent; prepared source: {cache}')
