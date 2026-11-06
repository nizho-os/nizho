# Machine-owner image trust

The machine owner selected a separate local signing key and local image
repository. Official Nizho release keys remain independent. No private release
key is included in an installed system or supplied to a build container.

The source includes explicit owner-trust setup and loopback registry units.
They remain dormant and no GPG/skopeo/registry tooling is installed by default.
Provisioning generates a 3072-bit RSA signing key in the root-only
`/var/lib/nizho-owner/private` directory. The public key is installed at
`/etc/pki/containers/nizho-owner.gpg`. Native containers/image `signedBy`
verification applies only to `localhost:30070/nizho-owner`; signatures are kept
in the root-owned local lookaside. Official Nizho's `sigstoreSigned` policy is
preserved. The default policy and Fedora's permissive docker-daemon exception
become `reject`. Other unsigned transport exceptions block provisioning.

TLS uses separate CA, server and client keys. The registry receives only its
server TLS key via systemd credentials, never the CA, client or image-signing
private keys. It runs as a dynamic unprivileged user
with its own storage and listens on 127.0.0.1:30070. Its public certificate is
trusted only for this local endpoint. Mandatory client-certificate authentication
prevents unprivileged local users from uploading content or filling the registry.
The root-only client key is used by native containers/image. Image signature
verification remains independent of TLS authentication. Registry storage is not
a source of authorization to sign or deploy an image. The TLS configuration uses
[Distribution's documented client authentication settings](https://distribution.github.io/distribution/about/configuration/#tls).

Provisioning deliberately refuses existing owner keys, conflicting policy scopes,
or partial initialization. It does not silently rotate keys or weaken policy.
If setup is interrupted, retain the state and inspect `journalctl -u
nizho-owner-init.service`, `policy.before.json`, the public key, and the current
container policy before repairing it. Never delete the signing key while a
deployment still depends on it. Key rotation, certificate renewal and trust
removal are not implemented yet.

On an installed disposable Nizho test VM, an administrator can provision with:

```sh
systemctl start nizho-owner-init.service
systemctl enable --now nizho-owner-registry.service
journalctl -u nizho-owner-init.service -u nizho-owner-registry.service
```

These commands are not host setup instructions. The GTK application does not
execute them. Provisioning is not currently exposed as a package capability.
The init unit is not automatically enabled, so shipping these files alone does
not change an existing machine's image trust.

Validation so far: five policy/filesystem unit tests pass, and the native GPG
test generates an isolated key, verifies with only the public key, rejects
modified content, and checks private file permissions. Run:

```sh
python3 -m unittest discover -s archon-service/owner -v
python3 scripts/test-archon-owner-key.py
PODMAN="$HOME/.cache/nizho-build-tools/podman" bash scripts/test-archon-owner-repository.sh
```

An earlier disposable-container test passed TLS client-certificate rejection,
GPG image signing, verification by manifest digest, and rejection of missing or
corrupted signatures. Its synthetic empty image is only a cryptographic fixture,
not a bootc deployment. The test is not part of the shipped package workflow.

**Still incomplete:** installed-unit provisioning, actual isolated Archon RPM
image builds, authorized signing/staging orchestration, activation correlation
and rollback tests. System package mutation remains unadvertised until those
boundaries are implemented and validated.
