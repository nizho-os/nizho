#!/usr/bin/env python3
"""Validate the exact upstream-plus-patch source without changing either repo."""
from pathlib import Path
import subprocess
import tempfile
r=Path(__file__).resolve().parents[1]
revision=(r/'integrations/archon/source-revision').read_text().strip()
assert len(revision)==40 and all(c in '0123456789abcdef' for c in revision)
with tempfile.TemporaryDirectory(prefix='nizho-archon-source-') as directory:
    subprocess.run(['git','init','-q',directory],check=True)
    subprocess.run(['git','-C',directory,'fetch','--depth','1','https://github.com/ravindran-dev/archon.git',revision],check=True)
    subprocess.run(['git','-C',directory,'checkout','--detach','FETCH_HEAD'],check=True)
    subprocess.run(['git','-C',directory,'apply','--check',str(r/'integrations/archon/nizho-integration.patch')],check=True)
    print('PASS pinned upstream source and integration patch')
