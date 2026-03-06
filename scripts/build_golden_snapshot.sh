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

mkdir -p /tmp/omega/assets
rm -f /tmp/fc_golden.sock

echo "[*] Booting Firecracker template for snapshot creation..."
firecracker --api-sock /tmp/fc_golden.sock &
FC_PID=$!
cleanup() {
  kill "${FC_PID}" >/dev/null 2>&1 || true
  wait "${FC_PID}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

sleep 1
curl -sS -X PUT --unix-socket /tmp/fc_golden.sock http://localhost/machine-config \
  -d '{"vcpu_count":1,"mem_size_mib":256}' >/dev/null
curl -sS -X PUT --unix-socket /tmp/fc_golden.sock http://localhost/boot-source \
  -d '{"kernel_image_path":"/tmp/omega/assets/vmlinux.bin","boot_args":"console=ttyS0 reboot=k panic=1 pci=off init=/sbin/init"}' >/dev/null
curl -sS -X PUT --unix-socket /tmp/fc_golden.sock http://localhost/drives/rootfs \
  -d '{"drive_id":"rootfs","path_on_host":"/tmp/omega/assets/rootfs.ext4","is_root_device":true,"is_read_only":false}' >/dev/null
curl -sS -X PUT --unix-socket /tmp/fc_golden.sock http://localhost/actions \
  -d '{"action_type":"InstanceStart"}' >/dev/null

sleep 2
echo "[*] Capturing snapshot..."
curl -sS -X PUT --unix-socket /tmp/fc_golden.sock http://localhost/actions \
  -d '{"action_type":"Pause"}' >/dev/null
curl -sS -X PUT --unix-socket /tmp/fc_golden.sock http://localhost/snapshot/create \
  -d '{"snapshot_type":"Full","snapshot_path":"/tmp/omega/assets/vm.snap","mem_file_path":"/tmp/omega/assets/vm.mem"}' >/dev/null

echo "[*] Snapshot written to /tmp/omega/assets/vm.snap and /tmp/omega/assets/vm.mem"
