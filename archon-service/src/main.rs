mod authorization;
mod manager;
mod status;
use archon_distro::{Backend, DnfBackend};
use std::time::Duration;
use tokio::io::AsyncReadExt;

struct Archon;
fn unavailable() -> zbus::fdo::Error {
    zbus::fdo::Error::NotSupported("Image planning and deployment require a validated builder and trust broker; live RPM mutations are disabled".into())
}

// Fixed read-only executable, bounded output and runtime. No shell or client argv.
async fn inventory() -> zbus::fdo::Result<Vec<(String, String, String)>> {
    let mut child = tokio::process::Command::new("/usr/bin/rpm")
        .args(
            DnfBackend
                .inventory_argv()
                .expect("RPM inventory supported")
                .into_iter()
                .skip(1),
        )
        .env_clear()
        .env("LC_ALL", "C")
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .map_err(|e| zbus::fdo::Error::Failed(e.to_string()))?;
    let result = tokio::time::timeout(Duration::from_secs(15), async {
        let mut bytes = Vec::new();
        child
            .stdout
            .take()
            .unwrap()
            .take(4 * 1024 * 1024 + 1)
            .read_to_end(&mut bytes)
            .await?;
        if bytes.len() > 4 * 1024 * 1024 {
            return Err(std::io::Error::other("inventory exceeds limit"));
        }
        if !child.wait().await?.success() {
            return Err(std::io::Error::other("RPM query failed"));
        }
        Ok(bytes)
    })
    .await
    .map_err(|_| zbus::fdo::Error::Failed("RPM query timed out".into()))?
    .map_err(|e: std::io::Error| zbus::fdo::Error::Failed(e.to_string()))?;
    let text = String::from_utf8(result).map_err(|e| zbus::fdo::Error::Failed(e.to_string()))?;
    parse_inventory(&text)
}
fn parse_inventory(text: &str) -> zbus::fdo::Result<Vec<(String, String, String)>> {
    DnfBackend
        .parse_inventory(text)
        .map(|rows| {
            rows.into_iter()
                .map(|p| (p.name, p.version, p.architecture))
                .collect()
        })
        .map_err(zbus::fdo::Error::Failed)
}
#[zbus::interface(name = "org.nizho.Archon1")]
impl Archon {
    fn get_capabilities(&self) -> Vec<(&str, &str)> {
        vec![
            ("list_installed", "read-only"),
            ("search_installed", "read-only"),
            ("system_status", "published-read-only"),
            ("plan_install", "unsupported"),
            ("plan_remove", "unsupported"),
            ("stage_deployment", "unsupported"),
        ]
    }
    fn get_system_status(&self) -> zbus::fdo::Result<(String, String, bool, bool)> {
        let s = status::read_published().map_err(zbus::fdo::Error::Failed)?;
        Ok((
            s.installed.image.unwrap_or_default(),
            s.installed.version.unwrap_or_default(),
            s.restart_needed,
            s.rollback_available,
        ))
    }
    async fn list_installed(&self) -> zbus::fdo::Result<Vec<(String, String, String)>> {
        inventory().await
    }
    async fn search_packages(
        &self,
        query: &str,
    ) -> zbus::fdo::Result<Vec<(String, String, String)>> {
        if query.is_empty() || query.len() > 128 {
            return Err(zbus::fdo::Error::InvalidArgs(
                "Query must contain 1–128 bytes".into(),
            ));
        }
        Ok(inventory()
            .await?
            .into_iter()
            .filter(|r| r.0.contains(query))
            .take(200)
            .collect())
    }
    async fn get_package_info(
        &self,
        name: &str,
    ) -> zbus::fdo::Result<Vec<(String, String, String)>> {
        if name.is_empty()
            || name.len() > 128
            || !name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"+._-".contains(&b))
            || name.starts_with('-')
        {
            return Err(zbus::fdo::Error::InvalidArgs("Invalid package name".into()));
        }
        Ok(inventory()
            .await?
            .into_iter()
            .filter(|r| r.0 == name)
            .collect())
    }
    fn create_plan(&self, _request: &str) -> zbus::fdo::Result<String> {
        Err(unavailable())
    }
    fn validate_plan(&self, _id: &str) -> zbus::fdo::Result<bool> {
        Err(unavailable())
    }
    fn apply_plan(&self, _id: &str) -> zbus::fdo::Result<String> {
        Err(unavailable())
    }
    fn get_transaction(&self, _id: &str) -> zbus::fdo::Result<String> {
        Err(unavailable())
    }
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let (builder, production, state, installation) = match args.as_slice() {
        [] => (
            zbus::connection::Builder::system()?,
            true,
            std::path::PathBuf::from("/var/lib/nizho-archon"),
            None,
        ),
        [flag] if flag == "--session" => (
            zbus::connection::Builder::session()?,
            false,
            std::env::temp_dir().join(format!("nizho-archon-test-{}", std::process::id())),
            None,
        ),
        [flag, install_flag, installation, state_flag, state]
            if cfg!(debug_assertions)
                && flag == "--session"
                && install_flag == "--test-installation"
                && state_flag == "--state-dir" =>
        {
            (
                zbus::connection::Builder::session()?,
                false,
                std::path::PathBuf::from(state),
                Some(std::path::PathBuf::from(installation)),
            )
        }
        _ => return Err(
            "usage: nizho-archon-service [--session] (isolated installation flags are debug-only)"
                .into(),
        ),
    };
    let manager = manager::Manager::new(state, production, installation)?;
    let _connection = builder
        .name("org.nizho.Archon1")?
        .serve_at("/org/nizho/Archon1", Archon)?
        .serve_at("/org/nizho/Archon1", manager)?
        .build()
        .await?;
    std::future::pending::<()>().await;
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn inventory_rejects_corruption() {
        assert!(parse_inventory("name\tversion").is_err());
    }
    #[test]
    fn inventory_retains_versions_and_architectures() {
        assert_eq!(
            parse_inventory("z\t1:2-3\tx86_64\na\t2\tnoarch\n").unwrap()[1],
            ("z".into(), "1:2-3".into(), "x86_64".into())
        );
    }
    #[test]
    fn mutations_are_disabled() {
        assert!(Archon.apply_plan("anything").is_err());
    }
}
