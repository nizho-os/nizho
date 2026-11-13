use serde::Deserialize;
use std::fs;
use tracing::warn;

const CONFIG_PATH: &str = "/etc/nizhod/nizhod.toml";

#[derive(Deserialize)]
pub struct Config {
    #[serde(default = "default_check_interval")]
    pub check_interval_secs: u64,
    #[serde(default)]
    pub dns: DnsConfig,
    #[serde(default)]
    pub hostname: HostnameConfig,
    #[serde(default)]
    pub phone_link: PhoneLinkConfig,
    #[serde(default)]
    pub remote_login: RemoteLoginConfig,
}

#[derive(Deserialize)]
pub struct RemoteLoginConfig {
    /// Holds the word "on" when the user allowed remote login (SSH) on home networks.
    #[serde(default = "default_remote_login_state")]
    pub state_path: String,
}

#[derive(Deserialize)]
pub struct PhoneLinkConfig {
    /// Holds the word "on" when the user allowed phones to find this computer.
    #[serde(default = "default_phone_link_state")]
    pub state_path: String,
}

#[derive(Deserialize)]
pub struct DnsConfig {
    #[serde(default = "default_dns_server")]
    pub server: String,
    #[serde(default = "default_dns_name")]
    pub tls_name: String,
}

#[derive(Deserialize)]
pub struct HostnameConfig {
    #[serde(default = "default_hostname")]
    pub name: String,
}

impl Config {
    pub fn load() -> Self {
        match fs::read_to_string(CONFIG_PATH) {
            Ok(contents) => toml::from_str(&contents).unwrap_or_else(|e| {
                warn!("config parse error: {e}, using defaults");
                Self::default()
            }),
            Err(_) => Self::default(),
        }
    }
}

impl Default for Config {
    fn default() -> Self {
        Self {
            check_interval_secs: default_check_interval(),
            dns: DnsConfig::default(),
            hostname: HostnameConfig::default(),
            phone_link: PhoneLinkConfig::default(),
            remote_login: RemoteLoginConfig::default(),
        }
    }
}

impl Default for DnsConfig {
    fn default() -> Self {
        Self {
            server: default_dns_server(),
            tls_name: default_dns_name(),
        }
    }
}

impl Default for PhoneLinkConfig {
    fn default() -> Self {
        Self {
            state_path: default_phone_link_state(),
        }
    }
}

impl Default for RemoteLoginConfig {
    fn default() -> Self {
        Self {
            state_path: default_remote_login_state(),
        }
    }
}

impl Default for HostnameConfig {
    fn default() -> Self {
        Self {
            name: default_hostname(),
        }
    }
}

fn default_check_interval() -> u64 { 60 }
fn default_dns_server() -> String { "9.9.9.9".into() }
fn default_dns_name() -> String { "dns.quad9.net".into() }
fn default_hostname() -> String { "nizho".into() }
fn default_phone_link_state() -> String { "/etc/nizho/phone-link".into() }
fn default_remote_login_state() -> String { "/etc/nizho/remote-login".into() }

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn phone_link_state_path_defaults_and_can_be_set() {
        assert_eq!(Config::default().phone_link.state_path, "/etc/nizho/phone-link");
        let c: Config = toml::from_str("").unwrap();
        assert_eq!(c.phone_link.state_path, "/etc/nizho/phone-link");
        let c: Config = toml::from_str("[phone_link]\nstate_path = \"/tmp/x\"").unwrap();
        assert_eq!(c.phone_link.state_path, "/tmp/x");
    }

    #[test]
    fn remote_login_state_path_defaults_and_can_be_set() {
        assert_eq!(Config::default().remote_login.state_path, "/etc/nizho/remote-login");
        let c: Config = toml::from_str("[remote_login]\nstate_path = \"/tmp/y\"").unwrap();
        assert_eq!(c.remote_login.state_path, "/tmp/y");
    }
}
