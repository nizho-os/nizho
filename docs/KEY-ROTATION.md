# Changing the image signing key: proposal and runbook

Status on 2026-11-10: a proposal. Nothing here is implemented. No step was run. No key, secret or
workflow was changed to write it. The policy syntax was read from the manual page and was not
tried on a Nizho image.

## How it is today

- One key signs every published image. Its public half is `cosign.pub`, copied into the image as
  `/etc/pki/containers/nizho.pub` (Containerfile).
- The image's `/etc/containers/policy.json` accepts `ghcr.io/nizho-os/nizho` only when signed
  with that key (the `sigstoreSigned` lines in the Containerfile).
  `system/containers/nizho.yaml` tells the tools to look for the signature in the registry.
- The private half is the repository secret `SIGNING_SECRET`. `image.yml` uses it with an empty
  passphrase (`COSIGN_PASSWORD: ""`), so the secret alone is enough to sign.
- There is no second key and no written way to replace the first one.

What follows from this:

- If the private key is lost, no new image can be signed. Installed computers refuse every later
  update.
- If the private key is copied, someone else can sign an image that installed computers accept.
  They would still need to get it into the registry.
- A computer checks a new image with the policy it already has. A new key therefore reaches a
  computer only inside an update signed with a key that computer already trusts.

## Proposal

### 1. Trust two keys

Replace `keyPath` with `keyPaths` in the policy. The manual page says an image is accepted when
its signature matches any key in the list, and that exactly one of `keyPath`, `keyPaths`,
`keyData`, `keyDatas`, `fulcio` and `pki` may be present
([containers-policy.json(5)](https://github.com/containers/container-libs/blob/main/image/docs/containers-policy.json.5.md),
section `sigstoreSigned`).

    {
        "type": "sigstoreSigned",
        "keyPaths": ["/etc/pki/containers/nizho.pub", "/etc/pki/containers/nizho-backup.pub"],
        "signedIdentity": {"type": "matchRepository"}
    }

Do not add a second entry to the list of requirements. The same page says all requirements in
that list must hold at once, so two entries would demand both signatures.

To verify before use: that the container tools in the Fedora 44 image accept `keyPaths`. Fedora
44 ships containers-common 0.67.2 and skopeo 1.22.3. Whether that version reads `keyPaths` was
not checked. Test it with `scripts/test-signing.py` extended to a two-key policy, and on a
built image.

### 2. Keep the second key offline

- Create the backup key on a computer that is not CI. Give it a passphrase.
- Store the private half offline in two places. It never goes into GitHub.
- Only its public half enters the repository.
- Its job is to sign the image that introduces a replacement CI key when the CI key is lost or
  copied.

### 3. Give the CI key a passphrase

Store the passphrase as a second secret. This helps only when the key file leaks without the
secrets store. It needs a change to the signing step of `image.yml`, which is the owner's to make.

## Order of a planned change

The order matters. Signing with the new key first locks out every computer that has not yet
learned it.

1. Publish an image, signed with the current key, whose policy trusts the current and the new key.
2. Wait. There is no telemetry, so nobody can know how many computers have taken that image.
   Choose a period and write it in the release notes.
3. During the wait, sign each image with both keys. A computer with the old policy accepts it
   through the old signature. This needs a second `cosign sign` in `image.yml`.
4. Stop signing with the old key.
5. Later, publish an image whose policy no longer lists the old key.

## If the CI key is lost or copied

1. Create a new CI key.
2. Publish one image signed with the offline backup key. Its policy lists the backup key and the
   new CI key and drops the old one.
3. Computers that already trust the backup key accept it. Computers that never received a two-key
   policy cannot be reached by an update. They need the manual steps below.
4. Revoke the old secret. Note that an attacker holding the old key can still serve images to
   computers that have not updated, if they can also write to the registry or stand between the
   computer and it.

## Ways this fails

- A computer was off during the whole waiting period. It trusts only the old key and refuses
  updates.
- A computer goes back to a version from before step 1. Its policy is the old one again.
- An install from an old ISO has the old policy.
- Someone edited `/etc/containers/policy.json` on their computer. Local changes to `/etc` are kept
  across updates, so the new policy may not arrive.
- The policy file has a mistake. Every update is then refused, or every image is accepted. Test the
  exact file first.
- The backup key is lost too. Then nothing can be signed for existing computers.

Manual repair for a stranded computer, to be written and tested before any rotation: download the
new public key from the website, compare its fingerprint with one published somewhere else, place
it in `/etc/pki/containers/`, and edit the policy. This asks the user to trust the website, so
the wording needs care.

## Before any of this is used

- Test steps 1 to 5 in a VM with throwaway keys and a test registry: an old-policy computer, a
  two-key computer, a rolled back computer.
- Decide the waiting period.
- Decide who holds the offline key and where the two copies are.
