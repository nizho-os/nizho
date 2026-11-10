use nizho_shell_common::{BrowserSandbox, DnsStatus, SandboxStatus};
use crate::state::NizhodState;

// An internal number. It is published as the D-Bus property PrivacyScore, and
// no Nizho program reads or shows it: the dashboard and the tray show each
// setting by name.
//
// Every point comes from a setting that nizhod checked on this computer. Ten
// more points used to be added for "telemetry blocked" and "tracking blocked".
// Nothing checked those, so they were removed and the top value is 90.
pub fn compute(s: &NizhodState) -> u8 {
    let mut n: u8 = 0;
    if s.mac_randomization                                      { n += 20; }
    if s.dns_status == DnsStatus::Encrypted                     { n += 25; }
    if s.hostname_privacy                                       { n += 10; }
    if s.ipv6_privacy                                           { n += 10; }
    if s.sandbox_status == SandboxStatus::Active                { n += 15; }
    if s.sandbox_status.is_active() && matches!(s.browser_sandbox,
        BrowserSandbox::Persistent
        | BrowserSandbox::Ephemeral
        | BrowserSandbox::PrivateWorkspace)                     { n += 10; }
    n
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nothing_checked_scores_nothing() {
        assert_eq!(compute(&NizhodState::default()), 0);
    }

    #[test]
    fn every_point_comes_from_a_checked_setting() {
        let mut s = NizhodState::default();
        s.mac_randomization = true;
        assert_eq!(compute(&s), 20);
        s.dns_status = DnsStatus::Encrypted;
        assert_eq!(compute(&s), 45);
        s.hostname_privacy = true;
        assert_eq!(compute(&s), 55);
        s.ipv6_privacy = true;
        assert_eq!(compute(&s), 65);
        s.sandbox_status = SandboxStatus::Active;
        assert_eq!(compute(&s), 80);
        s.browser_sandbox = BrowserSandbox::Ephemeral;
        assert_eq!(compute(&s), 90, "the top value");
    }

    #[test]
    fn an_allowed_plaintext_network_earns_no_dns_points() {
        let mut s = NizhodState::default();
        for status in [DnsStatus::ForcedPlaintext, DnsStatus::Plaintext, DnsStatus::Degraded, DnsStatus::Error] {
            s.dns_status = status;
            assert_eq!(compute(&s), 0);
        }
    }

    #[test]
    fn a_browser_mode_without_an_active_sandbox_earns_nothing() {
        let mut s = NizhodState::default();
        s.sandbox_status = SandboxStatus::Inactive;
        s.browser_sandbox = BrowserSandbox::Ephemeral;
        assert_eq!(compute(&s), 0);
    }
}
