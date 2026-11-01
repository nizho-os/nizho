# Proprietary NVIDIA driver as an opt-in image

Status: design only. Nothing here is built, and nothing here has run on a machine.

## Why it is not in the default image

The default Nizho image runs NVIDIA cards on the open driver (nouveau, with NVK for Vulkan). The
proprietary driver is left out for four reasons, from web research done on 2026-11-01 (a
subagent's report, not independently verified):

- Every way of getting it onto an image-based Fedora comes from a third party (RPM Fusion or
  Universal Blue). The image Nizho signs would then contain code Nizho cannot review.
- A proprietary kernel module must be signed with a key the machine trusts. With Secure Boot on,
  the user has to enrol that key themselves, or the module does not load.
- Whether NVIDIA's licence lets a public image carry the driver was not confirmed. That needs a
  legal read before anything is published.
- The research could not confirm what the proprietary driver does or does not send anywhere, so
  Nizho cannot make its usual claims about it.

The cost of leaving it out: weaker performance and power management on NVIDIA cards, and no CUDA.

## The idea

A second image, built by CI from the default one, that the user chooses to switch to.

    ghcr.io/nizho-os/nizho          the default image (open driver only)
    ghcr.io/nizho-os/nizho-nvidia   FROM the default image, plus the proprietary driver

Nothing changes for anyone who does not switch. The default image never contains the driver, never
trusts the third-party repository, and is never signed with anything the variant needs.

## How it would be built

1. A new workflow builds `Containerfile.nvidia`, which starts `FROM ghcr.io/nizho-os/nizho:<tag>`
   at a pinned digest, so the variant always matches a specific default build.
2. The third-party repository is added inside that build stage only, its signing key is pinned by
   fingerprint, and the repository file is removed again at the end of the stage.
3. The kernel module is built for the exact kernel in the base image, inside the Containerfile, and
   the build fails if the module does not build or does not load-check (`modinfo`). The build does
   not rely on a build step that runs later on the user's machine.
4. The module is signed with a separate Nizho module-signing key kept in CI. Its public certificate
   ships in the variant image so users can enrol it.
5. The image is signed with the existing Nizho image key and published. The signature policy on
   installed machines (`policy.json` and the registries entry) names only the default image today,
   so the variant needs its own entry. Without it, an installed Nizho would refuse to switch.

## How a user would switch

1. A command, `nizho gpu`, explains what the choice means in plain words and asks first.
2. If Secure Boot is on, it starts the key enrolment (`mokutil --import`) and tells the user that a
   blue screen will appear on the next boot where they must enter the password they just chose.
3. It runs `bootc switch ghcr.io/nizho-os/nizho-nvidia:<tag>`, then asks for a reboot.
4. Going back is the same command in reverse, or the normal rollback to the previous deployment.

## Safety nets

- A failed boot rolls back to the default deployment automatically (greenboot already does this).
- A new health check would roll back if the variant booted but the module did not load, so a missing
  key enrolment cannot leave someone at a blank screen for good. This check does not exist yet and
  has to be tested on a Secure Boot machine.
- The open driver stays installed in the variant, so a failed load falls back to it.

## What Nizho would and would not say about it

Would say: this image adds NVIDIA's proprietary driver, built from a third-party repository and
signed by Nizho. It is optional. You must enrol a key if Secure Boot is on.

Would not say: that the driver is private, that it has been audited, or that it sends nothing.
Nizho cannot check closed code. The privacy dashboard would show the driver as "third-party,
closed" and would not count it as one of the checked settings.

## Open questions (none are answered yet)

1. **Licence.** May the driver be redistributed in a public container image? If not, the variant
   cannot be published, and the only option is for each user to build it locally. That needs the
   licence read by someone who can judge it.
2. **Key enrolment.** Is there a way to have Nizho's key trusted without every user enrolling it
   (a signed shim)? The research did not find one. Without it, Secure Boot users go through the
   enrolment screen.
3. **Hybrid laptops.** The AMD-plus-NVIDIA setup (display on AMD, NVIDIA for offload) on KDE
   Wayland is not verified on an image like this.
4. **Kernel updates.** Builds have failed on new kernels before (a 6.19 report). The variant would
   always lag the default image by however long a fix takes, and its build would be allowed to fail
   without blocking the default image.
5. **Is it worth it?** The open driver's real-world result on an RTX 4060 has not been measured.
   If it is good enough, this whole layer may not be needed.

## Order of work

1. Boot the current Nizho stick on the RTX 4060 laptop and record what the open driver does
   (desktop smoothness, an offload test, battery, temperatures). This decides whether to continue.
2. Get an answer on the licence.
3. Build the variant in CI without publishing it, and boot it on that laptop with Secure Boot off,
   then on.
4. Write `nizho gpu` and the extra policy entry with tests.
5. Only then publish, with a note in the README stating what was and was not tested.

## Not covered

AMD and Intel graphics need nothing from this. Other proprietary hardware drivers (Wi-Fi chips,
for example) are a separate question.
