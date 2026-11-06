#!/usr/bin/env python3
"""Prepare reviewed integration changes against the recorded independent revision.
This does not stage, commit, reset or modify the Archon source checkout.
"""
from pathlib import Path
import subprocess
import hashlib
import sys
root=Path(__file__).resolve().parents[1]
source=Path(sys.argv[1]) if len(sys.argv)>1 else root.parents[1]/'archon'
revision=(root/'integrations/archon/source-revision').read_text().strip()
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()
if head!=revision:raise SystemExit('Source HEAD differs from recorded pin; review the pin before preparing a patch')
files=['crates/archon-distro/src/lib.rs','crates/archon-distro/src/flatpak.rs','crates/archon-distro/src/flatpak_bridge.py','crates/archon/src/commands/mod.rs','crates/archon/src/main.rs','crates/archon/src/commands/nizho.rs','crates/archon/src/commands/nizho_client.py','scripts/test-flatpak-native.py','scripts/test-flatpak-metadata.py']
patch=b''
for file in files:
    tracked=subprocess.run(['git','cat-file','-e',f'HEAD:{file}'],cwd=source,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
    if tracked:patch+=subprocess.check_output(['git','diff','HEAD','--',file],cwd=source)
    else:
        result=subprocess.run(['git','diff','--no-index','--','/dev/null',file],cwd=source,capture_output=True)
        if result.returncode!=1:raise SystemExit('Cannot prepare '+file)
        patch+=result.stdout
(root/'integrations/archon/nizho-integration.patch').write_bytes(patch)
(root/'integrations/archon/source-patch.sha256').write_text(hashlib.sha256(patch).hexdigest()+'  nizho-integration.patch\n')
print('Prepared pinned integration patch:',len(patch),'bytes')
