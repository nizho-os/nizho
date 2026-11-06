# Machine-owner image trust

Machine-owner image signing is future work. The official Nizho release keys
remain independent; this experimental design does not place private release keys
in an image or package build container.

The repository retains experimental owner-trust setup and loopback registry
source files for future development. They are not copied into the Nizho image,
and no GPG/skopeo/registry tooling is installed for this unsupported feature.
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

The prototype trust configuration code and units remain in the source tree for
future work, but are not installed into Nizho. There is no setup command or
supported owner repository in the current image.

Prototype-only validation: five policy/filesystem tests pass, and the native GPG
test generates an isolated key, verifies with only the public key, rejects
modified content, and checks private file permissions. These tests do not mean
machine-owner signing is shipped. Run:

```sh
python3 -m unittest discover -s archon-service/owner -v
python3 scripts/test-archon-owner-key.py
```

**Still incomplete:** installed-unit provisioning, actual isolated Archon RPM
image builds, authorized signing/staging orchestration, activation correlation
and rollback tests. System package mutation remains unadvertised until those
boundaries are implemented and validated.
