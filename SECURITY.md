# Security policy

Nizho is a **developer preview**. Don't rely on it to protect anything
important yet; see [what we can and can't show](website/index.html#honest).

## Reporting a vulnerability

Please report security problems **privately**, not in a public issue:

1. Open the repository's **Security** tab on GitHub.
2. Choose **Report a vulnerability** (GitHub private vulnerability reporting).

Include what you found, how to reproduce it, and which Nizho version or image
you used (`cat /usr/lib/nizho-release`).

## What to expect

- An acknowledgement within 7 days.
- A fix or a clear plan within 90 days, sooner for anything serious.
- Credit in the release notes, if you'd like it.

## In scope

The Nizho image and everything in this repository: `nizhod`, `nizho-pulse`, the
Nizho apps, the Firefox sandbox wrapper, setup, and the image build. Problems in
Fedora, KDE or other upstream software are best reported upstream too; tell us
as well if Nizho makes them worse.

## Wording counts as a bug

If any Nizho screen, document or the website claims more protection than the
system actually provides, report it like a vulnerability. Overstated privacy
claims put people at risk.
