#!/usr/bin/env python3
"""Prepare exact Archon source outside the OS tree, without editing user repos."""
import hashlib
import os
from pathlib import Path
import subprocess
root=Path(__file__).resolve().parents[1]
revision=(root/'integrations/archon/source-revision').read_text().strip()
patch=root/'integrations/archon/nizho-integration.patch'
digest=hashlib.sha256(patch.read_bytes()).hexdigest()
cache=Path(os.environ.get('XDG_CACHE_HOME',Path.home()/'.cache'))/'nizho-archon/source'/f'{revision}-{digest}-v2'
if not cache.exists():
    cache.mkdir(parents=True)
    subprocess.run(['git','init','-q',str(cache)],check=True)
    subprocess.run(['git','-C',str(cache),'fetch','--depth','1','https://github.com/ravindran-dev/archon.git',revision],check=True)
    subprocess.run(['git','-C',str(cache),'checkout','--detach','FETCH_HEAD'],check=True)
    subprocess.run(['git','-C',str(cache),'apply',str(patch)],check=True)
    subprocess.run(['git','-C',str(cache),'add','--all'],check=True)
    tree=subprocess.check_output(['git','-C',str(cache),'write-tree'],text=True).strip()
    (cache/'.git/nizho-prepared-tree').write_text(tree+'\n')
else:
    head=subprocess.check_output(['git','-C',str(cache),'rev-parse','HEAD'],text=True).strip()
    if head!=revision:raise SystemExit('Prepared source pin mismatch')
    expected=(cache/'.git/nizho-prepared-tree').read_text().strip()
    tree=subprocess.check_output(['git','-C',str(cache),'write-tree'],text=True).strip()
    changed=subprocess.run(['git','-C',str(cache),'diff','--quiet']).returncode
    untracked=subprocess.check_output(['git','-C',str(cache),'ls-files','--others','--exclude-standard'],text=True)
    if expected!=tree or changed or untracked:raise SystemExit('Prepared source was modified; refusing a non-reproducible build')
link=root/'integrations/archon/source'
if link.is_symlink():link.unlink()
elif link.exists():raise SystemExit('Refusing to overwrite existing source directory')
link.symlink_to(cache,target_is_directory=True)
print('Archon remains independent; prepared source:',cache)
