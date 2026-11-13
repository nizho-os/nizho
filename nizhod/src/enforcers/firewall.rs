use std::path::{Path, PathBuf};
use std::sync::Arc;

use crate::config::Config;
use crate::error::NizhodError;
use crate::state::SharedState;
use super::AsyncEnforcer;

/// The Nizho zone (shipped in /usr/lib/firewalld/zones/nizho.xml): rejects
/// unsolicited incoming connections; replies to the computer's own
/// connections are still allowed. Every network the user has not marked as
/// home uses it, so it never holds an exception.
const ZONE: &str = "nizho";

/// The zone of networks the user marked as home (NetworkManager sets
/// connection.zone; see nizho-home-network). The same rules as `nizho`, plus
/// the exceptions below while the user has turned them on.
const HOME: &str = "nizho-home";

/// An incoming opening the user can allow, on home networks only.
struct Exception {
    /// firewalld's service name.
    service: &'static str,
    /// What that service must be defined as; a widened definition is unconfirmed.
    ports: &'static [&'static str],
    /// Holds the word "on" while the user allows it (written by the helper).
    flag: PathBuf,
    /// Event text when the rule is found while the setting is off.
    found_while_off: &'static str,
    /// Event text when the rule is found and the setting could not be read.
    found_unreadable: &'static str,
    /// Event text when the rule is found on networks that are not home.
    found_away: &'static str,
}

/// firewalld's own service for KDE Connect: 1714-1764 tcp and udp.
const KDECONNECT: &str = "kdeconnect";
const KDECONNECT_PORTS: &[&str] = &["1714-1764/tcp", "1714-1764/udp"];
/// firewalld's own service for the SSH server.
const SSH: &str = "ssh";
const SSH_PORTS: &[&str] = &["22/tcp"];

type EventLog = Arc<dyn Fn(&str, &str, &str) + Send + Sync>;

pub struct FirewallEnforcer {
    firewall_cmd: String,
    exceptions: Vec<Exception>,
    log: EventLog,
}

/// What a flag file says. Only the word `on` (surrounding whitespace
/// ignored) allows the exception; a missing file or any other content is
/// off. A file that exists but cannot be read leaves consent unknown, which
/// is treated as off for enforcement and is never reported as confirmed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Flag { On, Off, Unreadable }

fn read_flag(path: &Path) -> Flag {
    match std::fs::read(path) {
        Ok(bytes) if String::from_utf8_lossy(&bytes).trim() == "on" => Flag::On,
        Ok(_) => Flag::Off,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Flag::Off,
        Err(_) => Flag::Unreadable,
    }
}

impl FirewallEnforcer {
    pub fn new(cfg: &Config) -> Self {
        Self::with_parts("firewall-cmd", &cfg.phone_link.state_path, &cfg.remote_login.state_path,
                         Arc::new(crate::log::log_event))
    }

    fn with_parts(firewall_cmd: &str, phone_flag: &str, ssh_flag: &str, log: EventLog) -> Self {
        let exceptions = vec![
            Exception {
                service: KDECONNECT,
                ports: KDECONNECT_PORTS,
                flag: phone_flag.into(),
                found_while_off: "A rule opening KDE Connect ports was found while phone linking is off, so it was removed",
                found_unreadable: "A rule opening KDE Connect ports was found and the phone linking setting could not be read, so it was removed",
                found_away: "A rule opening KDE Connect ports was found on networks that are not marked as home, so it was removed",
            },
            Exception {
                service: SSH,
                ports: SSH_PORTS,
                flag: ssh_flag.into(),
                found_while_off: "A rule opening the remote login port was found while remote login is off, so it was removed",
                found_unreadable: "A rule opening the remote login port was found and the remote login setting could not be read, so it was removed",
                found_away: "A rule opening the remote login port was found on networks that are not marked as home, so it was removed",
            },
        ];
        Self { firewall_cmd: firewall_cmd.into(), exceptions, log }
    }

    async fn fw(&self, args: &[&str]) -> Result<String, NizhodError> {
        let out = crate::command::run_patient(&self.firewall_cmd, args).await?;
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    }

    /// Services of a zone, runtime or permanent configuration.
    async fn zone_services(&self, zone: &str, permanent: bool) -> Result<Vec<String>, NizhodError> {
        let z = format!("--zone={zone}");
        let mut args = vec![z.as_str(), "--list-services"];
        if permanent { args.insert(0, "--permanent"); }
        Ok(self.fw(&args).await?.split_whitespace().map(str::to_owned).collect())
    }

    /// Removes a service from a zone (runtime and permanent). True if it was there.
    async fn remove_from(&self, zone: &str, service: &str) -> Result<bool, NizhodError> {
        let z = format!("--zone={zone}");
        let remove = format!("--remove-service={service}");
        let runtime = self.zone_services(zone, false).await?.iter().any(|s| s == service);
        let permanent = self.zone_services(zone, true).await?.iter().any(|s| s == service);
        if permanent { self.fw(&["--permanent", &z, &remove]).await?; }
        if runtime { self.fw(&[&z, &remove]).await?; }
        Ok(runtime || permanent)
    }

    /// Makes each exception match its flag: present in the home zone (runtime
    /// and permanent) while on, absent otherwise, and never in the default
    /// zone. A rule found where the flag does not allow it is drift (for
    /// example added by hand with firewall-cmd, or left by an older Nizho that
    /// opened phone linking on every network) and is removed with a plain event.
    async fn enforce_exceptions(&self) -> Result<(), NizhodError> {
        for x in &self.exceptions {
            if self.remove_from(ZONE, x.service).await? {
                (self.log)("firewall", "drift", x.found_away);
            }
            let flag = read_flag(&x.flag);
            if flag == Flag::On {
                let home = format!("--zone={HOME}");
                let add = format!("--add-service={}", x.service);
                if !self.zone_services(HOME, true).await?.iter().any(|s| s == x.service) {
                    self.fw(&["--permanent", &home, &add]).await?;
                }
                if !self.zone_services(HOME, false).await?.iter().any(|s| s == x.service) {
                    self.fw(&[&home, &add]).await?;
                }
            } else if self.remove_from(HOME, x.service).await? {
                let msg = if flag == Flag::Unreadable { x.found_unreadable } else { x.found_while_off };
                (self.log)("firewall", "drift", msg);
            }
        }
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

    /// The services allowed in the home zone right now, or None while any
    /// setting cannot be read (consent unknown).
    fn allowed_at_home(&self) -> Option<Vec<&'static str>> {
        let mut on = Vec::new();
        for x in &self.exceptions {
            match read_flag(&x.flag) {
                Flag::Unreadable => return None,
                Flag::On => on.push(x.service),
                Flag::Off => {}
            }
        }
        Some(on)
    }

    async fn check(&self) -> bool {
        let Some(allowed) = self.allowed_at_home() else { return false };
        // Check runtime rules, not just the zones' names: firewall-config and
        // other applications can add exceptions without changing the names.
        let home = format!("--zone={HOME}");
        let home_list_all = [home.as_str(), "--list-all"];
        let (zones, rules, home_rules, direct, passthroughs, policies, dhcp, perm, perm_home) = tokio::join!(
            self.fw(&["--get-active-zones"]),
            self.fw(&["--zone=nizho", "--list-all"]),
            self.fw(&home_list_all),
            self.fw(&["--direct", "--get-all-rules"]),
            self.fw(&["--direct", "--get-all-passthroughs"]),
            self.fw(&["--list-all-policies"]),
            self.fw(&["--info-service=dhcpv6-client"]),
            self.zone_services(ZONE, true),
            self.zone_services(HOME, true),
        );
        let (Ok(z), Ok(r), Ok(rh), Ok(d), Ok(t), Ok(p), Ok(h), Ok(perm), Ok(perm_home)) =
            (zones, rules, home_rules, direct, passthroughs, policies, dhcp, perm, perm_home) else { return false };
        let base = only_zones_active(&z, &[ZONE, HOME])
            && restrictive_zone(&r, &[])
            && restrictive_zone(&rh, &allowed)
            && d.trim().is_empty()
            && t.trim().is_empty()
            && !has_inbound_policy(&p)
            && dhcp_service_is_narrow(&h);
        if !base { return false; }
        // The saved (permanent) rules must agree too, or a firewalld reload
        // would change what is open.
        for x in &self.exceptions {
            if perm.iter().any(|s| s == x.service) { return false; }
            if perm_home.iter().any(|s| s == x.service) != allowed.contains(&x.service) { return false; }
        }
        // A service definition can be overridden without changing its name.
        for x in self.exceptions.iter().filter(|x| allowed.contains(&x.service)) {
            match self.fw(&[&format!("--info-service={}", x.service)]).await {
                Ok(info) if service_is_narrow(&info, x.ports) => {}
                _ => return false,
            }
        }
        true
    }
}

#[async_trait::async_trait]
impl AsyncEnforcer for FirewallEnforcer {
    fn name(&self) -> &'static str { "firewall" }

    async fn enforce(&self) -> Result<(), NizhodError> {
        self.enforce_zone().await?;
        self.enforce_exceptions().await
    }

    async fn update_state(&self, state: &SharedState) {
        let on = self.check().await;
        let at_home = self.allowed_at_home().unwrap_or_default();
        let mut s = state.write().await;
        s.firewall = on;
        s.phone_link_at_home = on && at_home.contains(&KDECONNECT);
        s.remote_login_at_home = on && at_home.contains(&SSH);
    }
}

/// `firewall-cmd --get-active-zones` lists each zone name on an unindented
/// line, followed by indented "interfaces:"/"sources:" lines. Configured only
/// when at least one zone is active and every active zone is one of ours.
fn only_zones_active(text: &str, ours: &[&str]) -> bool {
    let zones: Vec<&str> = text
        .lines()
        .filter(|l| !l.is_empty() && !l.starts_with(char::is_whitespace))
        .map(|l| l.split_whitespace().next().unwrap_or(""))
        .collect();
    !zones.is_empty() && zones.iter().all(|z| ours.contains(z))
}

// Missing fields or multiline rules are unconfirmed, never a successful check.
// The zone must hold dhcpv6-client and exactly the allowed exceptions; no
// other service is ever acceptable.
fn restrictive_zone(text: &str, allowed: &[&str]) -> bool {
    let field = |name: &str| text.lines().find_map(|line| {
        let (key, value) = line.trim().split_once(':')?;
        (key == name).then_some(value.trim())
    });
    field("target") == Some("%%REJECT%%")
        && field("services").is_some_and(|v| {
            let services: Vec<&str> = v.split_whitespace().collect();
            services.iter().all(|s| *s == "dhcpv6-client" || allowed.contains(s))
                && allowed.iter().all(|a| services.contains(a))
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

// An exception's service must be exactly its documented ports, with nothing
// else attached.
fn service_is_narrow(text: &str, expected: &[&str]) -> bool {
    let field = |name: &str| text.lines().find_map(|line| {
        let (key, value) = line.trim().split_once(':')?;
        (key == name).then_some(value.trim())
    });
    field("ports").is_some_and(|v| {
        let mut ports: Vec<&str> = v.split_whitespace().collect();
        ports.sort_unstable();
        let mut want = expected.to_vec();
        want.sort_unstable();
        ports == want
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
        assert!(restrictive_zone(CLOSED, &[]));
        for (old, new) in [("%%REJECT%%", "ACCEPT"), ("services: dhcpv6-client", "services: dhcpv6-client ssh"), ("ports: ", "ports: 8080/tcp"), ("protocols: ", "protocols: tcp"), ("source-ports: ", "source-ports: 22/tcp")] {
            assert!(!restrictive_zone(&CLOSED.replace(old, new), &[]));
        }
        assert!(!restrictive_zone(&format!("{CLOSED}    rule family=ipv4 accept\n"), &[]));
        assert!(!restrictive_zone("nizho\n", &[]));
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
        assert!(only_zones_active("nizho (default)\n  interfaces: wlp2s0\n", &["nizho", "nizho-home"]));
        assert!(!only_zones_active("nizho\n  interfaces: wlp2s0\nhome\n  interfaces: enp3s0\n", &["nizho", "nizho-home"]));
        assert!(!only_zones_active("FedoraWorkstation (default)\n  interfaces: wlp2s0\n", &["nizho", "nizho-home"]));
        assert!(!only_zones_active("", &["nizho", "nizho-home"]));
        assert!(only_zones_active("nizho (default)\n  interfaces: wlp2s0\nnizho-home\n  interfaces: enp3s0\n", &["nizho", "nizho-home"]), "a home network is ours too");
    }

    // ---- exceptions: phone linking (KDE Connect) and remote login (SSH) ----
    fn zone_with(services: &str) -> String { CLOSED.replace("services: dhcpv6-client", &format!("services: dhcpv6-client{services}")) }
    const KDE_INFO: &str = "kdeconnect\n  ports: 1714-1764/tcp 1714-1764/udp\n  protocols: \n  source-ports: \n  helpers: \n  destination: \n  includes: \n";
    const SSH_INFO: &str = "ssh\n  ports: 22/tcp\n  protocols: \n  source-ports: \n  helpers: \n  destination: \n  includes: \n";

    #[test]
    fn a_zone_holds_exactly_the_allowed_exceptions() {
        assert!(restrictive_zone(&zone_with(" kdeconnect"), &[KDECONNECT]));
        assert!(!restrictive_zone(&zone_with(" kdeconnect"), &[]), "rule found while off");
        assert!(!restrictive_zone(&zone_with(""), &[KDECONNECT]), "on but the rule is missing");
        assert!(restrictive_zone(&zone_with(""), &[]));
        assert!(!restrictive_zone(&zone_with(" kdeconnect ssh"), &[KDECONNECT]), "nothing else beyond the baseline");
        assert!(restrictive_zone(&zone_with(" kdeconnect ssh"), &[KDECONNECT, SSH]));
        assert!(restrictive_zone(&zone_with(" ssh"), &[SSH]));
        assert!(!restrictive_zone(&zone_with(" ssh"), &[KDECONNECT, SSH]));
        assert!(!restrictive_zone(&zone_with(" kdeconnect").replace("ports: \n", "ports: 1714-1764/tcp\n"), &[KDECONNECT]));
        assert!(!restrictive_zone(&zone_with(" kdeconnect-extra"), &[KDECONNECT]));
    }

    #[test]
    fn an_exception_definition_must_be_its_documented_ports_and_nothing_more() {
        assert!(service_is_narrow(KDE_INFO, KDECONNECT_PORTS));
        assert!(service_is_narrow(&KDE_INFO.replace("1714-1764/tcp 1714-1764/udp", "1714-1764/udp 1714-1764/tcp"), KDECONNECT_PORTS));
        assert!(!service_is_narrow(&KDE_INFO.replace("1764/udp", "1764/udp 22/tcp"), KDECONNECT_PORTS));
        assert!(!service_is_narrow(&KDE_INFO.replace("1714-1764/udp", "1714-65535/udp"), KDECONNECT_PORTS));
        assert!(!service_is_narrow(&KDE_INFO.replace("includes: ", "includes: ssh"), KDECONNECT_PORTS));
        assert!(!service_is_narrow(&KDE_INFO.replace("protocols: ", "protocols: tcp"), KDECONNECT_PORTS));
        assert!(!service_is_narrow("", KDECONNECT_PORTS));
        assert!(service_is_narrow(SSH_INFO, SSH_PORTS));
        assert!(!service_is_narrow(&SSH_INFO.replace("22/tcp", "22/tcp 2222/tcp"), SSH_PORTS));
        assert!(!service_is_narrow(&SSH_INFO.replace("destination: ", "destination: ipv4:0.0.0.0/0"), SSH_PORTS));
    }

    // A stand-in for firewall-cmd that keeps each zone's runtime and saved
    // services in files, so enforce and update_state run their real code
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
            /// Each of the four lists is the services beyond dhcpv6-client, one per line.
            fn new(name: &str, home_runtime: &str, home_permanent: &str) -> Self {
                Self::zones(name, "", "", home_runtime, home_permanent)
            }
            fn zones(name: &str, runtime: &str, permanent: &str, home_runtime: &str, home_permanent: &str) -> Self {
                let serial = FIXTURE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
                let dir = std::env::temp_dir().join(format!("nizhod-fw-test-{}-{name}", std::process::id()));
                let _ = std::fs::remove_dir_all(&dir);
                std::fs::create_dir_all(&dir).unwrap();
                for (file, text) in [("nizho-runtime", runtime), ("nizho-permanent", permanent),
                                     ("nizho-home-runtime", home_runtime), ("nizho-home-permanent", home_permanent),
                                     ("kdeconnect-info", KDE_INFO), ("ssh-info", SSH_INFO)] {
                    std::fs::write(dir.join(file), text).unwrap();
                }
                let policies = concat!(env!("CARGO_MANIFEST_DIR"), "/src/enforcers/testdata/fedora44-list-all-policies.txt");
                let script = format!(r#"#!/bin/sh
d='{dir}'
echo "$*" >> "$d/calls"
zone=nizho; kind=runtime
for a in "$@"; do
  case "$a" in --permanent) kind=permanent ;; --zone=*) zone=${{a#--zone=}} ;; esac
done
list="$d/$zone-$kind"
case "$*" in
  *--get-default-zone*) echo nizho ;;
  *--get-active-zones*) printf 'nizho (default)\n  interfaces: wlp2s0\n' ;;
  *--list-services*) cat "$list" ;;
  *--list-all-policies*) cat '{policies}' ;;
  *--list-all*) printf '%s (active)\n  target: %%%%REJECT%%%%\n  services: dhcpv6-client %s\n  ports: \n  protocols: \n  source-ports: \n  forward-ports: \n  rich rules: \n' "$zone" "$(tr "\n" " " < "$d/$zone-runtime")" ;;
  *--direct*) ;;
  *--info-service=dhcpv6-client*) printf 'dhcpv6-client\n  ports: 546/udp\n  protocols: \n  source-ports: \n  helpers: \n  destination: ipv6:fe80::/64\n  includes: \n' ;;
  *--info-service=*) s=${{1#--info-service=}}; cat "$d/$s-info" ;;
  *--add-service=*) for a in "$@"; do case "$a" in --add-service=*) echo "${{a#--add-service=}}" >> "$list" ;; esac; done ;;
  *--remove-service=*) for a in "$@"; do case "$a" in --remove-service=*) grep -vx "${{a#--remove-service=}}" "$list" > "$list.new"; mv "$list.new" "$list" ;; esac; done ;;
  *) exit 1 ;;
esac
"#, dir = dir.display(), policies = policies);
                let cmd = dir.join("firewall-cmd");
                std::fs::write(&cmd, script).unwrap();
                std::fs::set_permissions(&cmd, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
                let events = Arc::new(Mutex::new(Vec::new()));
                let sink = events.clone();
                let log: EventLog = Arc::new(move |cat, ev, msg| sink.lock().unwrap().push(format!("{cat}/{ev}: {msg}")));
                let enforcer = FirewallEnforcer::with_parts(cmd.to_str().unwrap(), dir.join("phone-link").to_str().unwrap(),
                                                            dir.join("remote-login").to_str().unwrap(), log);
                Self { dir, events, enforcer, _serial: serial }
            }
            fn flag(&self, content: &str) { std::fs::write(self.dir.join("phone-link"), content).unwrap(); }
            fn ssh_flag(&self, content: &str) { std::fs::write(self.dir.join("remote-login"), content).unwrap(); }
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
            f.enforcer.enforce_exceptions().await.unwrap();
            assert_eq!(f.list("nizho-home-runtime"), ["kdeconnect"]);
            assert!(f.events().is_empty());
        }

        #[tokio::test]
        async fn flag_on_and_service_missing_is_not_confirmed_and_is_added_at_home_only() {
            let f = Fixture::new("on-missing", "", "");
            f.flag("on");
            assert!(!f.confirmed().await);
            f.enforcer.enforce_exceptions().await.unwrap();
            assert_eq!(f.list("nizho-home-runtime"), ["kdeconnect"]);
            assert_eq!(f.list("nizho-home-permanent"), ["kdeconnect"]);
            assert!(f.list("nizho-runtime").is_empty() && f.list("nizho-permanent").is_empty());
            assert!(f.confirmed().await);
        }

        #[tokio::test]
        async fn flag_on_with_only_the_saved_rule_missing_is_repaired() {
            let f = Fixture::new("on-runtime-only", "kdeconnect\n", "");
            f.flag("on");
            assert!(!f.confirmed().await);
            f.enforcer.enforce_exceptions().await.unwrap();
            assert_eq!(f.list("nizho-home-permanent"), ["kdeconnect"]);
            assert_eq!(f.list("nizho-home-runtime"), ["kdeconnect"], "not added twice");
        }

        #[tokio::test]
        async fn flag_on_with_a_widened_service_definition_is_not_confirmed() {
            let f = Fixture::new("on-widened", "kdeconnect\n", "kdeconnect\n");
            f.flag("on");
            std::fs::write(f.dir.join("kdeconnect-info"), KDE_INFO.replace("1714-1764/udp", "1-65535/udp")).unwrap();
            assert!(!f.confirmed().await);
        }

        #[tokio::test]
        async fn flag_off_and_service_present_is_drift_reported_and_removed() {
            let f = Fixture::new("off-present", "kdeconnect\n", "kdeconnect\n");
            f.flag("off");
            assert!(!f.confirmed().await);
            f.enforcer.enforce_exceptions().await.unwrap();
            assert!(f.list("nizho-home-runtime").is_empty());
            assert!(f.list("nizho-home-permanent").is_empty());
            assert_eq!(f.events(), ["firewall/drift: A rule opening KDE Connect ports was found while phone linking is off, so it was removed"]);
            assert!(f.confirmed().await);
        }

        #[tokio::test]
        async fn flag_off_and_service_absent_is_confirmed_without_changes_or_events() {
            let f = Fixture::new("off-absent", "", "");
            // no flag files at all
            assert!(f.confirmed().await);
            f.enforcer.enforce_exceptions().await.unwrap();
            assert!(f.events().is_empty());
            let calls = std::fs::read_to_string(f.dir.join("calls")).unwrap();
            assert!(!calls.contains("--add-service") && !calls.contains("--remove-service"));
        }

        #[tokio::test]
        async fn a_saved_rule_alone_is_drift_while_off() {
            let f = Fixture::new("off-permanent", "", "kdeconnect\n");
            assert!(!f.confirmed().await);
            f.enforcer.enforce_exceptions().await.unwrap();
            assert!(f.list("nizho-home-permanent").is_empty());
            assert_eq!(f.events().len(), 1);
        }

        #[tokio::test]
        async fn a_flag_file_with_garbage_counts_as_off() {
            for garbage in ["", "yes", "ON", "on please", "\u{0}\u{1}garbage", "off"] {
                let f = Fixture::new("garbage", "kdeconnect\n", "kdeconnect\n");
                f.flag(garbage);
                assert!(!f.confirmed().await, "{garbage:?}");
                f.enforcer.enforce_exceptions().await.unwrap();
                assert!(f.list("nizho-home-runtime").is_empty(), "{garbage:?}");
            }
            let f = Fixture::new("invalid-utf8", "", "");
            std::fs::write(f.dir.join("phone-link"), [0xff, 0xfe, 0x6f]).unwrap();
            assert_eq!(read_flag(&f.dir.join("phone-link")), Flag::Off);
            assert!(f.confirmed().await);
        }

        #[tokio::test]
        async fn an_unreadable_flag_is_never_confirmed_and_closes_the_rule() {
            // A directory where the file should be: it exists but cannot be read as a file.
            let f = Fixture::new("unreadable", "kdeconnect\n", "kdeconnect\n");
            std::fs::create_dir(f.dir.join("phone-link")).unwrap();
            assert_eq!(read_flag(&f.dir.join("phone-link")), Flag::Unreadable);
            assert!(!f.confirmed().await);
            f.enforcer.enforce_exceptions().await.unwrap();
            assert!(f.list("nizho-home-runtime").is_empty());
            assert!(f.events()[0].contains("could not be read"));
            assert!(!f.confirmed().await, "consent is still unknown, so the check stays unconfirmed");
        }

        #[tokio::test]
        async fn flag_paths_come_from_the_config() {
            assert_eq!(read_flag(Path::new("/nonexistent/nizho/phone-link")), Flag::Off);
            let cfg = Config::default();
            let e = FirewallEnforcer::new(&cfg);
            assert_eq!(e.exceptions[0].flag, PathBuf::from("/etc/nizho/phone-link"));
            assert_eq!(e.exceptions[1].flag, PathBuf::from("/etc/nizho/remote-login"));
        }

        // ---- home networks only ----

        #[tokio::test]
        async fn an_exception_in_the_default_zone_is_never_confirmed_even_while_on() {
            // What an older Nizho left behind: phone linking on, rule in zone nizho (every network).
            let f = Fixture::zones("away", "kdeconnect\n", "kdeconnect\n", "", "");
            f.flag("on");
            assert!(!f.confirmed().await);
            f.enforcer.enforce_exceptions().await.unwrap();
            assert!(f.list("nizho-runtime").is_empty() && f.list("nizho-permanent").is_empty(), "moved off other networks");
            assert_eq!(f.list("nizho-home-runtime"), ["kdeconnect"], "and kept for home networks");
            assert_eq!(f.list("nizho-home-permanent"), ["kdeconnect"]);
            assert_eq!(f.events(), ["firewall/drift: A rule opening KDE Connect ports was found on networks that are not marked as home, so it was removed"]);
            assert!(f.confirmed().await);
        }

        #[tokio::test]
        async fn a_saved_rule_in_the_default_zone_alone_is_unconfirmed() {
            let f = Fixture::zones("away-saved", "", "kdeconnect\n", "kdeconnect\n", "kdeconnect\n");
            f.flag("on");
            assert!(!f.confirmed().await);
        }

        #[tokio::test]
        async fn remote_login_opens_ssh_at_home_only_and_closes_it_when_off() {
            let f = Fixture::new("ssh-on", "", "");
            f.ssh_flag("on");
            f.enforcer.enforce_exceptions().await.unwrap();
            assert_eq!(f.list("nizho-home-runtime"), ["ssh"]);
            assert_eq!(f.list("nizho-home-permanent"), ["ssh"]);
            assert!(f.list("nizho-runtime").is_empty());
            assert!(f.confirmed().await);
            f.ssh_flag("off");
            assert!(!f.confirmed().await);
            f.enforcer.enforce_exceptions().await.unwrap();
            assert!(f.list("nizho-home-runtime").is_empty());
            assert_eq!(f.events(), ["firewall/drift: A rule opening the remote login port was found while remote login is off, so it was removed"]);
            assert!(f.confirmed().await);
        }

        #[tokio::test]
        async fn both_exceptions_on_together() {
            let f = Fixture::new("both", "", "");
            f.flag("on");
            f.ssh_flag("on");
            f.enforcer.enforce_exceptions().await.unwrap();
            let mut home = f.list("nizho-home-runtime");
            home.sort();
            assert_eq!(home, ["kdeconnect", "ssh"]);
            assert!(f.confirmed().await);
            std::fs::write(f.dir.join("ssh-info"), SSH_INFO.replace("22/tcp", "22/tcp 23/tcp")).unwrap();
            assert!(!f.confirmed().await, "a widened ssh definition is unconfirmed");
        }

        #[tokio::test]
        async fn ssh_in_the_default_zone_is_removed_even_while_remote_login_is_on() {
            let f = Fixture::zones("ssh-away", "ssh\n", "ssh\n", "ssh\n", "ssh\n");
            f.ssh_flag("on");
            assert!(!f.confirmed().await);
            f.enforcer.enforce_exceptions().await.unwrap();
            assert!(f.list("nizho-runtime").is_empty() && f.list("nizho-permanent").is_empty());
            assert_eq!(f.list("nizho-home-runtime"), ["ssh"]);
            assert!(f.confirmed().await);
        }

        #[tokio::test]
        async fn state_says_which_exceptions_are_on_only_when_confirmed() {
            let f = Fixture::new("state", "", "");
            f.flag("on");
            let state: SharedState = Default::default();
            f.enforcer.update_state(&state).await;
            assert!(!state.read().await.phone_link_at_home, "not confirmed yet");
            f.enforcer.enforce_exceptions().await.unwrap();
            f.enforcer.update_state(&state).await;
            let s = state.read().await;
            assert!(s.firewall && s.phone_link_at_home && !s.remote_login_at_home);
        }
    }
}
