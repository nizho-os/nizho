# Performance

Nizho has budgets for boot time and memory. None of them has been measured yet. This page says
what the budgets are, how to measure them, and what to write down with each result.

## Budgets

These come from [DP1-EXIT-CRITERIA.md](DP1-EXIT-CRITERIA.md).

| What | Budget | Measured |
|---|---|---|
| Boot time in a VM | 20 s or less | Not yet |
| nizhod memory | 30 MB or less | Not yet |
| Desktop memory at idle | 2.5 GB or less | Not yet |

A budget counts as met only when a recorded result below shows it.

## The script

`scripts/measure-baseline.sh` prints one JSON record. It only reads. It changes no setting, starts
or stops nothing and needs no root.

It waits 60 seconds so the desktop can settle, then records:

- `systemd-analyze time` and the 15 slowest units from `systemd-analyze blame`
- memory from `/proc/meminfo` and the 15 processes using the most memory (RSS)
- enabled and running services, for the system and for the user
- the number of processes, the kernel version and the CPU count
- whether it runs in a VM (`systemd-detect-virt`)
- the booted image and digest, when `rpm-ostree status` gives them
- battery readings, when the machine has a battery

A value it cannot read is `null`. Use `--idle SECONDS` to change the wait and `--note TEXT` to add
a note to the record.

RSS counts shared memory once for every process that maps it. Adding up the RSS column gives more
than the machine uses. For the idle desktop budget, use `total` minus `available` from
`memory_kb`.

## In a VM

1. Boot the image as in [TESTING.md](TESTING.md) and sign in to Plasma.
2. Copy the script into the VM and run it as the signed-in user:
   `bash measure-baseline.sh --note "VM, 4 GB, 2 CPUs" > baseline.json`
3. Run it on three separate boots. Boot time in a VM depends on the host, so write down the host
   and the VM's memory and CPU count.

## On a laptop

1. Charge the battery, unplug the charger and close other apps.
2. Sign in and run the script straight away, as above, with a note that names the laptop.
3. For battery use, run it again after a fixed time at idle and compare `energy_now`. Keep the
   screen brightness the same for both runs.

## What to record with every result

- The exact image: the commit it was built from and the image digest. The script records the
  digest when rpm-ostree gives it. Write the commit by hand.
- VM or real hardware. For hardware, the model, CPU, memory and disk.
- The date, and whether it was the first boot after an update. The first boot after an update
  does extra work and is slower.

Results go in a table on this page, one row per run, with a link to the JSON file.

## Results

None yet.
