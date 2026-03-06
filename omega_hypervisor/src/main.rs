#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("omega_hypervisor is Linux-only. Use control_plane.simulator_executor on macOS.");
}

#[cfg(target_os = "linux")]
mod linux_impl {
    use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
    use base64::Engine;
    use ed25519_dalek::{Signature, Signer, SigningKey};
    use futures_lite::{AsyncReadExt, AsyncWriteExt, StreamExt};
    use glommio::{
        executor,
        net::{TcpListener, UnixStream},
        sync::Semaphore,
        timer::sleep,
        LocalExecutorBuilder, Placement,
    };
    use rand_core::OsRng;
    use serde::{Deserialize, Serialize};
    use sha2::{Digest, Sha256};
    use std::collections::BTreeMap;
    use std::io::Write;
    use std::os::unix::io::AsRawFd;
    use std::process::{Child, Command};
    use std::rc::Rc;
    use std::sync::Arc;
    use std::time::{Duration, Instant};

    const SNAPSHOT_PATH: &str = "/tmp/omega/assets/vm.snap";
    const MEM_PATH: &str = "/tmp/omega/assets/vm.mem";
    const TEMPLATE_ROOTFS: &str = "/tmp/omega/assets/golden-template/rootfs.ext4";
    const VM_WORKSPACE: &str = "/tmp/omega/vms";
    const ARTIFACT_WORKSPACE: &str = "/tmp/omega/artifacts";
    const FICLONE: libc::c_ulong = 0x40049409;
    const MAX_CONCURRENT_VMS_PER_CORE: usize = 32;
    const STREAM_RESULTS: &str = "omega:stream:results";
    const STATS_INFLIGHT: &str = "omega:stats:inflight";

    #[derive(Serialize, Deserialize, Debug)]
    struct TaskCapsule {
        id: String,
        tree_id: String,
        source_path: String,
        test_path: String,
        code: String,
        test_code: String,
        #[serde(default)]
        run_id: String,
        #[serde(default)]
        source_relpath: String,
        #[serde(default)]
        test_relpath: String,
        #[serde(default)]
        repo_snapshot: BTreeMap<String, String>,
        #[serde(default)]
        repo_snapshot_digest: String,
        #[serde(default)]
        repo_snapshot_bytes: usize,
        #[serde(default)]
        repo_snapshot_files: usize,
    }

    #[derive(Serialize, Deserialize)]
    struct GuestResponse {
        success: bool,
        tests_passed: u32,
        tests_failed: u32,
        error: Option<String>,
        runtime: f64,
        #[serde(default)]
        meta: serde_json::Value,
    }

    #[derive(Serialize)]
    struct VerificationPack {
        capsule_id: String,
        run_id: String,
        success: bool,
        runtime: f64,
        node_id: String,
        hardware_isolated: bool,
        inputs_digest: String,
        tests_passed: u32,
        tests_failed: u32,
        error_signature: Option<String>,
        attestation: Option<String>,
        artifact_uri: Option<String>,
        meta: serde_json::Value,
    }

    async fn instant_vm_clone(src_path: &str, dest_path: &str) -> std::io::Result<()> {
        let src_path = src_path.to_string();
        let dest_path = dest_path.to_string();
        executor()
            .spawn_blocking(move || {
                let src_file = std::fs::File::open(&src_path)?;
                let dst_file = std::fs::OpenOptions::new()
                    .write(true)
                    .create(true)
                    .truncate(true)
                    .open(&dest_path)?;
                let cloned =
                    unsafe { libc::ioctl(dst_file.as_raw_fd(), FICLONE, src_file.as_raw_fd()) };
                if cloned == 0 {
                    return Ok(());
                }
                drop(dst_file);
                std::fs::copy(&src_path, &dest_path)?;
                Ok(())
            })
            .await
    }

    async fn send_json(
        stream: &mut UnixStream,
        method: &str,
        path: &str,
        body: &str,
    ) -> std::io::Result<()> {
        let request = format!(
            "{} {} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
            method,
            path,
            body.len(),
            body
        );
        stream.write_all(request.as_bytes()).await?;
        let mut buffer = vec![0_u8; 1024];
        let bytes = stream.read(&mut buffer).await?;
        let response = std::str::from_utf8(&buffer[..bytes]).unwrap_or("");
        if !response.contains("204 No Content") {
            return Err(std::io::Error::new(
                std::io::ErrorKind::Other,
                "Firecracker API error",
            ));
        }
        Ok(())
    }

    async fn write_framed(stream: &mut UnixStream, payload: &[u8]) -> std::io::Result<()> {
        let length = u32::try_from(payload.len()).map_err(|_| {
            std::io::Error::new(std::io::ErrorKind::InvalidInput, "payload too large")
        })?;
        stream.write_all(&length.to_be_bytes()).await?;
        stream.write_all(payload).await
    }

    async fn read_framed(stream: &mut UnixStream, max_len: usize) -> std::io::Result<Vec<u8>> {
        let mut header = [0_u8; 4];
        stream.read_exact(&mut header).await?;
        let payload_len = u32::from_be_bytes(header) as usize;
        if payload_len > max_len {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("framed payload exceeds limit: {payload_len}"),
            ));
        }
        let mut payload = vec![0_u8; payload_len];
        stream.read_exact(&mut payload).await?;
        Ok(payload)
    }

    async fn restore_golden_vm(fc_socket: &str, vm_dir: &str) -> std::io::Result<Child> {
        let sock_clone = fc_socket.to_string();
        let vm_dir = vm_dir.to_string();
        let child = executor()
            .spawn_blocking(move || {
                Command::new("firecracker")
                    .current_dir(&vm_dir)
                    .args(["--api-sock", &sock_clone])
                    .spawn()
                    .unwrap()
            })
            .await;

        let deadline = Instant::now() + Duration::from_secs(3);
        let mut stream = loop {
            if Instant::now() > deadline {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::TimedOut,
                    "Firecracker socket timeout",
                ));
            }
            if let Ok(sock) = UnixStream::connect(fc_socket).await {
                break sock;
            }
            sleep(Duration::from_millis(2)).await;
        };

        let snapshot = serde_json::json!({
            "snapshot_path": SNAPSHOT_PATH,
            "mem_file_path": MEM_PATH,
            "enable_diff_snapshots": false,
            "resume_vm": false
        })
        .to_string();
        send_json(&mut stream, "PUT", "/snapshot/load", &snapshot).await?;
        send_json(&mut stream, "PATCH", "/vm", r#"{"state":"Resumed"}"#).await?;
        Ok(child)
    }

    fn sign_pack(pack: &mut VerificationPack, signing_key: &SigningKey) {
        let mut core = BTreeMap::new();
        core.insert(
            "capsule_id",
            serde_json::Value::String(pack.capsule_id.clone()),
        );
        core.insert(
            "error_signature",
            match &pack.error_signature {
                Some(value) => serde_json::Value::String(value.clone()),
                None => serde_json::Value::Null,
            },
        );
        core.insert(
            "hardware_isolated",
            serde_json::Value::Bool(pack.hardware_isolated),
        );
        core.insert(
            "inputs_digest",
            serde_json::Value::String(pack.inputs_digest.clone()),
        );
        core.insert("run_id", serde_json::Value::String(pack.run_id.clone()));
        core.insert("success", serde_json::Value::Bool(pack.success));
        core.insert(
            "tests_failed",
            serde_json::Value::Number(serde_json::Number::from(pack.tests_failed)),
        );
        core.insert(
            "tests_passed",
            serde_json::Value::Number(serde_json::Number::from(pack.tests_passed)),
        );
        let mut encoded = Vec::new();
        let mut serializer =
            serde_json::Serializer::with_formatter(&mut encoded, serde_json::ser::CompactFormatter);
        core.serialize(&mut serializer).unwrap();
        let digest = Sha256::digest(&encoded);
        let signature: Signature = signing_key.sign(&digest);
        pack.attestation = Some(BASE64_STANDARD.encode(signature.to_bytes()));
    }

    fn persist_artifact(
        capsule_id: &str,
        capsule: &TaskCapsule,
        guest_response: &GuestResponse,
    ) -> Option<String> {
        let path = std::path::Path::new(ARTIFACT_WORKSPACE)
            .join(format!("{}.json", capsule_id.replace(':', "_")));
        std::fs::create_dir_all(ARTIFACT_WORKSPACE).ok()?;
        let payload = serde_json::json!({
            "capsule": capsule,
            "run_id": capsule.run_id,
            "guest_response": guest_response,
        });
        let mut file = std::fs::File::create(&path).ok()?;
        write!(file, "{}", serde_json::to_string_pretty(&payload).ok()?).ok()?;
        Some(path.to_string_lossy().to_string())
    }

    async fn publish_result(redis_url: String, pack: &VerificationPack) {
        let serialized = match serde_json::to_string(pack) {
            Ok(value) => value,
            Err(_) => return,
        };
        let _ = executor()
            .spawn_blocking(move || {
                let client = redis::Client::open(redis_url.as_str()).ok()?;
                let mut connection = client.get_connection().ok()?;
                let _: () = redis::cmd("XADD")
                    .arg(STREAM_RESULTS)
                    .arg("*")
                    .arg("payload")
                    .arg(serialized)
                    .query(&mut connection)
                    .ok()?;
                let _: () = redis::cmd("DECR")
                    .arg(STATS_INFLIGHT)
                    .query(&mut connection)
                    .ok()?;
                Some(())
            })
            .await;
    }

    async fn run_core_event_loop(
        core_id: usize,
        signing_key: Arc<SigningKey>,
        redis_url: String,
        executor_host: String,
        executor_base_port: u16,
    ) {
        let bind_addr = format!("{}:{}", executor_host, executor_base_port + core_id as u16);
        let listener = TcpListener::bind(&bind_addr).expect("Failed to bind executor port");
        let semaphore = Rc::new(Semaphore::new(MAX_CONCURRENT_VMS_PER_CORE as _));
        println!("[core {core_id}] online -> {bind_addr}");
        let mut incoming = listener.incoming();

        while let Some(stream_result) = incoming.next().await {
            let mut tcp_stream = match stream_result {
                Ok(stream) => stream,
                Err(_) => continue,
            };
            let permit = semaphore.acquire_static_permit(1).await.unwrap();
            let key = Arc::clone(&signing_key);
            let redis_for_task = redis_url.clone();

            glommio::spawn_local(async move {
                let _permit = permit;
                let mut buffer = Vec::new();
                if tcp_stream.read_to_end(&mut buffer).await.is_err() {
                    return;
                }
                let capsule: TaskCapsule = match serde_json::from_slice(&buffer) {
                    Ok(value) => value,
                    Err(_) => return,
                };

                let started = Instant::now();
                let vm_id = format!("vm_{}_{}", core_id, capsule.id.replace(':', "_"));
                let vm_dir = format!("{}/{}", VM_WORKSPACE, vm_id);
                let rootfs = format!("{}/rootfs.ext4", vm_dir);
                let fc_socket = format!("{}/firecracker.sock", vm_dir);
                let vsock = format!("{}/vsock.sock", vm_dir);
                let mut guest_response = GuestResponse {
                    success: false,
                    tests_passed: 0,
                    tests_failed: 1,
                    error: Some("VMFailure".to_string()),
                    runtime: 0.0,
                    meta: serde_json::Value::Object(Default::default()),
                };
                let mut firecracker: Option<Child> = None;
                let mut clone_ms = 0.0;
                let mut restore_ms = 0.0;
                let mut vsock_roundtrip_ms = 0.0;

                let _ = executor()
                    .spawn_blocking({
                        let vm_dir = vm_dir.clone();
                        move || {
                            let _ = std::fs::remove_dir_all(&vm_dir);
                            std::fs::create_dir_all(&vm_dir)
                        }
                    })
                    .await;

                let clone_started = Instant::now();
                if instant_vm_clone(TEMPLATE_ROOTFS, &rootfs).await.is_ok() {
                    clone_ms = clone_started.elapsed().as_secs_f64() * 1000.0;
                    let restore_started = Instant::now();
                    if let Ok(child) = restore_golden_vm(&fc_socket, &vm_dir).await {
                        restore_ms = restore_started.elapsed().as_secs_f64() * 1000.0;
                        firecracker = Some(child);
                        let vsock_conn = format!("{}_52", vsock);
                        let mut guest_socket = loop {
                            if Instant::now() > started + Duration::from_secs(2) {
                                break None;
                            }
                            if let Ok(mut socket) = UnixStream::connect(&vsock_conn).await {
                                let _ = socket.write_all(b"CONNECT 52\n").await;
                                let mut ack = [0_u8; 32];
                                if let Ok(bytes) = socket.read(&mut ack).await {
                                    if std::str::from_utf8(&ack[..bytes])
                                        .unwrap_or("")
                                        .starts_with("OK")
                                    {
                                        break Some(socket);
                                    }
                                }
                            }
                            sleep(Duration::from_millis(2)).await;
                        };

                        if let Some(mut socket) = guest_socket.take() {
                            let vsock_started = Instant::now();
                            let source_relpath = if capsule.source_relpath.is_empty() {
                                std::path::Path::new(&capsule.source_path)
                                    .file_name()
                                    .and_then(|value| value.to_str())
                                    .unwrap_or("algo.py")
                                    .to_string()
                            } else {
                                capsule.source_relpath.clone()
                            };
                            let test_relpath = if capsule.test_relpath.is_empty() {
                                std::path::Path::new(&capsule.test_path)
                                    .file_name()
                                    .and_then(|value| value.to_str())
                                    .unwrap_or("test_algo.py")
                                    .to_string()
                            } else {
                                capsule.test_relpath.clone()
                            };
                            let payload = serde_json::json!({
                                "code": capsule.code,
                                "test_code": capsule.test_code,
                                "source_relpath": source_relpath,
                                "test_relpath": test_relpath,
                                "repo_snapshot": capsule.repo_snapshot
                            })
                            .to_string();
                            if write_framed(&mut socket, payload.as_bytes()).await.is_ok() {
                                if let Ok(response_buffer) =
                                    read_framed(&mut socket, 1024 * 1024).await
                                {
                                    vsock_roundtrip_ms =
                                        vsock_started.elapsed().as_secs_f64() * 1000.0;
                                    if let Ok(parsed) =
                                        serde_json::from_slice::<GuestResponse>(&response_buffer)
                                    {
                                        guest_response = parsed;
                                    }
                                }
                            }
                        }
                    }
                }

                let artifact_started = Instant::now();
                let artifact_uri = persist_artifact(&capsule.id, &capsule, &guest_response);
                let artifact_write_ms = artifact_started.elapsed().as_secs_f64() * 1000.0;
                let _ = executor()
                    .spawn_blocking(move || {
                        if let Some(mut child) = firecracker {
                            let _ = child.kill();
                            let _ = child.wait();
                        }
                        let _ = std::fs::remove_dir_all(&vm_dir);
                    })
                    .await;

                let mut pack = VerificationPack {
                    capsule_id: capsule.id.clone(),
                    run_id: capsule.run_id.clone(),
                    success: guest_response.success,
                    runtime: started.elapsed().as_secs_f64(),
                    node_id: format!("core_{core_id}"),
                    hardware_isolated: true,
                    inputs_digest: hex::encode(Sha256::digest(capsule.code.as_bytes())),
                    tests_passed: guest_response.tests_passed,
                    tests_failed: guest_response.tests_failed,
                    error_signature: guest_response.error.clone(),
                    attestation: None,
                    artifact_uri,
                    meta: serde_json::json!({
                        "transport": {
                            "repo_snapshot_digest": capsule.repo_snapshot_digest,
                            "repo_snapshot_bytes": capsule.repo_snapshot_bytes,
                            "repo_snapshot_files": capsule.repo_snapshot_files,
                        },
                        "firecracker": {
                            "clone_ms": clone_ms,
                            "restore_ms": restore_ms,
                            "vsock_roundtrip_ms": vsock_roundtrip_ms,
                            "artifact_write_ms": artifact_write_ms,
                        },
                        "funnel": guest_response.meta,
                    }),
                };
                sign_pack(&mut pack, &key);
                publish_result(redis_for_task, &pack).await;
            })
            .detach();
        }
    }

    pub fn run() {
        let redis_url = std::env::var("OMEGA_REDIS_URL")
            .unwrap_or_else(|_| "redis://127.0.0.1:6379".to_string());
        let executor_host =
            std::env::var("OMEGA_EXECUTOR_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
        let executor_base_port = std::env::var("OMEGA_EXECUTOR_BASE_PORT")
            .ok()
            .and_then(|value| value.parse::<u16>().ok())
            .unwrap_or(18000);
        let requested_cores = std::env::var("OMEGA_EXECUTOR_CORES")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .unwrap_or(4);
        let num_cores = std::thread::available_parallelism()
            .map(|value| value.get())
            .unwrap_or(1)
            .min(requested_cores);
        let signing_key = SigningKey::generate(&mut OsRng);
        println!(
            ">>> KERNEL OMEGA V19 DATA PLANE [PubKey: {}] <<<",
            hex::encode(signing_key.verifying_key().to_bytes())
        );

        let _ = std::fs::create_dir_all(VM_WORKSPACE);
        let mut handles = Vec::new();
        for core_id in 0..num_cores {
            let key = Arc::new(signing_key.clone());
            let redis_for_core = redis_url.clone();
            let host_for_core = executor_host.clone();
            handles.push(std::thread::spawn(move || {
                LocalExecutorBuilder::new(Placement::Fixed(core_id))
                    .spawn(move || async move {
                        run_core_event_loop(
                            core_id,
                            key,
                            redis_for_core,
                            host_for_core,
                            executor_base_port,
                        )
                        .await
                    })
                    .unwrap()
                    .join()
                    .unwrap();
            }));
        }
        for handle in handles {
            handle.join().unwrap();
        }
    }
}

#[cfg(target_os = "linux")]
fn main() {
    linux_impl::run();
}
