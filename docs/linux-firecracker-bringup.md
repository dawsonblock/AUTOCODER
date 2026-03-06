# Linux Firecracker Bring-Up

Use this runbook before the first real `linux-prod` execution.

It is intentionally strict:
- fail fast on host mismatches
- verify each artifact before moving on
- only trust the next step if the expected log signature appears

## 1. Host Requirements

Supported host:
- Ubuntu 22.04+ or Debian 12
- `x86_64` or `aarch64`
- KVM available at `/dev/kvm`
- Firecracker-compatible Linux kernel

Quick preflight:

```bash
uname -s
uname -m
grep -E 'Ubuntu|Debian' /etc/os-release
ls -l /dev/kvm
test -r /dev/kvm && test -w /dev/kvm && echo "kvm access: ok"
```

Expected:
- `uname -s` -> `Linux`
- `uname -m` -> `x86_64`, `aarch64`, or `arm64`
- `/dev/kvm` exists
- `kvm access: ok`

Stop if:
- `/dev/kvm` is missing
- `/dev/kvm` is not writable by the current user
- host is not Ubuntu or Debian

## 2. Bootstrap The Guest Assets

Run:

```bash
./scripts/bootstrap.sh
```

Expected output signatures:
- `>>> BOOTSTRAPPING KERNEL OMEGA V19 <<<`
- `[*] Host architecture:`
- `[*] Guest Rust target:`
- `[*] Creating ext4 rootfs...`
- `[*] Installing Firecracker guest kernel and binary...`
- `>>> BOOTSTRAP COMPLETE <<<`

Expected artifacts after success:

```bash
ls -lh /tmp/omega/assets/rootfs.ext4
ls -lh /tmp/omega/assets/vmlinux.bin
command -v firecracker
```

You should see:
- `/tmp/omega/assets/rootfs.ext4`
- `/tmp/omega/assets/vmlinux.bin`
- `/usr/local/bin/firecracker` or another valid `firecracker` path

Common failures:

| Failure | Meaning | Fix |
|---|---|---|
| `/dev/kvm is missing` | No KVM available | Enable virtualization or use a real Linux host/UTM guest with KVM access |
| `Current user does not have read/write access to /dev/kvm` | Permissions issue | Add the user to the correct KVM group or adjust host permissions |
| `Missing required command:` | Host tool missing | Install the named package before retrying |
| `rustup is required.` | Rust toolchain missing | Install Rust with `rustup` |
| mount/chroot/apk failure | guest rootfs setup failed | check `sudo`, disk space, outbound network, and `/tmp/omega/mnt` mount state |

Cleanup check if bootstrap fails midway:

```bash
mountpoint /tmp/omega/mnt || true
```

Expected after a clean exit:
- `mountpoint: /tmp/omega/mnt is not a mountpoint`

## 3. Build The Golden Snapshot

Run:

```bash
./scripts/build_golden_snapshot.sh
```

Expected output signatures:
- `[*] Booting Firecracker template for snapshot creation...`
- `[*] Capturing snapshot...`
- `[*] Snapshot written to /tmp/omega/assets/vm.snap and /tmp/omega/assets/vm.mem`

Expected artifacts after success:

```bash
ls -lh /tmp/omega/assets/golden-template/rootfs.ext4
ls -lh /tmp/omega/assets/vm.snap
ls -lh /tmp/omega/assets/vm.mem
```

You should see:
- `/tmp/omega/assets/golden-template/rootfs.ext4`
- `/tmp/omega/assets/vm.snap`
- `/tmp/omega/assets/vm.mem`

Important detail:
- the snapshot is now created from `/tmp/omega/assets/golden-template/`
- that template includes the rootfs and vsock topology expected by restore
- per-run VMs clone from that template rootfs into `/tmp/omega/vms/<vm_id>/rootfs.ext4`

Common failures:

| Failure | Meaning | Fix |
|---|---|---|
| `Firecracker API socket did not become ready.` | Firecracker failed to start or bind | run `firecracker --version`, check KVM, check host kernel support |
| `Missing required asset:` | bootstrap did not finish | re-run `./scripts/bootstrap.sh` |
| `curl is required to drive the Firecracker API.` | host missing curl | install `curl` |
| Firecracker returns non-204 API responses | bad machine config, bad boot source, or unsupported host | inspect host logs and retry after correcting the failing step |

If this step fails, inspect:

```bash
ps -ef | grep firecracker | grep -v grep
ls -la /tmp/omega/assets/golden-template
```

## 4. Start The Linux Executor

Run:

```bash
./scripts/start_linux_executor.sh
```

Or with an env file:

```bash
./scripts/start_linux_executor.sh --env-file config/env/linux-executor.example.env
```

Expected output signatures:
- `>>> KERNEL OMEGA V19 DATA PLANE [PubKey: ...] <<<`
- `[core 0] online ->`
- more `[core N] online ->` lines up to the configured core count

Expected side effects:

```bash
test -f "$HOME/.config/kernel-omega/linux-executor.pub" && echo "pubkey file: ok"
```

The pubkey file should contain the same hex key printed in the executor banner.

Common failures:

| Failure | Meaning | Fix |
|---|---|---|
| `Missing required asset:` | snapshot/template artifacts are missing | run bootstrap and snapshot steps again |
| `Failed to bind executor port` | port conflict | change `OMEGA_EXECUTOR_BASE_PORT` |
| Firecracker process starts then exits immediately | restore or VM launch failed | inspect the executor log and verify snapshot/template assets |

Default log location:

```bash
tail -n 100 tmp/linux-executor/omega_hypervisor.log
```

## 5. Validate The Executor Is Reachable

On the Linux host:

```bash
ss -ltn | grep 18000
```

Expected:
- one listener per executor core beginning at `OMEGA_EXECUTOR_BASE_PORT`

In split-brain mode, from the Mac control-plane host:

```bash
nc -vz <linux-host-or-tailscale-ip> 18000
```

Expected:
- successful TCP connection

## 6. Control-Plane Wiring

On the host running the control plane, set:

```bash
export OMEGA_TRUSTED_PUBKEY="$(cat "$HOME/.config/kernel-omega/linux-executor.pub")"
```

Or point to the file-based variant if you use it:

```bash
export OMEGA_TRUSTED_PUBKEY_FILE="$HOME/.config/kernel-omega/linux-executor.pub"
```

The policy gate should trust only the exact key printed by the running executor.

## 7. Expected Runtime Flow

When the full pipeline is healthy, you should observe this sequence:

1. CLI seeds a task.
2. Worker dispatches a capsule.
3. Linux executor prints its core listeners.
4. Hypervisor clones `/tmp/omega/assets/golden-template/rootfs.ext4` into `/tmp/omega/vms/<vm_id>/rootfs.ext4`.
5. Firecracker restores from `/tmp/omega/assets/vm.snap` and `/tmp/omega/assets/vm.mem`.
6. Guest agent receives a framed JSON payload over vsock.
7. Funnel runs lint, type check, targeted tests, then full suite.
8. Hypervisor signs a `VerificationPack` and publishes it to Redis.

Healthy downstream indicators:
- result appears in `omega:stream:results`
- policy gate does not reject on `invalid_attestation`
- policy gate does not reject on `inputs_digest_mismatch`
- accepted repair reaches merger

## 8. Fast Failure Triage

Use these checks in order:

```bash
redis-cli XLEN omega:stream:results
redis-cli XRANGE omega:stream:results - + COUNT 5
redis-cli XRANGE omega:stream:accepted - + COUNT 5
tail -n 200 tmp/linux-executor/omega_hypervisor.log
```

Interpretation:

| Symptom | Likely boundary | Meaning |
|---|---|---|
| no executor banner | startup | hypervisor did not start |
| executor banner but no `core` listeners | bind/setup | executor failed before listener loop |
| listeners present but no results stream activity | dispatch/network | control plane is not reaching the Linux executor |
| results exist but no accepted records | policy | attestation, digest, or policy rejected the run |
| accepted exists but no repair branch | merger | git or repo-relative write failure |

## 9. Known Residual Risk

This repo is now aligned for Linux/Firecracker bring-up, but the final proof is still one real run on a Linux/KVM host.

Until that first successful run:
- treat the Linux path as build-verified and flow-corrected
- do not treat it as runtime-proven
- keep the macOS simulator path as the fallback for control-plane debugging
