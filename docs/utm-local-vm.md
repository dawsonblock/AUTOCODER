# UTM Local VM Path

This document makes the macOS local-VM path explicit for Kernel Omega V19.

Official UTM project:

- GitHub: [utmapp/UTM](https://github.com/utmapp/UTM)
- Releases: [UTM releases](https://github.com/utmapp/UTM/releases)

## What UTM is for here

Use UTM when you want a local Linux VM on the same Mac instead of renting a remote Linux host. It is the intended VM host for the "Path 2" workflow described during planning.

Within this repo, UTM is for:

- running Ubuntu locally on macOS
- testing Linux-only bootstrap logic without leaving your Mac
- validating the Firecracker/KVM path in a VM when you accept the performance tradeoff

UTM is not a substitute for Linux itself. Kernel Omega still needs a Linux guest to access the Linux-only facilities that matter here: `io_uring`, KVM, Firecracker, FICLONE, and eBPF.

## Recommended mode by host type

### Intel Mac

This is the simplest UTM path.

1. Install UTM from the official GitHub project.
2. Create an Ubuntu 22.04+ or Debian 12 `x86_64` VM.
3. Enable virtualization features in UTM.
4. Boot the VM and clone this repo inside the guest.
5. Run:
   - `./scripts/bootstrap.sh`
   - `./scripts/build_golden_snapshot.sh`
6. Start the Linux executor and Python daemons inside the guest.

Because the guest is `x86_64`, the existing scripts and Firecracker artifacts align with the current repo defaults.

### Apple Silicon Mac

This path is possible, but it is not the repo's easiest path today.

Recommended order:

1. Start with the `split-brain-mac` profile.
2. Use a remote Linux `x86_64` executor if you want production-faithful behavior.
3. Use UTM locally only when you specifically need a local Linux VM.

Why this is harder:

- an Apple Silicon Linux guest is usually `aarch64`
- Firecracker, the guest agent target, and guest kernel assets all need to match that architecture
- the repo now auto-selects those assets, but the full local ARM64 path is still less exercised than split-brain deployment

Current repo status on Apple Silicon UTM:

- supported well: macOS control plane + remote Linux executor
- supported for logic testing: macOS simulator executor
- bootstrap scripts now select ARM64 guest assets automatically inside an `aarch64` Linux guest
- still not verified in this repo on a real Apple Silicon UTM guest end to end

## Nested virtualization note

UTM's current release notes state that nested virtualization for Linux VMs using the Apple Virtualization backend is enabled by default on macOS 15 for M3 and newer. That improves the odds of getting the local VM path working on newer Apple Silicon machines.

It does not change these repo requirements:

- the Linux guest still needs Firecracker-compatible assets for its own architecture
- the guest agent must be compiled for the guest architecture
- snapshot creation still happens inside Linux

## ARM64 guest support in this repo

Inside an `aarch64` Linux guest, [bootstrap.sh](/Users/dawsonblock/SANDBOX/THEAUTOCODER/scripts/bootstrap.sh) now auto-selects:

- Rust target: `aarch64-unknown-linux-musl`
- Alpine minirootfs: `aarch64`
- Firecracker archive: `firecracker-v1.10.1-aarch64.tgz`
- guest kernel URL: the Firecracker `aarch64` quickstart kernel path

[build_golden_snapshot.sh](/Users/dawsonblock/SANDBOX/THEAUTOCODER/scripts/build_golden_snapshot.sh) now accepts both `x86_64` and `aarch64`.

Useful overrides if you need to pin or replace assets:

- `OMEGA_GUEST_TARGET`
- `OMEGA_FIRECRACKER_VERSION`
- `OMEGA_FIRECRACKER_TGZ_URL`
- `OMEGA_KERNEL_URL`
- `OMEGA_ALPINE_SERIES`
- `OMEGA_ALPINE_VERSION`
- `OMEGA_ALPINE_URL`

## Remaining caveat

This is now automated enough to attempt the full local ARM64 path inside UTM, but it is still a lower-confidence path than split-brain deployment because this repo has not yet executed the full Firecracker acceptance flow on a real Apple Silicon UTM guest.
