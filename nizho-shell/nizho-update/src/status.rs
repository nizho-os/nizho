//! Parse `bootc status --json --format-version=1` without invoking bootc.
//! Schema: https://github.com/bootc-dev/bootc/blob/main/crates/lib/src/spec.rs
//! Versions come from image metadata, never guessed from mutable image tags.
use serde::Deserialize;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Deployment {
    pub version: Option<String>,
    pub image: Option<String>,
    pub download_only: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UpdateStatus {
    pub installed: Deployment,
    pub staged: Option<Deployment>,
    pub restart_needed: bool,
    /// A previous deployment exists and the medium permits changing boot order.
    /// This does not certify that the previous deployment will boot successfully.
    pub rollback_available: bool,
    pub rollback_queued: bool,
    pub read_only: bool,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Host {
    api_version: String,
    kind: String,
    #[serde(default)]
    spec: Spec,
    status: Status,
}
#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Spec { boot_order: Option<String> }
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Status {
    booted: Option<Entry>,
    staged: Option<Entry>,
    rollback: Option<Entry>,
    rollback_queued: Option<bool>,
    #[serde(default)]
    read_only: bool,
}
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Entry {
    image: Option<Image>,
    // Required in format v1: reject malformed entries such as {}.
    #[serde(rename = "incompatible")]
    _incompatible: bool,
    #[serde(rename = "pinned")]
    _pinned: bool,
    #[serde(default)]
    download_only: bool,
}
#[derive(Debug, Deserialize)]
struct Image { image: ImageReference, version: Option<String> }
#[derive(Debug, Deserialize)]
struct ImageReference { image: String }

impl From<Entry> for Deployment {
    fn from(entry: Entry) -> Self {
        let (version, image) = match entry.image {
            Some(image) => (image.version.filter(|v| !v.trim().is_empty()), Some(image.image.image)),
            None => (None, None),
        };
        Self { version, image, download_only: entry.download_only }
    }
}

/// Invalid, unsupported or non-bootc output is an error, never "up to date".
/// Cached update metadata alone does not represent a staged deployment.
/// Where nizho-bootc-status.service publishes `bootc status` for unprivileged readers.
pub const PUBLISHED: &str = "/run/nizho/bootc-status.json";

/// Where nizho-image-ref-check.service leaves its warning when this computer follows an image
/// other than Nizho's published one. The file is absent when there is nothing to warn about.
pub const IMAGE_REF_WARNING: &str = "/run/nizho/image-ref-warning";

/// The warning to show, or None. The file is written by root and shown as plain text.
pub fn read_image_ref_warning() -> Option<String> {
    warning_text(std::fs::read_to_string(IMAGE_REF_WARNING).ok().as_deref())
}

/// Trimmed and capped; an absent, empty or blank file is no warning.
pub fn warning_text(contents: Option<&str>) -> Option<String> {
    const MAX_CHARS: usize = 600;
    let text = contents?.trim();
    (!text.is_empty()).then(|| text.chars().take(MAX_CHARS).collect())
}

/// Read the status published by nizho-bootc-status.service (no privileges needed).
pub fn read_published() -> Result<UpdateStatus, String> {
    read_published_timed().map(|(status, _)| status)
}

/// The published status together with the file time it was read with, so a caller that shows
/// "as of" a time shows the time of this very content, not of a file rewritten since.
pub fn read_published_timed() -> Result<(UpdateStatus, std::time::SystemTime), String> {
    read_published_at_timed(std::path::Path::new(PUBLISHED), std::time::SystemTime::now())
}

// The publisher runs every ten minutes. Allow scheduling jitter, not an
// indefinitely trusted snapshot after a failed publication.
const MAX_AGE: std::time::Duration = std::time::Duration::from_secs(720);
#[cfg(test)]
fn read_published_at(path: &std::path::Path, now: std::time::SystemTime) -> Result<UpdateStatus, String> {
    read_published_at_timed(path, now).map(|(status, _)| status)
}

fn read_published_at_timed(path: &std::path::Path, now: std::time::SystemTime)
    -> Result<(UpdateStatus, std::time::SystemTime), String> {
    use std::io::Read;
    let mut file = std::fs::File::open(path).map_err(|e| format!("update status unavailable ({e})"))?;
    let modified = file.metadata().and_then(|m| m.modified()).map_err(|e| e.to_string())?;
    let age = now.duration_since(modified).map_err(|_| "update status timestamp is in the future")?;
    if age >= MAX_AGE { return Err("update status is stale; the publisher has not refreshed it".into()); }
    let mut json = String::new();
    file.read_to_string(&mut json).map_err(|e| e.to_string())?;
    parse_status(&json).map(|status| (status, modified))
}

pub fn parse_status(json: &str) -> Result<UpdateStatus, String> {
    let host: Host = serde_json::from_str(json).map_err(|e| format!("Invalid bootc status: {e}"))?;
    if host.api_version != "org.containers.bootc/v1" || host.kind != "BootcHost" {
        return Err("Unsupported bootc status format".into());
    }
    let status = host.status;
    let installed = status.booted.ok_or("No booted bootc deployment")?.into();
    let rollback_queued = status.rollback_queued.unwrap_or(host.spec.boot_order.as_deref() == Some("rollback"));
    if rollback_queued && status.rollback.is_none() {
        return Err("Rollback queued without a rollback deployment".into());
    }
    let staged: Option<Deployment> = status.staged.map(Into::into);
    let restart_needed = rollback_queued || staged.as_ref().is_some_and(|s| !s.download_only);
    Ok(UpdateStatus {
        installed, staged, restart_needed, rollback_queued,
        rollback_available: status.rollback.is_some() && !status.read_only,
        read_only: status.read_only,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};
    fn entry(version: Value) -> Value {
        json!({"image": {"image": {"image": "quay.io/example/nizho:latest", "transport": "registry"},
            "version": version, "imageDigest": "sha256:123", "timestamp": null},
            "incompatible": false, "pinned": false, "ostree": {"checksum": "123"}})
    }
    fn host() -> Value {
        json!({"apiVersion": "org.containers.bootc/v1", "kind": "BootcHost",
            "spec": {"bootOrder": "default"},
            "status": {"booted": entry(json!("44.1")), "staged": null, "rollback": null}})
    }
    fn parse(value: Value) -> UpdateStatus { parse_status(&value.to_string()).unwrap() }
    #[test]
    fn image_ref_warning_is_shown_only_when_the_file_has_text() {
        assert_eq!(warning_text(None), None);
        assert_eq!(warning_text(Some("")), None);
        assert_eq!(warning_text(Some(" \n\n")), None);
        let text = "This computer is not set to receive Nizho updates.\nsudo bootc switch ghcr.io/nizho-os/nizho:latest\n";
        assert_eq!(warning_text(Some(text)).as_deref(), Some(text.trim()));
        assert_eq!(warning_text(Some(&"x".repeat(5000))).unwrap().chars().count(), 600);
    }
    #[test]
    fn published_snapshot_expires_and_rejects_future_timestamps() {
        let path = std::env::temp_dir().join(format!("nizho-status-{}-{}.json", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        std::fs::write(&path, host().to_string()).unwrap();
        let modified = std::fs::metadata(&path).unwrap().modified().unwrap();
        assert!(read_published_at(&path, modified).is_ok());
        assert!(read_published_at(&path, modified + MAX_AGE).is_err());
        assert!(read_published_at(&path, modified - std::time::Duration::from_secs(1)).is_err());
        std::fs::remove_file(&path).unwrap();
        assert!(read_published_at(&path, modified).is_err());
    }
    #[test]
    fn installed_version_is_metadata_not_tag_and_no_update_is_inferred() {
        let s = parse(host());
        assert_eq!(s.installed.version.as_deref(), Some("44.1"));
        assert!(!s.restart_needed);
        assert!(!s.rollback_available);
        assert!(s.staged.is_none());
    }
    #[test]
    fn staged_update_and_previous_deployment() {
        let mut v = host();
        v["status"]["staged"] = entry(json!("44.2"));
        v["status"]["rollback"] = entry(json!("44.0"));
        let s = parse(v);
        assert_eq!(s.staged.unwrap().version.as_deref(), Some("44.2"));
        assert!(s.restart_needed && s.rollback_available);
    }
    #[test]
    fn download_only_is_staged_but_not_ready_for_restart() {
        let mut v = host();
        v["status"]["staged"] = entry(json!("44.2"));
        v["status"]["staged"]["downloadOnly"] = json!(true);
        let s = parse(v);
        assert!(s.staged.unwrap().download_only);
        assert!(!s.restart_needed);
    }
    #[test]
    fn queued_rollback_requires_restart_without_staged_update() {
        for status_flag in [false, true] {
            let mut v = host();
            v["status"]["rollback"] = entry(json!("44.0"));
            if status_flag { v["status"]["rollbackQueued"] = json!(true); }
            else { v["spec"]["bootOrder"] = json!("rollback"); }
            let s = parse(v);
            assert!(s.rollback_queued && s.restart_needed);
        }
    }
    #[test]
    fn read_only_media_cannot_queue_rollback() {
        let mut v = host();
        v["status"]["rollback"] = entry(json!("44.0"));
        v["status"]["readOnly"] = json!(true);
        let s = parse(v);
        assert!(s.read_only);
        assert!(!s.rollback_available);
    }
    #[test]
    fn missing_version_and_cached_update_are_not_invented_status() {
        let mut v = host();
        v["status"]["booted"]["image"]["version"] = Value::Null;
        v["status"]["booted"]["cachedUpdate"] = entry(json!("99"))["image"].clone();
        let s = parse(v);
        assert!(s.installed.version.is_none());
        assert!(s.staged.is_none());
        assert!(!s.restart_needed);
    }
    #[test]
    fn malformed_unknown_or_unbooted_input_stays_unknown() {
        for bad in ["", "{}", "null", "{\"status\":{}}"] { assert!(parse_status(bad).is_err()); }
        for (path, value) in [("/apiVersion", json!("org.containers.bootc/v2")),
            ("/status/booted", Value::Null), ("/status/staged", json!({})),
            ("/status/staged", json!(false))] {
            let mut v = host();
            *v.pointer_mut(path).unwrap() = value;
            assert!(parse_status(&v.to_string()).is_err(), "{path}");
        }
        let mut v = host();
        v["status"]["rollbackQueued"] = json!(true);
        assert!(parse_status(&v.to_string()).is_err());
    }
    #[test]
    fn unknown_additional_fields_are_forward_compatible() {
        let mut v = host();
        v["status"]["futureField"] = json!({"anything": true});
        assert_eq!(parse(v), parse(host()));
    }
}
