#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "build_golden_snapshot.sh is Linux-only."
  exit 1
fi

case "$(uname -m)" in
  x86_64|aarch64|arm64)
    ;;
  *)
    echo "build_golden_snapshot.sh supports Linux x86_64 and aarch64 only."
    exit 1
    ;;
esac

if ! command -v firecracker >/dev/null 2>&1; then
  echo "Firecracker must be installed before creating a snapshot."
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required to drive the Firecracker API."
  exit 1
fi

ASSET_DIR="/tmp/omega/assets"
TEMPLATE_DIR="${ASSET_DIR}/golden-template"

for asset in "${ASSET_DIR}/vmlinux.bin" "${ASSET_DIR}/rootfs.ext4"; do
  if [[ ! -f "$asset" ]]; then
    echo "Missing required asset: $asset"
    exit 1
  fi
done

mkdir -p "$ASSET_DIR" "$TEMPLATE_DIR"
cp "${ASSET_DIR}/rootfs.ext4" "${TEMPLATE_DIR}/rootfs.ext4"
rm -f /tmp/fc_golden.sock "${TEMPLATE_DIR}/vsock.sock" "${TEMPLATE_DIR}/vsock.sock_"*

echo "[*] Booting Firecracker template for snapshot creation..."
pushd "$TEMPLATE_DIR" >/dev/null
firecracker --api-sock /tmp/fc_golden.sock &
FC_PID=$!
cleanup() {
  kill "${FC_PID}" >/dev/null 2>&1 || true
  wait "${FC_PID}" >/dev/null 2>&1 || true
  rm -f /tmp/fc_golden.sock
  rm -f "${TEMPLATE_DIR}/vsock.sock" "${TEMPLATE_DIR}/vsock.sock_"*
  popd >/dev/null || true
}
trap cleanup EXIT

ready=0
for _ in $(seq 1 100); do
  if curl -sS --unix-socket /tmp/fc_golden.sock http://localhost/ >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.1
done
if [[ "$ready" -ne 1 ]]; then
  echo "Firecracker API socket did not become ready." >&2
  exit 1
fi

curl -sS -X PUT --unix-socket /tmp/fc_golden.sock http://localhost/machine-config \
  -d '{"vcpu_count":1,"mem_size_mib":256}' >/dev/null
curl -sS -X PUT --unix-socket /tmp/fc_golden.sock http://localhost/boot-source \
  -d '{"kernel_image_path":"/tmp/omega/assets/vmlinux.bin","boot_args":"console=ttyS0 reboot=k panic=1 pci=off init=/sbin/init"}' >/dev/null
curl -sS -X PUT --unix-socket /tmp/fc_golden.sock http://localhost/drives/rootfs \
  -d '{"drive_id":"rootfs","path_on_host":"rootfs.ext4","is_root_device":true,"is_read_only":false}' >/dev/null
curl -sS -X PUT --unix-socket /tmp/fc_golden.sock http://localhost/vsock \
  -d '{"vsock_id":"vsock0","guest_cid":3,"uds_path":"vsock.sock"}' >/dev/null
curl -sS -X PUT --unix-socket /tmp/fc_golden.sock http://localhost/actions \
  -d '{"action_type":"InstanceStart"}' >/dev/null

sleep 2
echo "[*] Capturing snapshot..."
curl -sS -X PATCH --unix-socket /tmp/fc_golden.sock http://localhost/vm \
  -d '{"state":"Paused"}' >/dev/null
curl -sS -X PUT --unix-socket /tmp/fc_golden.sock http://localhost/snapshot/create \
  -d '{"snapshot_type":"Full","snapshot_path":"/tmp/omega/assets/vm.snap","mem_file_path":"/tmp/omega/assets/vm.mem"}' >/dev/null

echo "[*] Snapshot written to /tmp/omega/assets/vm.snap and /tmp/omega/assets/vm.mem"
