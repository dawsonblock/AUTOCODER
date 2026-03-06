#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "bootstrap.sh is Linux-only."
  exit 1
fi

HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
  x86_64)
    FC_ARCH="x86_64"
    GUEST_TARGET_DEFAULT="x86_64-unknown-linux-musl"
    ;;
  aarch64|arm64)
    FC_ARCH="aarch64"
    GUEST_TARGET_DEFAULT="aarch64-unknown-linux-musl"
    ;;
  *)
    echo "bootstrap.sh supports Linux x86_64 and aarch64 only."
    exit 1
    ;;
esac

if ! grep -Eq 'Ubuntu|Debian' /etc/os-release; then
  echo "Kernel Omega expects Ubuntu 22.04+ or Debian 12."
  exit 1
fi

if ! command -v firecracker >/dev/null 2>&1; then
  echo "Firecracker not found; it will be installed into /usr/local/bin."
fi

if ! command -v rustup >/dev/null 2>&1; then
  echo "rustup is required."
  exit 1
fi

FC_VERSION="${OMEGA_FIRECRACKER_VERSION:-v1.10.1}"
ALPINE_SERIES="${OMEGA_ALPINE_SERIES:-v3.18}"
ALPINE_VERSION="${OMEGA_ALPINE_VERSION:-3.18.4}"
GUEST_TARGET="${OMEGA_GUEST_TARGET:-$GUEST_TARGET_DEFAULT}"
ALPINE_URL="${OMEGA_ALPINE_URL:-https://dl-cdn.alpinelinux.org/alpine/${ALPINE_SERIES}/releases/${FC_ARCH}/alpine-minirootfs-${ALPINE_VERSION}-${FC_ARCH}.tar.gz}"
KERNEL_URL="${OMEGA_KERNEL_URL:-https://s3.amazonaws.com/spec.ccfc.min/img/quickstart_guide/${FC_ARCH}/kernels/vmlinux.bin}"
FIRECRACKER_TGZ_URL="${OMEGA_FIRECRACKER_TGZ_URL:-https://github.com/firecracker-microvm/firecracker/releases/download/${FC_VERSION}/firecracker-${FC_VERSION}-${FC_ARCH}.tgz}"
RELEASE_DIR="release-${FC_VERSION}-${FC_ARCH}"
FIRECRACKER_BIN="firecracker-${FC_VERSION}-${FC_ARCH}"

echo ">>> BOOTSTRAPPING KERNEL OMEGA V19 <<<"
echo "[*] Host architecture: ${HOST_ARCH} -> Firecracker arch: ${FC_ARCH}"
echo "[*] Guest Rust target: ${GUEST_TARGET}"
sudo prlimit --memlock=unlimited --pid $$
mkdir -p /tmp/omega/assets /tmp/omega/vms /tmp/omega/mnt

if ! df -T /tmp/omega 2>/dev/null | grep -q -e "btrfs" -e "xfs"; then
  echo "[*] Creating Btrfs loopback for FICLONE..."
  dd if=/dev/zero of=/tmp/omega_storage.img bs=1M count=4096 status=none
  mkfs.btrfs -q /tmp/omega_storage.img
  sudo mount -o loop,compress=zstd /tmp/omega_storage.img /tmp/omega
  sudo chown -R "$USER":"$USER" /tmp/omega
fi

echo "[*] Building guest agent..."
(cd guest_agent && rustup target add "$GUEST_TARGET" && cargo build --release --target "$GUEST_TARGET")

echo "[*] Creating ext4 rootfs..."
dd if=/dev/zero of=/tmp/omega/assets/rootfs.ext4 bs=1M count=150 status=none
mkfs.ext4 -q /tmp/omega/assets/rootfs.ext4
wget -q "$ALPINE_URL" -O /tmp/alpine.tar.gz
sudo mount /tmp/omega/assets/rootfs.ext4 /tmp/omega/mnt
sudo tar -xf /tmp/alpine.tar.gz -C /tmp/omega/mnt
sudo cp /etc/resolv.conf /tmp/omega/mnt/etc/resolv.conf
sudo chroot /tmp/omega/mnt /sbin/apk update
sudo chroot /tmp/omega/mnt /sbin/apk add --no-cache python3 py3-pip py3-pytest py3-mypy ruff
sudo cp "guest_agent/target/${GUEST_TARGET}/release/guest_agent" /tmp/omega/mnt/sbin/init
sudo cp guest_agent/funnel.py /tmp/omega/mnt/sbin/funnel.py
sudo chmod +x /tmp/omega/mnt/sbin/init /tmp/omega/mnt/sbin/funnel.py
sudo umount /tmp/omega/mnt

echo "[*] Installing Firecracker guest kernel and binary..."
wget -q "$KERNEL_URL" -O /tmp/omega/assets/vmlinux.bin
wget -q "$FIRECRACKER_TGZ_URL" -O /tmp/firecracker.tgz
tar -xzf /tmp/firecracker.tgz
sudo mv "${RELEASE_DIR}/${FIRECRACKER_BIN}" /usr/local/bin/firecracker
sudo chmod +x /usr/local/bin/firecracker
rm -rf "$RELEASE_DIR" /tmp/firecracker.tgz /tmp/alpine.tar.gz

echo ">>> BOOTSTRAP COMPLETE <<<"
