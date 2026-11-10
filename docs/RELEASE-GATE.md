# Testing an image before it becomes `latest`: proposal

Status on 2026-11-10: a proposal. Nothing here is implemented or tested. `image.yml` and
`disk.yml` were read and not changed.

## How it is today

- Every push to `main` that touches more than docs, the website or assets builds the image and
  pushes it to `ghcr.io/nizho-os/nizho:latest`. A weekly run does the same.
- Installed computers follow `latest`. They download it in the background and use it at the next
  restart.
- No step starts the image before it is published. The first start of each image happens on a
  user's computer.
- The image is pushed to `latest` first and signed afterwards. If signing fails, `latest` points
  at an unsigned image. Computers refuse it, so the failure is safe, and updates stop until a
  later run succeeds.
- `checks.yml` tests scripts and Rust code. It does not start an image.

Automatic rollback is the only protection after publishing. See [RECOVERY.md](RECOVERY.md) for
what it has and has not been shown to catch.

## Proposal

1. **Build and push as `candidate`.** Same build. Push by digest and tag it `candidate`. Sign the
   digest. Do not move `latest`.
2. **Start it in a VM in CI.** Make a disk from the candidate, start it with QEMU, and wait for a
   fixed marker: `greenboot-healthcheck.service` finished without error and `sddm.service` active.
   Use the serial console or SSH. `scripts/vm/` already has pieces for building and running a VM.
3. **Rollback smoke test.** In the same VM, update from the candidate to a test image with a
   required check that always fails, served from a registry inside the job. Pass when the VM is
   back on the candidate digest without help. Then update to a harmless test image and check that
   it stays.
4. **Promote.** Only after steps 2 and 3 pass, point `latest` at the candidate digest. This is a
   tag change on the registry. The image and its signature stay the same, because the signature
   belongs to the digest.

## What it needs

- KVM on the runner. GitHub's hosted Linux runners offer `/dev/kvm`, which has to be confirmed for
  this repository's runner type. Without KVM the test is too slow to be useful.
- Disk space. The image is about 12 GB and the job already frees space to build it. A disk image
  and a test registry need more. This may need a second job that pulls the candidate.
- Time. The build job already allows up to 300 minutes. Measure the added time before choosing a
  timeout.
- The test images for step 3 need a signature the VM accepts, or a VM policy that trusts a test
  key. Never put the real key in the test.
- Changes to the push and sign steps of `image.yml`. Those steps are the owner's to change.

## Risks

- A flaky VM test blocks every release, including security updates. Keep a manual way to promote,
  and log each use.
- A VM is not a laptop. Graphics drivers, Wi-Fi and suspend are not tested by this gate.
- The gate tests a fresh install updated once. It does not test an update from an older release.
- A test that always passes is worse than none. Check once that the gate fails on a broken image.

## Unpinned actions in the publishing workflows

`checks.yml` and `pages.yml` now name each action by commit. `archon.yml` already did. These lines
still use a tag that its owner can move, in the workflows that hold the signing secret or produce
install media:

| Workflow | Line | Action |
|---|---|---|
| `image.yml` | 62 | `actions/checkout@v4` |
| `image.yml` | 94 | `sigstore/cosign-installer@v3` |
| `disk.yml` | 39 | `actions/checkout@v4` |
| `disk.yml` | 41 | `sigstore/cosign-installer@v3` |
| `disk.yml` | 82 | `actions/upload-artifact@v4` |

Commits those tags pointed at on 2026-11-10, read with `gh api`:

| Tag | Commit | Release |
|---|---|---|
| `actions/checkout@v4` | `11d5960a326750d5838078e36cf38b85af677262` | v4.4.0 |
| `sigstore/cosign-installer@v3` | `398d4b0eeef1380460a10c8013a76f728fb906ac` | v3.9.1 |
| `actions/upload-artifact@v4` | `ea165f8d65b6e75b540449e92b4886f43607fa02` | v4.6.2 |

`image.yml` also runs the container `quay.io/fedora/fedora-bootc:44` by tag (line 75). `disk.yml`
runs only the Nizho image itself.
