use archon_distro::flatpak::Request;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    io::{Read, Write},
    os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt},
    path::{Path, PathBuf},
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    sync::Mutex,
};
use zbus::{message::Header, zvariant::Type, Connection, SignalContext};

type Result<T> = zbus::fdo::Result<T>;
fn failed(e: impl ToString) -> zbus::fdo::Error {
    zbus::fdo::Error::Failed(e.to_string())
}
fn invalid(e: impl ToString) -> zbus::fdo::Error {
    zbus::fdo::Error::InvalidArgs(e.to_string())
}
fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct Package {
    pub id: String,
    pub name: String,
    pub summary: String,
    pub version: String,
    pub repository: String,
    pub backend: String,
    pub installed: bool,
    pub download_bytes: u64,
    pub installed_bytes: u64,
    pub trust: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct SystemStatus {
    pub installed_image: String,
    pub installed_version: String,
    pub staged_image: String,
    pub staged_version: String,
    pub staged_state: String,
    pub reboot_required: bool,
    pub rollback_available: bool,
    pub rollback_queued: bool,
    pub read_only: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct Plan {
    pub id: String,
    pub expires_at: u64,
    pub backend: String,
    pub details: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct Transaction {
    pub id: String,
    pub state: String,
    pub package: String,
    pub operation: String,
    pub created_at: u64,
    pub detail: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Record {
    uid: u32,
    sender: String,
    plan: Plan,
    request: Request,
    resolved: Value,
    transaction: Transaction,
}
impl Record {
    fn validate_commit(&self, sender: &str, at: u64) -> std::result::Result<(), &'static str> {
        if self.sender != sender
            || self.transaction.state != "planned"
            || at < self.transaction.created_at
            || at >= self.plan.expires_at
        {
            return Err("expired, consumed or caller-mismatched plan");
        }
        Ok(())
    }
}

struct Inner {
    root: PathBuf,
    records: Mutex<BTreeMap<String, Record>>,
    gate: Arc<Mutex<()>>,
    production: bool,
    test_installation: Option<PathBuf>,
    active: Mutex<BTreeMap<String, tokio::sync::watch::Sender<bool>>>,
}
#[derive(Clone)]
pub struct Manager(Arc<Inner>);

fn valid_id(id: &str) -> bool {
    id.len() == 32 && id.bytes().all(|c| c.is_ascii_hexdigit())
}
fn save(root: &Path, record: &Record) -> std::io::Result<()> {
    if !valid_id(&record.plan.id) {
        return Err(std::io::Error::other("invalid record ID"));
    }
    let bytes = serde_json::to_vec(record)?;
    let temp = root.join(format!("{}.pending", record.plan.id));
    // Exclusive create prevents following or replacing an attacker-supplied path.
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&temp)?;
    f.write_all(&bytes)?;
    f.sync_all()?;
    std::fs::rename(temp, root.join(format!("{}.json", record.plan.id)))?;
    std::fs::File::open(root)?.sync_all()
}
impl Manager {
    pub fn new(
        root: PathBuf,
        production: bool,
        test_installation: Option<PathBuf>,
    ) -> std::io::Result<Self> {
        if test_installation.is_some() && (production || !cfg!(debug_assertions)) {
            return Err(std::io::Error::other(
                "test installations require a debug session service",
            ));
        }
        std::fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(&root)?;
        let meta = std::fs::symlink_metadata(&root)?;
        if !meta.is_dir()
            || meta.uid() != std::fs::metadata("/proc/self")?.uid()
            || meta.permissions().mode() & 0o077 != 0
        {
            return Err(std::io::Error::other(
                "state directory must be private (0700)",
            ));
        }
        let mut records = BTreeMap::new();
        for entry in std::fs::read_dir(&root)? {
            let entry = entry?;
            if entry.path().extension().and_then(|s| s.to_str()) != Some("json") {
                continue;
            }
            if !entry.file_type()?.is_file() || entry.metadata()?.len() > 1048576 {
                return Err(std::io::Error::other("unsafe transaction record"));
            }
            let mut record: Record = serde_json::from_reader(std::fs::File::open(entry.path())?)?;
            if !valid_id(&record.plan.id)
                || entry.file_name() != format!("{}.json", record.plan.id).as_str()
            {
                return Err(std::io::Error::other("record identity mismatch"));
            }
            if matches!(
                record.transaction.state.as_str(),
                "planned" | "authorizing" | "running"
            ) {
                record.transaction.state = "interrupted".into();
                record.transaction.detail =
                    "Service restarted; inspect native state and create a fresh plan".into();
                save(&root, &record)?;
            }
            records.insert(record.plan.id.clone(), record);
        }
        Ok(Self(Arc::new(Inner {
            root,
            records: Mutex::new(records),
            gate: Arc::new(Mutex::new(())),
            production,
            test_installation,
            active: Mutex::new(BTreeMap::new()),
        })))
    }
    async fn owner(conn: &Connection, header: &Header<'_>) -> Result<(u32, String)> {
        let sender = header
            .sender()
            .ok_or_else(|| invalid("authenticated sender required"))?
            .to_string();
        let reply = conn
            .call_method(
                Some("org.freedesktop.DBus"),
                "/org/freedesktop/DBus",
                Some("org.freedesktop.DBus"),
                "GetConnectionUnixUser",
                &(&sender,),
            )
            .await
            .map_err(failed)?;
        let uid: u32 = reply.body().deserialize().map_err(failed)?;
        Ok((uid, sender))
    }
    async fn bridge(
        &self,
        request: Value,
        progress: Option<(Connection, String, tokio::sync::watch::Receiver<bool>)>,
    ) -> Result<Value> {
        static LIMIT: tokio::sync::Semaphore = tokio::sync::Semaphore::const_new(4);
        let _permit = LIMIT
            .try_acquire()
            .map_err(|_| failed("backend is busy; retry shortly"))?;
        let mut command = tokio::process::Command::new("/usr/bin/python3");
        command.args(["-I", "-c", archon_distro::flatpak::BRIDGE]);
        if let Some(path) = &self.0.test_installation {
            command.arg("--test-installation").arg(path);
        }
        let mut child = command
            .env_clear()
            .env("PATH", "/usr/bin:/bin")
            .env("LANG", "C.UTF-8")
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .map_err(failed)?;
        let bytes = serde_json::to_vec(&request).map_err(failed)?;
        if bytes.len() > 1048576 {
            return Err(invalid("request too large"));
        }
        let mut input = child
            .stdin
            .take()
            .ok_or_else(|| failed("missing bridge input"))?;
        input.write_all(&bytes).await.map_err(failed)?;
        input.write_all(b"\n").await.map_err(failed)?;
        let cancellation = progress.as_ref().map(|(_, _, rx)| rx.clone());
        tokio::spawn(async move {
            if let Some(mut rx) = cancellation {
                let requested = rx.changed().await.is_ok() && *rx.borrow();
                if requested {
                    let _ = input.write_all(b"cancel\n").await;
                }
            }
        });
        let output = child
            .stdout
            .take()
            .ok_or_else(|| failed("missing bridge output"))?;
        let mut reader = BufReader::new(output);
        let work = async {
            let mut result = None;
            let mut total = 0;
            loop {
                let mut line = Vec::new();
                // Bound every event before parsing; no unbounded read_line allocation.
                loop {
                    let buf = reader.fill_buf().await.map_err(failed)?;
                    if buf.is_empty() {
                        break;
                    }
                    let n = buf
                        .iter()
                        .position(|b| *b == b'\n')
                        .map_or(buf.len(), |n| n + 1);
                    if line.len() + n > 1048576 {
                        return Err(failed("backend event too large"));
                    }
                    line.extend_from_slice(&buf[..n]);
                    reader.consume(n);
                    if line.last() == Some(&b'\n') {
                        break;
                    }
                }
                if line.is_empty() {
                    break;
                }
                total += line.len();
                if total > 32 * 1048576 {
                    return Err(failed("backend output exceeds limit"));
                }
                let event: Value = serde_json::from_slice(&line).map_err(failed)?;
                match event["event"].as_str() {
                    Some("result") => result = Some(event["value"].clone()),
                    Some("error") => {
                        return Err(failed(
                            event["detail"].as_str().unwrap_or("backend failure"),
                        ))
                    }
                    Some("progress") => {
                        if let Some((conn, id, _)) = &progress {
                            let ctxt =
                                SignalContext::new(conn, "/org/nizho/Archon1").map_err(failed)?;
                            let _ = Self::transaction_progress(
                                &ctxt,
                                id,
                                event["percent"].as_u64().unwrap_or(0) as u32,
                                event["detail"].as_str().unwrap_or(""),
                            )
                            .await;
                        }
                    }
                    _ => return Err(failed("unknown backend event")),
                }
            }
            if !child.wait().await.map_err(failed)?.success() {
                return Err(failed("native backend failed"));
            }
            result.ok_or_else(|| failed("backend returned no result"))
        };
        tokio::time::timeout(
            std::time::Duration::from_secs(if progress.is_some() { 3600 } else { 120 }),
            work,
        )
        .await
        .map_err(|_| failed("backend timed out"))?
    }
    async fn record(&self, id: &str, uid: u32) -> Result<Record> {
        if !valid_id(id) {
            return Err(invalid("invalid transaction identifier"));
        }
        let records = self.0.records.lock().await;
        let record = records
            .get(id)
            .ok_or_else(|| invalid("unknown transaction"))?;
        if record.uid != uid {
            return Err(zbus::fdo::Error::AccessDenied(
                "transaction belongs to another user".into(),
            ));
        }
        Ok(record.clone())
    }
    async fn store(&self, record: Record) -> Result<()> {
        let root = self.0.root.clone();
        let saved = record.clone();
        tokio::task::spawn_blocking(move || save(&root, &saved))
            .await
            .map_err(failed)?
            .map_err(failed)?;
        self.0
            .records
            .lock()
            .await
            .insert(record.plan.id.clone(), record);
        Ok(())
    }
}
#[zbus::interface(name = "org.nizho.Archon1.Manager")]
impl Manager {
    fn get_capabilities(&self) -> Vec<(&str, &str)> {
        vec![
            (
                "flatpak-system",
                "discover,details,installed,updates,plan,commit",
            ),
            (
                "system-packages",
                "unsupported: verified image builder required",
            ),
            (
                "scope",
                "system Flatpak installation; legacy user installations remain separate",
            ),
        ]
    }
    async fn get_system_status(&self) -> Result<(SystemStatus,)> {
        let status = tokio::task::spawn_blocking(crate::status::read_published)
            .await
            .map_err(failed)?
            .map_err(failed)?;
        let (staged_image, staged_version, staged_state) = match status.staged {
            Some(stage) => (
                stage.image.unwrap_or_default(),
                stage.version.unwrap_or_default(),
                if stage.download_only {
                    "downloaded"
                } else {
                    "staged"
                }
                .to_string(),
            ),
            None => (String::new(), String::new(), "none".to_string()),
        };
        Ok((SystemStatus {
            installed_image: status.installed.image.unwrap_or_default(),
            installed_version: status.installed.version.unwrap_or_default(),
            staged_image,
            staged_version,
            staged_state,
            reboot_required: status.restart_needed,
            rollback_available: status.rollback_available,
            rollback_queued: status.rollback_queued,
            read_only: status.read_only,
        },))
    }
    async fn search_packages(
        &self,
        query: &str,
        category: &str,
        offset: u32,
        limit: u32,
    ) -> Result<Vec<Package>> {
        if category.len() > 64
            || !category.bytes().all(|c| c.is_ascii_alphanumeric())
            || query.len() > 128
            || query.chars().any(char::is_control)
            || limit == 0
            || limit > 100
            || offset > 100000
        {
            return Err(invalid("invalid search bounds"));
        }
        serde_json::from_value(
            self.bridge(
                json!({"mode":"search","query":query,"category":category,"offset":offset,"limit":limit}),
                None,
            )
            .await?,
        )
        .map_err(failed)
    }
    async fn get_installed_packages(&self) -> Result<Vec<Package>> {
        serde_json::from_value(self.bridge(json!({"mode":"installed"}), None).await?)
            .map_err(failed)
    }
    async fn get_available_updates(&self) -> Result<Vec<Package>> {
        serde_json::from_value(self.bridge(json!({"mode":"updates"}), None).await?).map_err(failed)
    }
    async fn get_repositories(&self) -> Result<String> {
        Ok(self
            .bridge(json!({"mode":"repositories"}), None)
            .await?
            .to_string())
    }
    async fn get_package_details(&self, package: &str, repository: &str) -> Result<String> {
        Request {
            action: "install".into(),
            package: package.into(),
            repository: repository.into(),
        }
        .validate()
        .map_err(invalid)?;
        Ok(self
            .bridge(
                json!({"mode":"details","package":package,"repository":repository}),
                None,
            )
            .await?
            .to_string())
    }
    async fn plan_transaction(
        &self,
        action: &str,
        package: &str,
        repository: &str,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] header: Header<'_>,
    ) -> Result<(Plan,)> {
        let request = Request {
            action: action.into(),
            package: package.into(),
            repository: repository.into(),
        };
        request.validate().map_err(invalid)?;
        let _gate = self
            .0
            .gate
            .try_lock()
            .map_err(|_| failed("conflicting transaction in progress"))?;
        let (uid, sender) = Self::owner(conn, &header).await?;
        {
            let records = self.0.records.lock().await;
            if records.len() >= 10000
                || records
                    .values()
                    .filter(|r| {
                        r.uid == uid
                            && r.transaction.state == "planned"
                            && r.plan.expires_at > now()
                    })
                    .count()
                    >= 8
            {
                return Err(failed("transaction quota reached"));
            }
        }
        let resolved = self
            .bridge(json!({"mode":"plan","request":request}), None)
            .await?;
        let mut random = [0u8; 16];
        std::fs::File::open("/dev/urandom")
            .and_then(|mut f| f.read_exact(&mut random))
            .map_err(failed)?;
        let id: String = random.iter().map(|b| format!("{b:02x}")).collect();
        let plan = Plan {
            id: id.clone(),
            expires_at: now() + 300,
            backend: "flatpak-system".into(),
            details: resolved.to_string(),
        };
        self.store(Record {
            uid,
            sender,
            plan: plan.clone(),
            request: request.clone(),
            resolved,
            transaction: Transaction {
                id,
                state: "planned".into(),
                package: request.package,
                operation: request.action,
                created_at: now(),
                detail: String::new(),
            },
        })
        .await?;
        Ok((plan,))
    }
    async fn commit_transaction(
        &self,
        id: &str,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] header: Header<'_>,
    ) -> Result<String> {
        let (uid, sender) = Self::owner(conn, &header).await?;
        let gate = self
            .0
            .gate
            .clone()
            .try_lock_owned()
            .map_err(|_| failed("conflicting transaction in progress"))?;
        let mut record = self.record(id, uid).await?;
        record.validate_commit(&sender, now()).map_err(invalid)?;
        // Never enable privileged system operations on the development host.
        if !self.0.production || !Path::new("/usr/lib/nizho-release").is_file() {
            return Err(zbus::fdo::Error::AccessDenied(
                "commits require the Nizho system service".into(),
            ));
        }
        let request = &record.request;
        if let Err(error) =
            crate::authorization::check(conn, &header, &request.action, &request.package).await
        {
            eprintln!("Archon authorization denied uid={uid} plan={id}: {error}");
            return Err(error);
        }
        eprintln!("Archon authorized uid={uid} plan={id}");
        record.validate_commit(&sender, now()).map_err(invalid)?;
        record.transaction.state = "running".into();
        self.store(record.clone()).await?;
        let (cancel, cancelled) = tokio::sync::watch::channel(false);
        self.0.active.lock().await.insert(id.to_string(), cancel);
        let manager = self.clone();
        let conn = conn.clone();
        let id = id.to_string();
        let result_id = id.clone();
        tokio::spawn(async move {
            let _gate = gate;
            let result = manager.bridge(
                json!({"mode":"commit","request":record.request,"expected":record.resolved,"expires_at":record.plan.expires_at}),
                Some((conn.clone(), id.clone(), cancelled.clone())),
            )
            .await;
            manager.0.active.lock().await.remove(&id);
            let success = result.is_ok();
            record.transaction.state = if success {
                "succeeded"
            } else if *cancelled.borrow() {
                "cancelled"
            } else {
                "failed"
            }
            .into();
            record.transaction.detail = result.err().map(|e| e.to_string()).unwrap_or_default();
            if let Err(error) = manager.store(record.clone()).await {
                eprintln!("Archon journal persistence failed: {error}");
                return;
            }
            if let Ok(ctxt) = SignalContext::new(&conn, "/org/nizho/Archon1") {
                if success {
                    let _ = Self::transaction_completed(&ctxt, &id).await;
                    let _ = Self::package_catalogue_changed(&ctxt).await;
                } else {
                    let _ = Self::transaction_failed(&ctxt, &id, &record.transaction.detail).await;
                }
            }
        });
        Ok(result_id)
    }
    async fn get_transaction_status(
        &self,
        id: &str,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] header: Header<'_>,
    ) -> Result<(Transaction,)> {
        let (uid, _) = Self::owner(conn, &header).await?;
        Ok((self.record(id, uid).await?.transaction,))
    }
    async fn get_transaction_history(
        &self,
        offset: u32,
        limit: u32,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] header: Header<'_>,
    ) -> Result<Vec<Transaction>> {
        if limit == 0 || limit > 100 || offset > 10000 {
            return Err(invalid("invalid history bounds"));
        }
        let (uid, _) = Self::owner(conn, &header).await?;
        let records = self.0.records.lock().await;
        let mut rows: Vec<_> = records
            .values()
            .filter(|r| r.uid == uid)
            .map(|r| r.transaction.clone())
            .collect();
        rows.sort_by_key(|r| std::cmp::Reverse(r.created_at));
        Ok(rows
            .into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect())
    }
    async fn cancel_transaction(
        &self,
        id: &str,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] header: Header<'_>,
    ) -> Result<()> {
        let (uid, sender) = Self::owner(conn, &header).await?;
        let mut record = self.record(id, uid).await?;
        if record.sender != sender {
            return Err(zbus::fdo::Error::AccessDenied(
                "caller-mismatched transaction".into(),
            ));
        }
        if record.transaction.state == "running" {
            let active = self.0.active.lock().await;
            let cancel = active
                .get(id)
                .ok_or_else(|| invalid("transaction already finished"))?;
            cancel.send(true).map_err(failed)?;
            return Ok(());
        }
        let _gate = self
            .0
            .gate
            .try_lock()
            .map_err(|_| failed("conflicting transaction in progress"))?;
        if record.transaction.state != "planned" {
            return Err(invalid("transaction is no longer cancellable"));
        }
        record.transaction.state = "cancelled".into();
        self.store(record).await
    }
    #[zbus(signal)]
    async fn package_catalogue_changed(ctxt: &SignalContext<'_>) -> zbus::Result<()>;
    #[zbus(signal)]
    async fn transaction_progress(
        ctxt: &SignalContext<'_>,
        id: &str,
        percent: u32,
        detail: &str,
    ) -> zbus::Result<()>;
    #[zbus(signal)]
    async fn transaction_completed(ctxt: &SignalContext<'_>, id: &str) -> zbus::Result<()>;
    #[zbus(signal)]
    async fn transaction_failed(
        ctxt: &SignalContext<'_>,
        id: &str,
        detail: &str,
    ) -> zbus::Result<()>;
}

#[cfg(test)]
mod tests {
    use super::*;
    fn directory() -> PathBuf {
        let mut random = [0u8; 16];
        std::fs::File::open("/dev/urandom")
            .unwrap()
            .read_exact(&mut random)
            .unwrap();
        let root =
            std::env::temp_dir().join(format!("archon-journal-{:x}", u128::from_le_bytes(random)));
        std::fs::DirBuilder::new()
            .mode(0o700)
            .create(&root)
            .unwrap();
        root
    }
    fn record() -> Record {
        let id = "a".repeat(32);
        Record {
            uid: 1000,
            sender: ":1.2".into(),
            plan: Plan {
                id: id.clone(),
                expires_at: now() + 300,
                backend: "flatpak-system".into(),
                details: "{}".into(),
            },
            request: Request {
                action: "install".into(),
                package: "app/org.example.App/x86_64/stable".into(),
                repository: "test".into(),
            },
            resolved: json!({}),
            transaction: Transaction {
                id,
                state: "planned".into(),
                package: "org.example.App".into(),
                operation: "install".into(),
                created_at: now(),
                detail: String::new(),
            },
        }
    }
    #[test]
    fn journal_rejects_unsafe_paths_and_public_directories() {
        let root = directory();
        let mut row = record();
        row.plan.id = "../escape".into();
        assert!(save(&root, &row).is_err());
        std::fs::set_permissions(&root, std::fs::Permissions::from_mode(0o755)).unwrap();
        assert!(Manager::new(root.clone(), false, None).is_err());
        std::fs::remove_dir(root).unwrap();
    }
    #[tokio::test]
    async fn restart_never_replays_pending_or_running_transactions() {
        for state in ["planned", "running"] {
            let root = directory();
            let mut row = record();
            row.transaction.state = state.into();
            save(&root, &row).unwrap();
            let manager = Manager::new(root.clone(), false, None).unwrap();
            let recovered = manager.record(&row.plan.id, 1000).await.unwrap();
            assert_eq!(recovered.transaction.state, "interrupted");
            assert!(manager.record(&row.plan.id, 1001).await.is_err());
            assert!(manager.record("../escape", 1000).await.is_err());
            drop(manager);
            std::fs::remove_dir_all(root).unwrap();
        }
    }
    #[test]
    fn stale_replayed_and_caller_mismatched_plans_are_rejected() {
        let mut row = record();
        let at = now();
        assert!(row.validate_commit(":1.2", at).is_ok());
        assert!(row.validate_commit(":1.3", at).is_err());
        assert!(row.validate_commit(":1.2", row.plan.expires_at).is_err());
        assert!(row
            .validate_commit(":1.2", row.transaction.created_at - 1)
            .is_err());
        row.transaction.state = "succeeded".into();
        assert!(row.validate_commit(":1.2", at).is_err());
    }
}
