use std::path::PathBuf;
use std::sync::Arc;

use crate::config::Config;
use crate::error::NizhodError;
use crate::state::SharedState;
use super::AsyncEnforcer;

/// The Nizho zone (shipped in /usr/lib/firewalld/zones/nizho.xml): rejects
/// unsolicited incoming connections; replies to the computer's own
/// connections are still allowed.
const ZONE: &str = "nizho";

/// firewalld's own service for KDE Connect: 1714-1764 tcp and udp. The only
/// thing the zone may hold beyond dhcpv6-client, and only while the user has
/// allowed phone linking (the flag file written by nizho-phone-link).
const KDECONNECT: &str = "kdeconnect";

type EventLog = Arc<dyn Fn(&str, &str, &str) + Send + Sync>;

pub struct FirewallEnforcer {
    firewall_cmd: String,
    phone_link_state: PathBuf,
    log: EventLog,
}

/// What the phone-link flag file says. Only the word `on` (surrounding
/// whitespace ignored) allows phone linking; a missing file or any other
/// content is off. A file that exists but cannot be read leaves consent
/// unknown, which is treated as off for enforcement and is never reported
/// as confirmed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PhoneLink { On, Off, Unreadable }

fn read_phone_link(path: &std::path::Path) -> PhoneLink {
    match std::fs::read(path) {
        Ok(bytes) if String::from_utf8_lossy(&bytes).trim() == "on" => PhoneLink::On,
        Ok(_) => PhoneLink::Off,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => PhoneLink::Off,
        Err(_) => PhoneLink::Unreadable,
    }
}

impl FirewallEnforcer {
    pub fn new(cfg: &Config) -> Self {
        Self::with_parts("firewall-cmd", &cfg.phone_link.state_path, Arc::new(crate::log::log_event))
    }

    fn with_parts(firewall_cmd: &str, state_path: &str, log: EventLog) -> Self {
        Self { firewall_cmd: firewall_cmd.into(), phone_link_state: state_path.into(), log }
    }

    async fn fw(&self, args: &[&str]) -> Result<String, NizhodError> {
        let out = crate::command::run_patient(&self.firewall_cmd, args).await?;
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    }

    /// Services of the nizho zone, runtime or permanent configuration.
    async fn zone_services(&self, permanent: bool) -> Result<Vec<String>, NizhodError> {
        let mut args = vec!["--zone=nizho", "--list-services"];
        if permanent { args.insert(0, "--permanent"); }
        Ok(self.fw(&args).await?.split_whitespace().map(str::to_owned).collect())
    }

    /// Makes the KDE Connect rule match the flag: present (runtime and
    /// permanent) when phone linking is on, absent from both otherwise. A rule
    /// found while the flag is not on is drift (for example added by hand with
    /// firewall-cmd) and is removed with a plain event.
    async fn enforce_phone_rule(&self) -> Result<(), NizhodError> {
        let flag = read_phone_link(&self.phone_link_state);
        let runtime = self.zone_services(false).await?;
        let permanent = self.zone_services(true).await?;
        let has = |l: &[String]| l.iter().any(|s| s == KDECONNECT);
        let service = format!("--add-service={KDECONNECT}");
        let remove = format!("--remove-service={KDECONNECT}");
        if flag == PhoneLink::On {
            if !has(&permanent) { self.fw(&["--permanent", "--zone=nizho", &service]).await?; }
            if !has(&runtime) { self.fw(&["--zone=nizho", &service]).await?; }
            return Ok(());
        }
        if !has(&permanent) && !has(&runtime) { return Ok(()); }
        if has(&permanent) { self.fw(&["--permanent", "--zone=nizho", &remove]).await?; }
        if has(&runtime) { self.fw(&["--zone=nizho", &remove]).await?; }
        let msg = if flag == PhoneLink::Unreadable {
            "A rule opening KDE Connect ports was found and the phone linking setting could not be read, so it was removed"
        } else {
            "A rule opening KDE Connect ports was found while phone linking is off, so it was removed"
        };
        (self.log)("firewall", "drift", msg);
        Ok(())
    }

    async fn enforce_zone(&self) -> Result<(), NizhodError> {
        if crate::command::run("systemctl", &["is-active", "--quiet", "firewalld"]).await.is_err() {
            crate::command::run("systemctl", &["start", "firewalld"]).await?;
        }
        let out = self.fw(&["--get-default-zone"]).await?;
        if out.trim() != ZONE {
            self.fw(&["--set-default-zone", ZONE]).await?;
        }
        // Interfaces the user moved to another zone are left alone; they show
        // up as "Needs attention" instead of being silently changed back.
        Ok(())
    }

    async fn check(&self) -> bool {
        let flag = read_phone_link(&self.phone_link_state);
        if flag == PhoneLink::Unreadable { return false; }
        let phone_on = flag == PhoneLink::On;
        // Check runtime rules, not just the zone's name: firewall-config and
        // other applications can add exceptions without changing that name.
        let (zones, rules, direct, passthroughs, policies, dhcp, permanent) = tokio::join!(
            self.fw(&["--get-active-zones"]),
            self.fw(&["--zone=nizho", "--list-all"]),
            self.fw(&["--direct", "--get-all-rules"]),
            self.fw(&["--direct", "--get-all-passthroughs"]),
            self.fw(&["--list-all-policies"]),
            self.fw(&["--info-service=dhcpv6-client"]),
            self.zone_services(true),
        );
        let (Ok(z), Ok(r), Ok(d), Ok(t), Ok(p), Ok(h), Ok(perm)) =
            (zones, rules, direct, passthroughs, policies, dhcp, permanent) else { return false };
        let base = only_zone_active(&z, ZONE)
            && restrictive_zone(&r, phone_on)
            && d.trim().is_empty()
            && t.trim().is_empty()
            && !has_inbound_policy(&p)
            && dhcp_service_is_narrow(&h);
        // The saved (permanent) rule must agree with the flag too, or a
        // firewalld reload would change what is open.
        if !base || perm.iter().any(|s| s == KDECONNECT) != phone_on { return false; }
        if !phone_on { return true; }
        // The service definition can be overridden without changing its name.
        match self.fw(&["--info-service=kdeconnect"]).await {
            Ok(info) => kdeconnect_service_is_narrow(&info),
            Err(_) => false,
        }
    }
}

#[async_trait::async_trait]
impl AsyncEnforcer for FirewallEnforcer {
    fn name(&self) -> &'static str { "firewall" }

    async fn enforce(&self) -> Result<(), NizhodError> {
        self.enforce_zone().await?;
        self.enforce_phone_rule().await
    }

    async fn update_state(&self, state: &SharedState) {
        let on = self.check().await;
        state.write().await.firewall = on;
    }
}

/// `firewall-cmd --get-active-zones` lists each zone name on an unindented
/// line, followed by indented "interfaces:"/"sources:" lines. Configured only
/// when at least one zone is active and every active zone is ours.
fn only_zone_active(text: &str, zone: &str) -> bool {
    let zones: Vec<&str> = text
        .lines()
        .filter(|l| !l.is_empty() && !l.starts_with(char::is_whitespace))
        .map(|l| l.split_whitespace().next().unwrap_or(""))
        .collect();
    !zones.is_empty() && zones.iter().all(|z| *z == zone)
}

// Missing fields or multiline rules are unconfirmed, never a successful check.
// With phone linking on the zone must also hold the kdeconnect service; with
// it off the zone must not. No other service is ever acceptable.
fn restrictive_zone(text: &str, phone_on: bool) -> bool {
    let field = |name: &str| text.lines().find_map(|line| {
        let (key, value) = line.trim().split_once(':')?;
        (key == name).then_some(value.trim())
    });
    field("target") == Some("%%REJECT%%")
        && field("services").is_some_and(|v| {
            let services: Vec<&str> = v.split_whitespace().collect();
            services.iter().all(|s| *s == "dhcpv6-client" || (phone_on && *s == KDECONNECT))
                && services.contains(&KDECONNECT) == phone_on
        })
        && ["ports", "protocols", "source-ports", "forward-ports", "rich rules"]
            .iter().all(|key| field(key) == Some(""))
        && !text.lines().any(|l| l.trim_start().starts_with("rule "))
        && !text.lines().any(|l| l.trim_start().starts_with("port="))
}

// Service definitions can be overridden without changing the service name.
fn dhcp_service_is_narrow(text: &str) -> bool {
    let field = |name: &str| text.lines().find_map(|line| {
        let (key, value) = line.trim().split_once(':')?;
        (key == name).then_some(value.trim())
    });
    field("ports") == Some("546/udp")
        && field("destination") == Some("ipv6:fe80::/64")
        && ["protocols", "source-ports"].iter().all(|key| field(key) == Some(""))
        && ["helpers", "modules", "includes"].iter().all(|key| field(key).unwrap_or("").is_empty())
}

// firewalld's kdeconnect service must be exactly KDE's documented range, tcp
// and udp, with nothing else attached.
fn kdeconnect_service_is_narrow(text: &str) -> bool {
    let field = |name: &str| text.lines().find_map(|line| {
        let (key, value) = line.trim().split_once(':')?;
        (key == name).then_some(value.trim())
    });
    field("ports").is_some_and(|v| {
        let mut ports: Vec<&str> = v.split_whitespace().collect();
        ports.sort_unstable();
        ports == ["1714-1764/tcp", "1714-1764/udp"]
    })
        && ["protocols", "source-ports"].iter().all(|key| field(key) == Some(""))
        && ["destination", "helpers", "modules", "includes"].iter().all(|key| field(key).unwrap_or("").is_empty())
}

// An independent policy can let traffic in around the zone. Disabled policies
// do nothing. An active policy into this computer (egress HOST or ANY) is
// acceptable only if it opens no ports, services or protocols and its rules
// only accept ICMP message types: that is Fedora's allow-host-ipv6, which IPv6
// needs (neighbour discovery, router advertisements). Anything else, or
// anything we can't parse, is unconfirmed.
fn has_inbound_policy(text: &str) -> bool {
    let mut blocks: Vec<Vec<&str>> = Vec::new();
    for line in text.lines().filter(|l| !l.trim().is_empty()) {
        if !line.starts_with(char::is_whitespace) { blocks.push(vec![line]); }
        else if let Some(b) = blocks.last_mut() { b.push(line); }
        else { return true; }
    }
    blocks.iter().any(|b| !policy_is_harmless(b))
}

fn policy_is_harmless(block: &[&str]) -> bool {
    if block[0].contains("(disabled)") { return true; }
    let field = |name: &str| block[1..].iter().find_map(|l| {
        let (k, v) = l.trim().split_once(':')?;
        (k == name).then_some(v.trim())
    });
    let into_host = field("egress-zones").is_none_or(|v| v.split_whitespace().any(|z| z == "HOST" || z == "ANY"));
    let from_host = field("ingress-zones") == Some("HOST");
    if from_host || !into_host { return true; }
    let opens_nothing = ["services", "ports", "protocols", "source-ports", "forward-ports"]
        .iter().all(|k| field(k) == Some(""))
        && field("target").is_some_and(|t| t != "ACCEPT");
    // Judge rich rules by their keywords (whitespace-separated), never by
    // substrings: ICMP type names such as "mld-listener-report" contain "port".
    let only_icmp = block[1..].iter().filter(|l| l.trim_start().starts_with("rule ")).all(|l| {
        let words: Vec<&str> = l.split_whitespace().collect();
        words.last() == Some(&"accept")
            && words.iter().any(|w| *w == "icmp-type")
            && words.iter().all(|w| matches!(w.split('=').next(), Some("rule" | "family" | "icmp-type" | "name" | "accept")))
    });
    opens_nothing && only_icmp
}

#[cfg(test)]
mod tests {
    use super::*;
    const CLOSED: &str = "nizho (active)\n  target: %%REJECT%%\n  services: dhcpv6-client\n  ports: \n  protocols: \n  source-ports: \n  forward-ports: \n  rich rules: \n";
    #[test]
    fn service_overrides_cannot_open_extra_ports() {
        let service = "dhcpv6-client\n  ports: 546/udp\n  protocols: \n  source-ports: \n  helpers: \n  destination: ipv6:fe80::/64\n  includes: \n";
        assert!(dhcp_service_is_narrow(service));
        assert!(!dhcp_service_is_narrow(&service.replace("546/udp", "546/udp 22/tcp")));
        assert!(!dhcp_service_is_narrow(&service.replace("includes: ", "includes: ssh")));
        assert!(!dhcp_service_is_narrow(""));
    }
    #[test]
    fn zone_name_does_not_prove_restrictive_rules() {
        assert!(restrictive_zone(CLOSED, false));
        for (old, new) in [("%%REJECT%%", "ACCEPT"), ("services: dhcpv6-client", "services: dhcpv6-client ssh"), ("ports: ", "ports: 8080/tcp"), ("protocols: ", "protocols: tcp"), ("source-ports: ", "source-ports: 22/tcp")] {
            assert!(!restrictive_zone(&CLOSED.replace(old, new), false));
        }
        assert!(!restrictive_zone(&format!("{CLOSED}    rule family=ipv4 accept\n"), false));
        assert!(!restrictive_zone("nizho\n", false));
    }
    // As printed by `firewall-cmd --list-all-policies` on Fedora 44 (VM, 2026-10-05).
    const FEDORA_POLICIES: &str = "allow-host-ipv6 (active)\n  priority: -15000\n  target: CONTINUE\n  ingress-zones: ANY\n  egress-zones: HOST\n  services: \n  ports: \n  protocols: \n  masquerade: no\n  forward-ports: \n  source-ports: \n  icmp-blocks: \n  rich rules: \n\trule family=\"ipv6\" icmp-type name=\"neighbour-advertisement\" accept\n\trule family=\"ipv6\" icmp-type name=\"neighbour-solicitation\" accept\n\trule family=\"ipv6\" icmp-type name=\"router-advertisement\" accept\n\trule family=\"ipv6\" icmp-type name=\"redirect\" accept\n\
gateway-world-to-HOST (disabled)\n  target: CONTINUE\n  ingress-zones: external public\n  egress-zones: HOST\n  services: ssh\n  ports: \n  protocols: \n  forward-ports: \n  source-ports: \n  rich rules: \n";

    // Captured verbatim from `firewall-cmd --list-all-policies` in an Nizho VM
    // (Fedora 44, 2026-10-11). Includes mld-listener-report, whose name contains
    // "port" and once made the check fail.
    const REAL_F44: &str = include_str!("testdata/fedora44-list-all-policies.txt");

    #[test]
    fn the_real_fedora_44_policy_listing_is_harmless() {
        assert!(REAL_F44.contains("mld-listener-report"));
        assert!(!has_inbound_policy(REAL_F44));
    }

    #[test]
    fn rich_rules_are_judged_by_keyword_not_substring() {
        let rule = |r: &str| REAL_F44.replacen("\trule family=\"ipv6\" icmp-type name=\"redirect\" accept", r, 1);
        for opened in [
            "\trule family=\"ipv4\" port port=\"22\" protocol=\"tcp\" accept",
            "\trule family=\"ipv4\" service name=\"ssh\" accept",
            "\trule family=\"ipv4\" source address=\"10.0.0.0/8\" accept",
            "\trule family=\"ipv6\" icmp-type name=\"echo-request\" source address=\"::/0\" accept",
            "\trule family=\"ipv4\" forward-port port=\"80\" protocol=\"tcp\" to-port=\"8080\" accept",
            "\trule family=\"ipv4\" accept",
        ] {
            assert!(has_inbound_policy(&rule(opened)), "{opened}");
        }
    }

    #[test]
    fn fedoras_ipv6_control_policy_and_disabled_policies_are_harmless() {
        assert!(!has_inbound_policy(FEDORA_POLICIES));
        assert!(!has_inbound_policy(""));
        assert!(!has_inbound_policy("outbound (active)\n  ingress-zones: HOST\n  egress-zones: ANY\n  services: ssh\n"));
    }

    #[test]
    fn an_active_policy_that_opens_anything_inbound_is_unconfirmed() {
        let opened = FEDORA_POLICIES.replace("allow-host-ipv6 (active)\n  priority: -15000\n  target: CONTINUE\n  ingress-zones: ANY\n  egress-zones: HOST\n  services: ",
                                               "allow-host-ipv6 (active)\n  priority: -15000\n  target: CONTINUE\n  ingress-zones: ANY\n  egress-zones: HOST\n  services: ssh");
        assert!(has_inbound_policy(&opened));
        for (old, new) in [("target: CONTINUE", "target: ACCEPT"), ("  ports: \n", "  ports: 22/tcp\n"),
                           ("name=\"redirect\" accept", "name=\"redirect\" accept\n\trule family=\"ipv4\" port port=\"22\" protocol=\"tcp\" accept"),
                           ("gateway-world-to-HOST (disabled)", "gateway-world-to-HOST (active)")] {
            assert!(has_inbound_policy(&FEDORA_POLICIES.replacen(old, new, 1)), "{new}");
        }
        assert!(has_inbound_policy("custom\n"), "unknown shape is unconfirmed");
        assert!(has_inbound_policy("  orphan: line\n"));
    }
    #[test]
    fn every_active_zone_must_be_the_nizho_zone() {
        assert!(only_zone_active("nizho (default)\n  interfaces: wlp2s0\n", "nizho"));
        assert!(!only_zone_active("nizho\n  interfaces: wlp2s0\nhome\n  interfaces: enp3s0\n", "nizho"));
        assert!(!only_zone_active("FedoraWorkstation (default)\n  interfaces: wlp2s0\n", "nizho"));
        assert!(!only_zone_active("", "nizho"));
    }

    // ---- phone linking (KDE Connect) ----
    fn phone_zone(services: &str) -> String { CLOSED.replace("services: dhcpv6-client", &format!("services: dhcpv6-client{services}")) }
    const KDE_INFO: &str = "kdeconnect\n  ports: 1714-1764/tcp 1714-1764/udp\n  protocols: \n  source-ports: \n  helpers: \n  destination: \n  includes: \n";

    #[test]
    fn the_kdeconnect_service_is_only_accepted_while_phone_linking_is_on() {
        assert!(restrictive_zone(&phone_zone(" kdeconnect"), true));
        assert!(!restrictive_zone(&phone_zone(" kdeconnect"), false), "rule found while off");
        assert!(!restrictive_zone(&phone_zone(""), true), "on but the rule is missing");
        assert!(restrictive_zone(&phone_zone(""), false));
        assert!(!restrictive_zone(&phone_zone(" kdeconnect ssh"), true), "nothing else beyond the baseline");
        assert!(!restrictive_zone(&phone_zone(" kdeconnect").replace("ports: \n", "ports: 1714-1764/tcp\n"), true));
        assert!(!restrictive_zone(&phone_zone(" kdeconnect-extra"), true));
    }

    #[test]
    fn the_kdeconnect_definition_must_be_kdes_range_and_nothing_more() {
        assert!(kdeconnect_service_is_narrow(KDE_INFO));
        assert!(kdeconnect_service_is_narrow(&KDE_INFO.replace("1714-1764/tcp 1714-1764/udp", "1714-1764/udp 1714-1764/tcp")));
        assert!(!kdeconnect_service_is_narrow(&KDE_INFO.replace("1764/udp", "1764/udp 22/tcp")));
        assert!(!kdeconnect_service_is_narrow(&KDE_INFO.replace("1714-1764/udp", "1714-65535/udp")));
        assert!(!kdeconnect_service_is_narrow(&KDE_INFO.replace("includes: ", "includes: ssh")));
        assert!(!kdeconnect_service_is_narrow(&KDE_INFO.replace("protocols: ", "protocols: tcp")));
        assert!(!kdeconnect_service_is_narrow(""));
    }

    // A stand-in for firewall-cmd that keeps the nizho zone's runtime and saved
    // services in two files, so enforce and update_state run their real code
    // without touching the machine's firewall.
    mod with_fake_firewall {
        use super::*;
        use std::path::{Path, PathBuf};
        use std::sync::Mutex;

        // A test writes a script and runs it straight away. If another test thread forks in between,
        // the child still holds the script open for writing and the run fails with "Text file busy".
        // One fixture at a time keeps the write and the run apart.
        static FIXTURE_LOCK: Mutex<()> = Mutex::new(());

        struct Fixture {
            dir: PathBuf,
            events: Arc<Mutex<Vec<String>>>,
            enforcer: FirewallEnforcer,
            _serial: std::sync::MutexGuard<'static, ()>,
        }

        impl Fixture {
            fn new(name: &str, runtime: &str, permanent: &str) -> Self {
                let serial = FIXTURE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
                let dir = std::env::temp_dir().join(format!("nizhod-fw-test-{}-{name}", std::process::id()));
                let _ = std::fs::remove_dir_all(&dir);
                std::fs::create_dir_all(&dir).unwrap();
                std::fs::write(dir.join("runtime"), runtime).unwrap();
                std::fs::write(dir.join("permanent"), permanent).unwrap();
                std::fs::write(dir.join("kde-info"), KDE_INFO).unwrap();
                let policies = concat!(env!("CARGO_MANIFEST_DIR"), "/src/enforcers/testdata/fedora44-list-all-policies.txt");
                let script = format!(r#"#!/bin/sh
d='{dir}'
echo "$*" >> "$d/calls"
list="$d/runtime"
for a in "$@"; do [ "$a" = --permanent ] && list="$d/permanent"; done
case "$*" in
  *--get-default-zone*) echo nizho ;;
  *--get-active-zones*) printf 'nizho (default)\n  interfaces: wlp2s0\n' ;;
  *--list-services*) cat "$list" ;;
  *--zone=nizho\ --list-all*) printf 'nizho (active)\n  target: %%%%REJECT%%%%\n  services: dhcpv6-client %s\n  ports: \n  protocols: \n  source-ports: \n  forward-ports: \n  rich rules: \n' "$(cat "$d/runtime")" ;;
  *--direct*) ;;
  *--list-all-policies*) cat '{policies}' ;;
  *--info-service=dhcpv6-client*) printf 'dhcpv6-client\n  ports: 546/udp\n  protocols: \n  source-ports: \n  helpers: \n  destination: ipv6:fe80::/64\n  includes: \n' ;;
  *--info-service=kdeconnect*) cat "$d/kde-info" ;;
  *--add-service=kdeconnect*) echo kdeconnect >> "$list" ;;
  *--remove-service=kdeconnect*) grep -vx kdeconnect "$list" > "$list.new"; mv "$list.new" "$list" ;;
  *) exit 1 ;;
esac
"#, dir = dir.display(), policies = policies);
                let cmd = dir.join("firewall-cmd");
                std::fs::write(&cmd, script).unwrap();
                std::fs::set_permissions(&cmd, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
                let events = Arc::new(Mutex::new(Vec::new()));
                let sink = events.clone();
                let log: EventLog = Arc::new(move |cat, ev, msg| sink.lock().unwrap().push(format!("{cat}/{ev}: {msg}")));
                let enforcer = FirewallEnforcer::with_parts(cmd.to_str().unwrap(), dir.join("phone-link").to_str().unwrap(), log);
                Self { dir, events, enforcer, _serial: serial }
            }
            fn flag(&self, content: &str) { std::fs::write(self.dir.join("phone-link"), content).unwrap(); }
            fn list(&self, which: &str) -> Vec<String> {
                std::fs::read_to_string(self.dir.join(which)).unwrap().split_whitespace().map(str::to_owned).collect()
            }
            async fn confirmed(&self) -> bool { self.enforcer.check().await }
            fn events(&self) -> Vec<String> { self.events.lock().unwrap().clone() }
        }
        impl Drop for Fixture { fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.dir); } }

        #[tokio::test]
        async fn flag_on_and_service_present_is_confirmed_and_left_alone() {
            let f = Fixture::new("on-present", "kdeconnect\n", "kdeconnect\n");
            f.flag("on\n");
            assert!(f.confirmed().await);
            f.enforcer.enforce_phone_rule().await.unwrap();
            assert_eq!(f.list("runtime"), ["kdeconnect"]);
            assert!(f.events().is_empty());
        }

        #[tokio::test]
        async fn flag_on_and_service_missing_is_not_confirmed_and_is_added() {
            let f = Fixture::new("on-missing", "", "");
            f.flag("on");
            assert!(!f.confirmed().await);
            f.enforcer.enforce_phone_rule().await.unwrap();
            assert_eq!(f.list("runtime"), ["kdeconnect"]);
            assert_eq!(f.list("permanent"), ["kdeconnect"]);
            assert!(f.confirmed().await);
        }

        #[tokio::test]
        async fn flag_on_with_only_the_saved_rule_missing_is_repaired() {
            let f = Fixture::new("on-runtime-only", "kdeconnect\n", "");
            f.flag("on");
            assert!(!f.confirmed().await);
            f.enforcer.enforce_phone_rule().await.unwrap();
            assert_eq!(f.list("permanent"), ["kdeconnect"]);
            assert_eq!(f.list("runtime"), ["kdeconnect"], "not added twice");
        }

        #[tokio::test]
        async fn flag_on_with_a_widened_service_definition_is_not_confirmed() {
            let f = Fixture::new("on-widened", "kdeconnect\n", "kdeconnect\n");
            f.flag("on");
            std::fs::write(f.dir.join("kde-info"), KDE_INFO.replace("1714-1764/udp", "1-65535/udp")).unwrap();
            assert!(!f.confirmed().await);
        }

        #[tokio::test]
        async fn flag_off_and_service_present_is_drift_reported_and_removed() {
            let f = Fixture::new("off-present", "kdeconnect\n", "kdeconnect\n");
            f.flag("off");
            assert!(!f.confirmed().await);
            f.enforcer.enforce_phone_rule().await.unwrap();
            assert!(f.list("runtime").is_empty());
            assert!(f.list("permanent").is_empty());
            assert_eq!(f.events(), ["firewall/drift: A rule opening KDE Connect ports was found while phone linking is off, so it was removed"]);
            assert!(f.confirmed().await);
        }

        #[tokio::test]
        async fn flag_off_and_service_absent_is_confirmed_without_changes_or_events() {
            let f = Fixture::new("off-absent", "", "");
            // no flag file at all
            assert!(f.confirmed().await);
            f.enforcer.enforce_phone_rule().await.unwrap();
            assert!(f.events().is_empty());
            let calls = std::fs::read_to_string(f.dir.join("calls")).unwrap();
            assert!(!calls.contains("--add-service") && !calls.contains("--remove-service"));
        }

        #[tokio::test]
        async fn a_saved_rule_alone_is_drift_while_off() {
            let f = Fixture::new("off-permanent", "", "kdeconnect\n");
            assert!(!f.confirmed().await);
            f.enforcer.enforce_phone_rule().await.unwrap();
            assert!(f.list("permanent").is_empty());
            assert_eq!(f.events().len(), 1);
        }

        #[tokio::test]
        async fn a_flag_file_with_garbage_counts_as_off() {
            for garbage in ["", "yes", "ON", "on please", "\u{0}\u{1}garbage", "off"] {
                let f = Fixture::new("garbage", "kdeconnect\n", "kdeconnect\n");
                f.flag(garbage);
                assert!(!f.confirmed().await, "{garbage:?}");
                f.enforcer.enforce_phone_rule().await.unwrap();
                assert!(f.list("runtime").is_empty(), "{garbage:?}");
            }
            let f = Fixture::new("invalid-utf8", "", "");
            std::fs::write(f.dir.join("phone-link"), [0xff, 0xfe, 0x6f]).unwrap();
            assert_eq!(read_phone_link(&f.dir.join("phone-link")), PhoneLink::Off);
            assert!(f.confirmed().await);
        }

        #[tokio::test]
        async fn an_unreadable_flag_is_never_confirmed_and_closes_the_rule() {
            // A directory where the file should be: it exists but cannot be read as a file.
            let f = Fixture::new("unreadable", "kdeconnect\n", "kdeconnect\n");
            std::fs::create_dir(f.dir.join("phone-link")).unwrap();
            assert_eq!(read_phone_link(&f.dir.join("phone-link")), PhoneLink::Unreadable);
            assert!(!f.confirmed().await);
            f.enforcer.enforce_phone_rule().await.unwrap();
            assert!(f.list("runtime").is_empty());
            assert!(f.events()[0].contains("could not be read"));
            assert!(!f.confirmed().await, "consent is still unknown, so the check stays unconfirmed");
        }

        #[tokio::test]
        async fn a_missing_file_is_off_and_its_path_comes_from_the_config() {
            assert_eq!(read_phone_link(Path::new("/nonexistent/nizho/phone-link")), PhoneLink::Off);
            let cfg = Config::default();
            assert_eq!(FirewallEnforcer::new(&cfg).phone_link_state, PathBuf::from("/etc/nizho/phone-link"));
        }
    }
}
