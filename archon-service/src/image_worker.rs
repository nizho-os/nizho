//! Native Archon resolution/execution inside a disposable rootless build container.
//! This entry point is never a live-system package operation or a D-Bus authority.
use archon_distro::transaction::{
    ChangeAction, TransactionPlan, TransactionRequest, FEDORA_BRIDGE,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    io::Write,
    os::unix::fs::{MetadataExt, OpenOptionsExt},
    time::{SystemTime, UNIX_EPOCH},
};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    request: TransactionRequest,
    expected: Option<TransactionPlan>,
    expires_at: u64,
}

fn validate_plan(plan: &TransactionPlan) -> Result<(), String> {
    plan.validate()?;
    if plan.backend != "libdnf5" {
        return Err("Image worker requires Archon's libdnf5 backend".into());
    }
    for change in &plan.changes {
        let name = &change.package.name;
        if matches!(change.action, ChangeAction::Remove | ChangeAction::Replaced)
            && (matches!(
                name.as_str(),
                "bootc" | "rpm" | "glibc" | "ostree" | "sudo" | "polkit"
            ) || ["kernel", "systemd", "nizho", "archon"]
                .iter()
                .any(|prefix| name.starts_with(prefix)))
        {
            return Err(format!(
                "Removing protected image component {name} is unsupported"
            ));
        }
    }
    Ok(())
}

fn rootless_mapping(mapping: &str) -> bool {
    let rows: Option<Vec<Vec<u64>>> = mapping
        .lines()
        .map(|line| {
            line.split_whitespace()
                .map(|field| field.parse().ok())
                .collect()
        })
        .collect();
    rows.is_some_and(|rows| {
        !rows.is_empty()
            && rows
                .iter()
                .all(|row| row.len() == 3 && row[1] != 0 && row[2] > 0 && row[2] <= 1_048_576)
    })
}

fn unexpired(expires_at: u64) -> bool {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .is_ok_and(|time| time.as_secs() < expires_at && expires_at - time.as_secs() <= 300)
}

pub async fn run() -> Result<(), Box<dyn std::error::Error>> {
    if !std::path::Path::new("/run/.containerenv").is_file()
        || std::path::Path::new("/run/ostree-booted").exists()
        || std::fs::metadata("/proc/self")?.uid() != 0
        || !rootless_mapping(&std::fs::read_to_string("/proc/self/uid_map")?)
    {
        return Err(
            "Image worker requires root inside a disposable rootless build container".into(),
        );
    }
    let mut input = BufReader::new(tokio::io::stdin());
    let line = read_line(&mut input).await?;
    let request: Request = serde_json::from_slice(&line)?;
    request.request.validate()?;
    if !unexpired(request.expires_at) {
        return Err("Image plan has expired or has an invalid lifetime".into());
    }
    if let Some(expected) = &request.expected {
        validate_plan(expected)?;
        if expected.request != request.request {
            return Err("Stored plan does not match request".into());
        }
    }
    let apply = request.expected.is_some();
    let mut journal = if apply {
        let file = std::fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .mode(0o600)
            .open("/nizho-image-worker.journal")?;
        std::fs::File::open("/")?.sync_all()?;
        Some(file)
    } else {
        None
    };
    let mut child = tokio::process::Command::new("/usr/bin/python3")
        .args(["-I", "-c", FEDORA_BRIDGE])
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin")
        .env("LC_ALL", "C.UTF-8")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::inherit())
        .kill_on_drop(true)
        .spawn()?;
    let mut decisions = child.stdin.take().ok_or("Missing native input")?;
    let mut events = BufReader::new(child.stdout.take().ok_or("Missing native output")?);
    decisions
        .write_all(
            format!(
                "{}\n",
                json!({"schema_version":1,
        "mode":if apply {"apply"} else {"plan"},"request":request.request})
            )
            .as_bytes(),
        )
        .await?;
    let mut seen_plan = false;
    let mut complete = false;
    let work = async {
        loop {
            let bytes = read_line(&mut events).await?;
            let event: Value = serde_json::from_slice(&bytes)?;
            let decision = match event["event"].as_str() {
                Some("ready") if !seen_plan => Some(json!({"continue":true})),
                Some("plan") if !seen_plan => {
                    let plan: TransactionPlan = serde_json::from_value(event["plan"].clone())?;
                    validate_plan(&plan)?;
                    if plan.request != request.request || !unexpired(request.expires_at) {
                        return Err("Resolved request mismatch or expired plan".into());
                    }
                    if let Some(expected) = &request.expected {
                        if &plan != expected {
                            return Err("Native packages, policy or base state changed; review a fresh plan".into());
                        }
                    }
                    seen_plan = true;
                    apply.then(|| json!({"approve":true}))
                }
                Some("checkpoint") if apply && seen_plan => {
                    if !unexpired(request.expires_at) {
                        return Err("Plan expired before native commit".into());
                    }
                    // The native engine requires durable acknowledgement before
                    // mutating even this disposable container's RPM root.
                    let journal = journal.as_mut().ok_or("Missing native journal")?;
                    writeln!(journal, "{event}")?;
                    journal.sync_all()?;
                    Some(json!({"continue":true}))
                }
                Some("complete") if seen_plan => {
                    complete = true;
                    None
                }
                Some("error") => return Err(format!("Native Archon failure: {event}").into()),
                _ => return Err("Unexpected native transaction event".into()),
            };
            println!("{event}");
            std::io::stdout().flush()?;
            if let Some(decision) = decision {
                decisions
                    .write_all(format!("{decision}\n").as_bytes())
                    .await?;
            }
            if complete {
                break;
            }
        }
        if !child.wait().await?.success() {
            return Err("Native worker exited unsuccessfully".into());
        }
        Ok::<(), Box<dyn std::error::Error>>(())
    };
    tokio::time::timeout(std::time::Duration::from_secs(300), work).await??;
    if apply {
        std::fs::remove_file("/nizho-image-worker.journal")?;
        std::fs::File::open("/")?.sync_all()?;
    }
    Ok(())
}

async fn read_line<R: tokio::io::AsyncBufRead + Unpin>(reader: &mut R) -> std::io::Result<Vec<u8>> {
    let mut bytes = Vec::new();
    loop {
        let buffer = reader.fill_buf().await?;
        if buffer.is_empty() {
            return Err(std::io::Error::other("Truncated native protocol"));
        }
        let length = buffer
            .iter()
            .position(|byte| *byte == b'\n')
            .map_or(buffer.len(), |n| n + 1);
        if bytes.len() + length > 1024 * 1024 {
            return Err(std::io::Error::other("Native event exceeds limit"));
        }
        bytes.extend_from_slice(&buffer[..length]);
        reader.consume(length);
        if bytes.last() == Some(&b'\n') {
            return Ok(bytes);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn protects_boot_components_after_dependency_resolution() {
        let base = json!({"schema_version":1,"backend":"libdnf5",
            "request":{"operation":"remove","packages":["example"]},
            "changes":[],"download_bytes":0,"install_bytes":0,
            "system_fingerprint":"a".repeat(64),"policy_fingerprint":"b".repeat(64),"warnings":[]});
        for name in [
            "bootc",
            "systemd",
            "kernel-core",
            "nizho-release",
            "rpm",
            "glibc",
        ] {
            let mut value = base.clone();
            value["changes"] = json!([{"action":"remove",
                "package":{"name":name,"version":"1","architecture":"x86_64"},
                "reason":"dependency","repository":"@System","download_bytes":0,
                "install_bytes":0,"checksum":null,"replaces":[],"replaced_by":[]}]);
            let mut plan: TransactionPlan = serde_json::from_value(value).unwrap();
            assert!(validate_plan(&plan).is_err());
            plan.changes[0].package.name = "example".into();
            assert!(validate_plan(&plan).is_ok());
        }
    }
    #[test]
    fn refuses_initial_user_namespace_and_bad_mappings() {
        assert!(!rootless_mapping("0 0 4294967295\n"));
        assert!(!rootless_mapping("0 0 1\n1 100000 65535\n"));
        assert!(!rootless_mapping("0 1000\n"));
        assert!(!rootless_mapping(""));
        assert!(rootless_mapping("0 1000 1\n1 100000 65536\n"));
        assert!(!unexpired(0));
        assert!(!unexpired(u64::MAX));
    }
    #[tokio::test]
    async fn rejects_truncated_and_oversized_native_output() {
        assert!(read_line(&mut &b"truncated"[..]).await.is_err());
        assert!(read_line(&mut &vec![b'x'; 1024 * 1024 + 1][..])
            .await
            .is_err());
        assert_eq!(read_line(&mut &b"{}\n"[..]).await.unwrap(), b"{}\n");
    }
}
