#!/usr/bin/env python3
"""Explicit cleanup of one stopped VM state directory created by run-vm.sh.
Never scans directories recursively or deletes a supplied base image.
"""
import os
from pathlib import Path
import sys
if len(sys.argv)!=2:raise SystemExit('usage: cleanup.py /absolute/path/to/nizho-archon-vm.STATE')
p=Path(sys.argv[1])
if not p.is_absolute() or p.is_symlink() or not p.is_dir() or not p.name.startswith('nizho-archon-vm.'):
    raise SystemExit('Refusing an unrecognized state directory')
if p.stat().st_uid!=os.getuid() or p.stat().st_mode & 0o077:
    raise SystemExit('State directory must be private and owned by this user')
if (p/'owner.marker').is_symlink() or (p/'owner.marker').read_text().strip()!='nizho-archon-vm-v1':
    raise SystemExit('Missing task ownership marker')
pid=int((p/'qemu.pid').read_text().strip())
if pid<=1:raise SystemExit('Invalid VM PID')
try:os.kill(pid,0)
except ProcessLookupError:pass
else:raise SystemExit('PID still exists; refusing to remove potentially active VM state')
allowed={'owner.marker','qemu.pid','disk.qcow2','vars.fd','serial.log','qemu.log','config.txt'}
files=list(p.iterdir())
if any(f.name not in allowed or f.is_symlink() or not f.is_file() for f in files):
    raise SystemExit('Unexpected files: refusing to remove unrelated state')
for f in files:f.unlink()
p.rmdir()
print('Removed only the selected stopped test VM state directory')
