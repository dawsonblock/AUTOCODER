#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("guest_agent is Linux-only and meant to run inside the Firecracker guest.");
}

#[cfg(target_os = "linux")]
mod linux_impl {
    use serde::{Deserialize, Serialize};
    use serde_json::Value;
    use std::collections::BTreeMap;
    use std::io::{Read, Write};
    use std::path::{Component, Path, PathBuf};
    use std::process::{Command, Output};
    use vsock::{VsockAddr, VsockListener, VMADDR_CID_ANY};

    #[derive(Deserialize)]
    struct Payload {
        code: String,
        test_code: String,
        #[serde(default)]
        source_relpath: String,
        #[serde(default)]
        test_relpath: String,
        #[serde(default)]
        repo_snapshot: BTreeMap<String, String>,
    }

    #[derive(Serialize)]
    struct FailureResult {
        success: bool,
        tests_passed: u32,
        tests_failed: u32,
        error: Option<String>,
        runtime: f64,
        meta: Value,
    }

    fn read_framed(reader: &mut impl Read, max_len: usize) -> std::io::Result<Vec<u8>> {
        let mut header = [0_u8; 4];
        reader.read_exact(&mut header)?;
        let payload_len = u32::from_be_bytes(header) as usize;
        if payload_len > max_len {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("framed payload exceeds limit: {payload_len}"),
            ));
        }
        let mut payload = vec![0_u8; payload_len];
        reader.read_exact(&mut payload)?;
        Ok(payload)
    }

    fn write_framed(writer: &mut impl Write, payload: &[u8]) -> std::io::Result<()> {
        let payload_len = u32::try_from(payload.len()).map_err(|_| {
            std::io::Error::new(std::io::ErrorKind::InvalidInput, "payload too large")
        })?;
        writer.write_all(&payload_len.to_be_bytes())?;
        writer.write_all(payload)
    }

    fn safe_join(root: &Path, relative: &str) -> std::io::Result<PathBuf> {
        let relative_path = Path::new(relative);
        if relative_path.is_absolute()
            || relative_path.components().any(|component| {
                matches!(
                    component,
                    Component::ParentDir | Component::RootDir | Component::Prefix(_)
                )
            })
        {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                format!("unsafe relative path: {relative}"),
            ));
        }
        Ok(root.join(relative_path))
    }

    fn materialize_snapshot(
        root: &Path,
        snapshot: &BTreeMap<String, String>,
    ) -> std::io::Result<()> {
        for (relative_path, content) in snapshot {
            let target = safe_join(root, relative_path)?;
            if let Some(parent) = target.parent() {
                std::fs::create_dir_all(parent)?;
            }
            std::fs::write(target, content)?;
        }
        Ok(())
    }

    fn execute_payload(payload: &Payload) -> std::io::Result<Output> {
        let repo_root = Path::new("/tmp/repo");
        let _ = std::fs::remove_dir_all(repo_root);
        std::fs::create_dir_all(repo_root)?;
        materialize_snapshot(repo_root, &payload.repo_snapshot)?;

        let source_path = safe_join(repo_root, &payload.source_relpath)?;
        let test_path = safe_join(repo_root, &payload.test_relpath)?;
        if let Some(parent) = source_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        if let Some(parent) = test_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(&source_path, &payload.code)?;
        std::fs::write(&test_path, &payload.test_code)?;

        Command::new("python3")
            .arg("/sbin/funnel.py")
            .arg(source_path)
            .arg(test_path)
            .arg("--cwd")
            .arg(repo_root)
            .output()
    }

    pub fn run() {
        let listener = VsockListener::bind(&VsockAddr::new(VMADDR_CID_ANY, 52))
            .expect("Failed to bind VSOCK listener");

        for stream in listener.incoming() {
            if let Ok(mut socket) = stream {
                if let Ok(buffer) = read_framed(&mut socket, 4 * 1024 * 1024) {
                    if let Ok(payload) = serde_json::from_slice::<Payload>(&buffer) {
                        match execute_payload(&payload) {
                            Ok(proc) => {
                                let _ = write_framed(&mut socket, &proc.stdout);
                            }
                            Err(error) => {
                                let failure = FailureResult {
                                    success: false,
                                    tests_passed: 0,
                                    tests_failed: 1,
                                    error: Some(error.to_string()),
                                    runtime: 0.0,
                                    meta: Value::Object(Default::default()),
                                };
                                if let Ok(serialized) = serde_json::to_vec(&failure) {
                                    let _ = write_framed(&mut socket, &serialized);
                                }
                            }
                        }
                    }
                }
            }
            break;
        }
        unsafe { libc::reboot(libc::LINUX_REBOOT_CMD_POWER_OFF) };
    }
}

#[cfg(target_os = "linux")]
fn main() {
    linux_impl::run();
}
