use std::sync::Arc;
use tokio::sync::RwLock;
use nizho_shell_common::{BrowserSandbox, DnsStatus, SandboxStatus};

pub type SharedState = Arc<RwLock<NizhodState>>;

impl NizhodState {
    pub fn control_healthy(&self, name: &str) -> bool {
        match name {
            "mac" => self.mac_randomization,
            // A per-network exception the user chose isn't a failure: there is
            // nothing to re-apply, and retrying would log a "drift" every cycle.
            // The dashboard and tray still show DNS as unencrypted there.
            "dns" => self.dns_status.is_encrypted() || self.dns_status == DnsStatus::ForcedPlaintext,
            "hostname" => self.hostname_privacy,
            "ipv6" => self.ipv6_privacy,
            "firewall" => self.firewall,
            "sandbox" => self.sandbox_status.is_active() && self.browser_sandbox.is_isolated(),
            _ => false,
        }
    }

    pub fn invalidate(&mut self, name: &str) {
        match name {
            "mac" => self.mac_randomization = false,
            "dns" => self.dns_status = DnsStatus::Error,
            "hostname" => self.hostname_privacy = false,
            "ipv6" => self.ipv6_privacy = false,
            "firewall" => {
                self.firewall = false;
                self.phone_link_at_home = false;
                self.remote_login_at_home = false;
            }
            "sandbox" => {
                self.sandbox_status = SandboxStatus::Inactive;
                self.browser_sandbox = BrowserSandbox::None;
            }
            _ => (),
        }
    }
}

#[derive(Debug, Clone)]
pub struct NizhodState {
    /// Unix seconds of the last completed reconcile; zero until one completes.
    pub last_check: u64,
    pub privacy_score: u8,
    pub mac_randomization: bool,
    pub dns_status: DnsStatus,
    pub hostname_privacy: bool,
    pub ipv6_privacy: bool,
    /// Incoming connections rejected by the Nizho firewall zone.
    pub firewall: bool,
    /// Phone linking is allowed on home networks (and the firewall is confirmed).
    pub phone_link_at_home: bool,
    /// Remote login (SSH) is allowed on home networks (and the firewall is confirmed).
    pub remote_login_at_home: bool,
    pub sandbox_status: SandboxStatus,
    pub browser_sandbox: BrowserSandbox,
}

impl Default for NizhodState {
    fn default() -> Self {
        Self {
            last_check: 0,
            privacy_score: 0,
            mac_randomization: false,
            dns_status: DnsStatus::Unknown("Initializing".into()),
            hostname_privacy: false,
            ipv6_privacy: false,
            firewall: false,
            phone_link_at_home: false,
            remote_login_at_home: false,
            sandbox_status: SandboxStatus::Unknown("Initializing".into()),
            browser_sandbox: BrowserSandbox::Unknown("Initializing".into()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn a_user_dns_exception_needs_no_retry_but_other_plaintext_does() {
        let mut s = NizhodState::default();
        s.dns_status = DnsStatus::ForcedPlaintext;
        assert!(s.control_healthy("dns"));
        for d in [DnsStatus::Plaintext, DnsStatus::Degraded, DnsStatus::Error] {
            s.dns_status = d;
            assert!(!s.control_healthy("dns"));
        }
    }
}
