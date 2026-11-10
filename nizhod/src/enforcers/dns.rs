use std::collections::BTreeSet;
use std::sync::{Arc, Mutex};

use nizho_shell_common::DnsStatus;
use crate::config::Config;
use crate::error::NizhodError;
use crate::state::SharedState;
use super::AsyncEnforcer;

const RESOLVED_CONF: &str = "/etc/systemd/resolved.conf.d/99-nizhod-dot.conf";

type EventLog = Arc<dyn Fn(&str, &str, &str) + Send + Sync>;

pub struct DnsEnforcer {
    server: String,
    tls_name: String,
    log: EventLog,
    exceptions: Mutex<ExceptionTracker>,
}

/// Remembers which NetworkManager connections are using the per-network
/// exception (connection.dns-over-tls=no), so each one is written to the event
/// log when it is first seen and when it ends, and not on every check.
///
/// Any desktop program of the user can set that connection setting without a
/// password. The exception stays a healthy state (see state.rs); these events
/// only make it visible. The memory is not kept across a daemon restart, so a
/// connection still excepted then is logged once more.
#[derive(Default)]
struct ExceptionTracker {
    active: BTreeSet<String>,
}

impl ExceptionTracker {
    /// `excepted` is `Some` only when this check could tell which connections
    /// are excepted: every connection in use, or none when DNS is encrypted
    /// everywhere. `None` means unknown (resolver or NetworkManager gave no
    /// clear answer) and changes nothing, so a failed check never logs an end.
    fn observe(&mut self, excepted: Option<BTreeSet<String>>) -> Vec<(&'static str, String)> {
        let Some(now) = excepted else { return Vec::new() };
        let mut events = Vec::new();
        for name in now.difference(&self.active) {
            events.push(("exception", format!(
                "Unencrypted DNS is allowed on the connection \"{}\": its setting connection.dns-over-tls is \"no\"",
                shown(name)
            )));
        }
        for name in self.active.difference(&now) {
            events.push(("exception-ended", format!(
                "The unencrypted DNS exception on the connection \"{}\" is no longer in use",
                shown(name)
            )));
        }
        self.active = now;
        events
    }
}

/// A connection name as written to the event log. The name is chosen outside
/// nizhod, so control characters are replaced and the length is bounded.
fn shown(name: &str) -> String {
    let mut out: String = name.chars().take(64).map(|c| if c.is_control() { '?' } else { c }).collect();
    if name.chars().count() > 64 { out.push_str("..."); }
    out
}

impl DnsEnforcer {
    pub fn new(cfg: &Config) -> Self {
        Self::with_log(cfg, Arc::new(crate::log::log_event))
    }

    fn with_log(cfg: &Config, log: EventLog) -> Self {
        Self {
            server: cfg.dns.server.clone(),
            tls_name: cfg.dns.tls_name.clone(),
            log,
            exceptions: Mutex::new(ExceptionTracker::default()),
        }
    }

    /// Logs exceptions that started or ended since the last check.
    fn note_exceptions(&self, excepted: Option<BTreeSet<String>>) {
        let events = self.exceptions.lock().unwrap_or_else(|p| p.into_inner()).observe(excepted);
        for (ev, msg) in events {
            (self.log)("dns", ev, &msg);
        }
    }

    fn conf_content(&self) -> String {
        format!(
            "[Resolve]\nDNS={}#{}\nDNSOverTLS=yes\nFallbackDNS=\nDNSSEC=allow-downgrade\n",
            self.server, self.tls_name
        )
    }
}

#[async_trait::async_trait]
impl AsyncEnforcer for DnsEnforcer {
    fn name(&self) -> &'static str { "dns" }

    async fn enforce(&self) -> Result<(), NizhodError> {
        let expected = self.conf_content();
        let changed = std::fs::read_to_string(RESOLVED_CONF).map(|s| s != expected).unwrap_or(true);
        std::fs::create_dir_all("/etc/systemd/resolved.conf.d")?;
        if changed { std::fs::write(RESOLVED_CONF, expected)?; }
        let running = crate::command::run("systemctl", &["is-active", "--quiet", "systemd-resolved"]).await.is_ok();
        if changed || !running {
            crate::command::run("systemctl", &["restart", "systemd-resolved"]).await?;
        }
        // Conflicting per-link/VPN settings stay unconfirmed. Repeated retries
        // must not restart a healthy resolver or silently rewrite a user's VPN.
        Ok(())
    }

    async fn update_state(&self, state: &SharedState) {
        let configured = std::fs::read_to_string(RESOLVED_CONF)
            .map(|c| c == self.conf_content()).unwrap_or(false);
        let stub = std::fs::read_to_string("/etc/resolv.conf")
            .map(|s| uses_stub_only(&s)).unwrap_or(false);
        // `excepted` stays None unless this check can tell which connections
        // use the exception.
        let mut excepted = None;
        let status = match crate::command::run("resolvectl", &["--no-pager", "status"]).await {
            Ok(out) => {
                let text = String::from_utf8_lossy(&out.stdout);
                if configured && stub && strict_tls_scopes(&text) {
                    excepted = Some(BTreeSet::new());
                    DnsStatus::Encrypted
                } else if !(configured && stub) {
                    DnsStatus::Degraded
                } else if let Some(connections) = user_exception_connections("nmcli", &text).await {
                    // The user chose "allow unencrypted DNS on this network only"
                    // (NetworkManager connection.dns-over-tls=no). Report it as
                    // such, never as encrypted.
                    excepted = Some(connections);
                    DnsStatus::ForcedPlaintext
                } else {
                    DnsStatus::Degraded
                }
            }
            Err(_) => DnsStatus::Error,
        };
        self.note_exceptions(excepted);
        state.write().await.dns_status = status;
    }
}

fn uses_stub_only(contents: &str) -> bool {
    let servers: Vec<_> = contents.lines().filter_map(|line| {
        let mut words = line.split_whitespace();
        (words.next() == Some("nameserver")).then(|| words.next().unwrap_or(""))
    }).collect();
    !servers.is_empty() && servers.iter().all(|s| *s == "127.0.0.53")
}

/// The connection names, when the global scope is strict and every non-strict
/// link belongs to a NetworkManager connection the user exempted
/// (connection.dns-over-tls=no). None in every other case.
async fn user_exception_connections(nmcli: &str, resolvectl: &str) -> Option<BTreeSet<String>> {
    let scopes = scopes(resolvectl);
    let global_ok = scopes.iter().any(|s| s.name.is_none() && s.has_dns && s.strict);
    let plain: Vec<_> = scopes.iter().filter(|s| s.has_dns && !s.strict).collect();
    if !global_ok || plain.is_empty() { return None; }
    let mut connections = BTreeSet::new();
    for scope in plain {
        let dev = scope.name.as_deref()?;
        let out = crate::command::run(nmcli, &["-g", "GENERAL.CONNECTION", "device", "show", dev]).await.ok()?;
        let conn = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if conn.is_empty() { return None; }
        let out = crate::command::run(nmcli, &["-g", "connection.dns-over-tls", "connection", "show", &conn]).await.ok()?;
        if !is_user_exception(&String::from_utf8_lossy(&out.stdout)) { return None; }
        connections.insert(conn);
    }
    Some(connections)
}

/// nmcli prints the setting as "no", "0" or "0 (no)" depending on version.
fn is_user_exception(value: &str) -> bool {
    let v = value.trim();
    v == "no" || v == "0" || v.starts_with("0 ")
}

struct Scope { name: Option<String>, strict: bool, has_dns: bool }

/// resolvectl status scopes: Global (name None) and "Link N (ifname)".
fn scopes(text: &str) -> Vec<Scope> {
    let mut out = Vec::new();
    let mut cur: Option<Scope> = None;
    for line in text.lines() {
        let line = line.trim();
        if line == "Global" || line.starts_with("Link ") {
            if let Some(s) = cur.take() { out.push(s); }
            let name = line.strip_prefix("Link ").and_then(|l| {
                let (a, b) = (l.find('(')?, l.rfind(')')?);
                Some(l[a + 1..b].to_string())
            });
            cur = Some(Scope { name, strict: false, has_dns: false });
        } else if let Some(s) = cur.as_mut() {
            if let Some(p) = line.strip_prefix("Protocols:") {
                s.strict = p.split_whitespace().any(|v| v == "+DNSOverTLS");
            }
            if let Some(v) = line.strip_prefix("DNS Servers:") {
                s.has_dns = !v.trim().is_empty();
            }
        }
    }
    if let Some(s) = cur { out.push(s); }
    out
}

// Check every scope that has DNS servers, including VPN/split-DNS links.
// A recognized strict TLS token is required; opportunistic TLS is insufficient.
// This observes resolver policy, not arbitrary application traffic or uptime.
fn strict_tls_scopes(text: &str) -> bool {
    let mut scopes = Vec::new();
    let mut current: Option<(bool, bool)> = None; // strict TLS, has DNS servers
    for line in text.lines() {
        let line = line.trim();
        if line == "Global" || line.starts_with("Link ") {
            if let Some(scope) = current.take() { scopes.push(scope); }
            current = Some((false, false));
        } else if let Some(scope) = current.as_mut() {
            if let Some(protocols) = line.strip_prefix("Protocols:") {
                scope.0 = protocols.split_whitespace().any(|v| v == "+DNSOverTLS");
            }
            if let Some(servers) = line.strip_prefix("DNS Servers:") {
                scope.1 = !servers.trim().is_empty();
            }
        }
    }
    if let Some(scope) = current { scopes.push(scope); }
    let dns_scopes: Vec<_> = scopes.into_iter().filter(|s| s.1).collect();
    !dns_scopes.is_empty() && dns_scopes.iter().all(|s| s.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_resolver_bypass_and_empty_configuration() {
        assert!(uses_stub_only("# local resolver\nnameserver 127.0.0.53\n"));
        assert!(!uses_stub_only("nameserver 127.0.0.53\nnameserver 8.8.8.8"));
        assert!(!uses_stub_only("# nameserver 127.0.0.53"));
    }
    #[test]
    fn plaintext_split_dns_cannot_hide_behind_encrypted_global_dns() {
        let global = "Global\n Protocols: -LLMNR +DNSOverTLS\n DNS Servers: 9.9.9.9#dns.quad9.net\n";
        assert!(strict_tls_scopes(global));
        assert!(!strict_tls_scopes(&format!("{global}Link 2 (vpn)\n Protocols: -DNSOverTLS\n DNS Servers: 10.0.0.1")));
        assert!(!strict_tls_scopes(&global.replace("+DNSOverTLS", "DNSOverTLS=opportunistic")));
        assert!(!strict_tls_scopes(""));
        assert!(!strict_tls_scopes("Global\n Protocols: +DNSOverTLS"));
    }
    #[test]
    fn finds_the_plaintext_link_by_interface_name() {
        let t = "Global\n Protocols: +DNSOverTLS\n DNS Servers: 9.9.9.9#dns.quad9.net\n\
                 Link 3 (wlp2s0)\n Protocols: +DefaultRoute -DNSOverTLS\n DNS Servers: 192.168.1.1\n";
        let s = scopes(t);
        assert_eq!(s.len(), 2);
        assert!(s[0].name.is_none() && s[0].strict && s[0].has_dns);
        assert_eq!(s[1].name.as_deref(), Some("wlp2s0"));
        assert!(!s[1].strict && s[1].has_dns);
    }
    fn set(names: &[&str]) -> Option<BTreeSet<String>> {
        Some(names.iter().map(|n| n.to_string()).collect())
    }

    #[test]
    fn an_exception_is_logged_when_first_seen_and_when_it_ends_and_not_in_between() {
        let mut t = ExceptionTracker::default();
        assert!(t.observe(set(&[])).is_empty(), "encrypted everywhere: nothing to say");
        assert_eq!(t.observe(set(&["Cafe"])), [("exception",
            "Unencrypted DNS is allowed on the connection \"Cafe\": its setting connection.dns-over-tls is \"no\"".to_string())]);
        // Checks repeat every 60 seconds, and twice within one reconcile.
        for _ in 0..5 { assert!(t.observe(set(&["Cafe"])).is_empty()); }
        // An unclear check (resolver error, another plaintext link) says nothing
        // and does not count as an end.
        assert!(t.observe(None).is_empty());
        assert!(t.observe(set(&["Cafe"])).is_empty());
        assert_eq!(t.observe(set(&[])), [("exception-ended",
            "The unencrypted DNS exception on the connection \"Cafe\" is no longer in use".to_string())]);
        assert!(t.observe(set(&[])).is_empty());
        // Seen again later: a new first sighting.
        assert_eq!(t.observe(set(&["Cafe"])).len(), 1);
    }

    #[test]
    fn each_connection_is_tracked_by_name() {
        let mut t = ExceptionTracker::default();
        assert_eq!(t.observe(set(&["Cafe", "Hotel"])).len(), 2);
        let events = t.observe(set(&["Hotel", "Train"]));
        assert_eq!(events.len(), 2);
        assert!(events.iter().any(|(ev, msg)| *ev == "exception" && msg.contains("\"Train\"")));
        assert!(events.iter().any(|(ev, msg)| *ev == "exception-ended" && msg.contains("\"Cafe\"")));
        assert!(!events.iter().any(|(_, msg)| msg.contains("Hotel")));
    }

    #[test]
    fn a_connection_name_cannot_add_lines_or_unbounded_text_to_an_event() {
        assert_eq!(shown("Cafe\nfake\u{7}"), "Cafe?fake?");
        let long = shown(&"x".repeat(500));
        assert_eq!(long.chars().count(), 67);
        assert!(long.ends_with("..."));
        assert_eq!(shown("Caf\u{e9} \"Free\""), "Caf\u{e9} \"Free\"");
    }

    #[test]
    fn the_enforcer_writes_each_change_to_the_event_log_once() {
        let events = Arc::new(Mutex::new(Vec::new()));
        let sink = events.clone();
        let e = DnsEnforcer::with_log(&Config::default(), Arc::new(move |cat, ev, msg| {
            sink.lock().unwrap().push(format!("{cat}|{ev}|{msg}"));
        }));
        e.note_exceptions(set(&["Cafe"]));
        e.note_exceptions(set(&["Cafe"]));
        e.note_exceptions(None);
        e.note_exceptions(set(&[]));
        e.note_exceptions(set(&[]));
        let events = events.lock().unwrap();
        assert_eq!(events.len(), 2, "{events:?}");
        assert!(events[0].starts_with("dns|exception|Unencrypted DNS is allowed on the connection \"Cafe\""));
        assert!(events[1].starts_with("dns|exception-ended|"));
    }

    const GLOBAL: &str = "Global\n Protocols: +DNSOverTLS\n DNS Servers: 9.9.9.9#dns.quad9.net\n";
    const WIFI: &str = "Link 3 (wlp2s0)\n Protocols: +DefaultRoute -DNSOverTLS\n DNS Servers: 192.168.1.1\n";
    const WIRED: &str = "Link 2 (enp1s0)\n Protocols: +DefaultRoute -DNSOverTLS\n DNS Servers: 10.0.0.1\n";

    /// A stand-in for nmcli: answers the two queries nizhod makes from a fixed
    /// table of device, connection name and connection.dns-over-tls value.
    struct FakeNmcli(std::path::PathBuf);
    impl FakeNmcli {
        fn new(table: &[(&str, &str, &str)]) -> Self {
            use std::os::unix::fs::PermissionsExt;
            static NEXT: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
            let path = std::env::temp_dir().join(format!(
                "nizhod-fake-nmcli-{}-{}", std::process::id(), NEXT.fetch_add(1, std::sync::atomic::Ordering::SeqCst)
            ));
            // Called as: -g GENERAL.CONNECTION device show DEV
            //        or: -g connection.dns-over-tls connection show NAME
            let mut script = String::from("#!/bin/sh\ncase \"$2|$5\" in\n");
            for (dev, conn, value) in table {
                script.push_str(&format!("'GENERAL.CONNECTION|{dev}') echo '{conn}' ;;\n"));
                script.push_str(&format!("'connection.dns-over-tls|{conn}') echo '{value}' ;;\n"));
            }
            script.push_str("*) exit 10 ;;\nesac\n");
            // Written by a child process. A file this process had open for
            // writing could still be open in a child another test forks at the
            // same moment, and running it would then fail as "text file busy".
            let mut writer = std::process::Command::new("sh")
                .args(["-c", "umask 077 && cat > \"$1\"", "sh"])
                .arg(&path)
                .stdin(std::process::Stdio::piped())
                .spawn()
                .unwrap();
            std::io::Write::write_all(&mut writer.stdin.take().unwrap(), script.as_bytes()).unwrap();
            assert!(writer.wait().unwrap().success());
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
            Self(path)
        }
        fn path(&self) -> &str { self.0.to_str().unwrap() }
    }
    impl Drop for FakeNmcli {
        fn drop(&mut self) {
            if let Err(e) = std::fs::remove_file(&self.0) {
                eprintln!("could not remove {}: {e}", self.0.display());
            }
        }
    }

    #[tokio::test]
    async fn exception_connections_are_named_only_when_every_plaintext_link_is_excepted() {
        let both = format!("{GLOBAL}{WIFI}{WIRED}");
        let nm = FakeNmcli::new(&[("wlp2s0", "Cafe Free WiFi", "0 (no)"), ("enp1s0", "Office", "no")]);
        assert_eq!(user_exception_connections(nm.path(), &format!("{GLOBAL}{WIFI}")).await, set(&["Cafe Free WiFi"]));
        assert_eq!(user_exception_connections(nm.path(), &both).await, set(&["Cafe Free WiFi", "Office"]));

        // One link without the exception: not a user exception at all.
        let nm = FakeNmcli::new(&[("wlp2s0", "Cafe Free WiFi", "0 (no)"), ("enp1s0", "Office", "-1 (default)")]);
        assert_eq!(user_exception_connections(nm.path(), &both).await, None);
        // NetworkManager does not know the link, or has no connection on it.
        assert_eq!(user_exception_connections(nm.path(), &format!("{GLOBAL}Link 9 (tun0)\n Protocols: -DNSOverTLS\n DNS Servers: 10.8.0.1\n")).await, None);
        let nm = FakeNmcli::new(&[("wlp2s0", "", "no")]);
        assert_eq!(user_exception_connections(nm.path(), &format!("{GLOBAL}{WIFI}")).await, None);
        // No plaintext link, a plaintext global scope, or no nmcli: never an exception.
        assert_eq!(user_exception_connections(nm.path(), GLOBAL).await, None);
        assert_eq!(user_exception_connections(nm.path(), &format!("{}{WIFI}", GLOBAL.replace("+DNSOverTLS", "-DNSOverTLS"))).await, None);
        assert_eq!(user_exception_connections("/nonexistent/nmcli", &format!("{GLOBAL}{WIFI}")).await, None);
    }

    #[test]
    fn only_an_explicit_no_counts_as_a_user_exception() {
        for v in ["no", "0", "0 (no)", " no\n"] { assert!(is_user_exception(v), "{v}"); }
        for v in ["", "-1", "-1 (default)", "1 (opportunistic)", "2 (yes)", "yes", "default"] {
            assert!(!is_user_exception(v), "{v}");
        }
    }
}
