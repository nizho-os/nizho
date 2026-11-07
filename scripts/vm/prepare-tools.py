#!/usr/bin/env python3
"""Extract signature-verified Arch VM tools into a private task cache.
No pacman installation, sudo, host configuration edits or host services.
"""
import hashlib
import fcntl
import os
from pathlib import Path
import subprocess
import urllib.request

root = Path(os.environ.get('NIZHO_BUILD_TOOLS', Path.home()/'.cache/nizho-build-tools'))
manifest = Path(__file__).resolve().parents[2]/'integrations/archon/host-tools.sha256'
root.mkdir(parents=True,exist_ok=True)
lock=(root/'toolchain.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
packages=root/'packages';packages.mkdir(exist_ok=True)
runtime=root/'runtime';runtime.mkdir(exist_ok=True)
for line in manifest.read_text().splitlines():
    digest,url=line.split()
    if not url.startswith('https://fastly.mirror.pkgbuild.com/') or '/' in url.rsplit('/',1)[-1]:raise ValueError('Unexpected source')
    path=packages/url.rsplit('/',1)[-1]
    if not path.exists():urllib.request.urlretrieve(url,path)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Tool checksum mismatch: '+path.name)
    signature=path.with_name(path.name+'.sig')
    if not signature.exists():urllib.request.urlretrieve(url+'.sig',signature)
    subprocess.run(['gpgv','--keyring','/etc/pacman.d/gnupg/pubring.gpg',str(signature),str(path)],check=True)
    subprocess.run(['bsdtar','-xf',str(path),'-C',str(runtime),'--no-same-owner'],check=True)
subprocess.run(['go','install','github.com/containers/gvisor-tap-vsock/cmd/gvproxy@6dbb5d4fa0033356c72a201346f8350594229868'], env=dict(os.environ,GOBIN=str(runtime/'usr/bin')),check=True)
(root/'crun-task').write_text(f'#!/bin/sh\nexport LD_LIBRARY_PATH="{runtime}/usr/lib"\nexec "{runtime}/usr/bin/crun" "$@"\n')
(root/'crun-task').chmod(0o755)
(root/'containers.conf').write_text(f'''[containers]
seccomp_profile="{runtime}/usr/share/containers/seccomp.json"
[engine]
runtime="crun"
cgroup_manager="cgroupfs"
events_logger="file"
helper_binaries_dir=["{runtime}/usr/lib/podman","{runtime}/usr/bin"]
conmon_path=["{runtime}/usr/bin/conmon"]
[engine.runtimes]
crun=["{root}/crun-task"]
''')
config=root/'config/containers';config.mkdir(parents=True,exist_ok=True)
(config/'policy.json').write_bytes((runtime/'etc/containers/policy.json').read_bytes())
(root/'podman').write_text(f'''#!/bin/sh
export PATH="{runtime}/usr/bin:{runtime}/usr/lib:$PATH"
export LD_LIBRARY_PATH="{runtime}/usr/lib${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"
export XDG_CONFIG_HOME="{root}/config"
export CONTAINERS_CONF="{root}/containers.conf"
exec flock --close -s "{root}/toolchain.lock" "{runtime}/usr/bin/podman" --root "{root}/storage" --runroot "${{XDG_RUNTIME_DIR:?}}/nizho-podman" "$@"
''');(root/'podman').chmod(0o755)
print('Rootless Podman:',root/'podman')
