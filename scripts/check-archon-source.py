#!/usr/bin/env python3
"""Verify the organization-owned Archon tag resolves to the recorded commit."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
tag = (root / 'integrations/archon/source-tag').read_text().strip()
revision = (root / 'integrations/archon/source-revision').read_text().strip()
if not tag or any(c not in '0123456789abcdef' for c in revision) or len(revision) != 40:
    raise SystemExit('Invalid Archon source tag or revision pin')
with tempfile.TemporaryDirectory(prefix='nizho-archon-source-') as directory:
    subprocess.run(['git', 'init', '-q', directory], check=True)
    subprocess.run([
        'git', '-C', directory, 'fetch', '--depth', '1',
        'https://github.com/nizho-os/archon.git', f'refs/tags/{tag}',
    ], check=True)
    subprocess.run(['git', '-C', directory, 'checkout', '--detach', 'FETCH_HEAD'], check=True)
    actual = subprocess.check_output(
        ['git', '-C', directory, 'rev-parse', 'HEAD'], text=True
    ).strip()
    if actual != revision:
        raise SystemExit(f'Archon tag {tag} resolved to {actual}, expected {revision}')
    print(f'PASS organization Archon tag {tag} -> {revision}')
