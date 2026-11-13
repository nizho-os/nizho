//! Home networks and remote login for Settings: reading what the helpers report, calling them, and
//! the words for each state. Nothing here touches the screen, so it can be tested without one.
//!
//! A network marked as home uses the firewall zone `nizho-home`. Phone linking and remote login open
//! their ports there only (docs/HOME-NETWORKS-PLAN.md). Marking is done by the root helper
//! `nizho-home-network`; remote login by `nizho-remote-login`. Both run through pkexec.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use crate::phone::{outcome_of, Outcome};

const HOME_HELPER: &str = "/usr/libexec/nizho-home-network";
const REMOTE_HELPER: &str = "/usr/libexec/nizho-remote-login";

pub const HOME_ON: &str = "Home. Phone linking and remote login work here while you have them on.";
pub const HOME_OFF: &str = "Not home. Nothing on this network can connect to this computer.";

pub const REMOTE_OFF: &str = "Off. No other computer can sign in to this one.";
pub const REMOTE_ON_KEYS: &str = "On, on home networks only. Computers with a key you added can sign in.";
pub const REMOTE_ON_PASSWORDS: &str = "On, on home networks only. Computers with a key you added, or anyone \
who knows an account's password, can sign in.";
pub const REMOTE_NO_HOME: &str = " No network is marked as home yet, so nothing can connect.";
pub const REMOTE_TIMER: &str = " It turns itself off within an hour.";
/// Shown before turning on (docs/HOME-NETWORKS-PLAN.md, Warnings).
pub const REMOTE_WARNING: &str = "Anyone on your home network can try to sign in to this computer with a key you added.";
pub const PASSWORDS_WARNING: &str = "Passwords can be guessed by anyone on your home network; keys can't.";

#[derive(Debug, Clone, PartialEq)]
pub struct Network {
    pub uuid: String,
    /// "wifi" or "ethernet".
    pub kind: String,
    pub home: bool,
    pub active: bool,
    pub name: String,
}

/// `nizho-home-network list`: uuid, kind, home, active, name, tab-separated. Lines of any other
/// shape are skipped, never guessed at.
pub fn parse_list(text: &str) -> Vec<Network> {
    text.lines()
        .filter_map(|l| {
            let mut f = l.splitn(5, '\t');
            let (uuid, kind, home, active, name) = (f.next()?, f.next()?, f.next()?, f.next()?, f.next()?);
            let yes = |v: &str| match v { "yes" => Some(true), "no" => Some(false), _ => None };
            if !matches!(kind, "wifi" | "ethernet") || uuid.is_empty() { return None; }
            Some(Network { uuid: uuid.into(), kind: kind.into(), home: yes(home)?, active: yes(active)?, name: name.into() })
        })
        .collect()
}

fn helper(env: &str, path: &str) -> Command {
    match std::env::var(env).ok().filter(|s| !s.is_empty()) {
        Some(script) => Command::new(script),
        None => {
            let mut c = Command::new("pkexec");
            c.arg(path);
            c
        }
    }
}

fn run(mut cmd: Command, missing: &str) -> Outcome {
    match cmd.stdin(Stdio::null()).output() {
        Err(_) => Outcome::Failed(missing.into()),
        Ok(out) => outcome_of(out.status.code(), &String::from_utf8_lossy(&out.stderr)),
    }
}

/// Saved Wi-Fi and wired networks. Listing needs no password, so it runs the helper directly.
/// None when the helper could not read them.
pub fn networks() -> Option<Vec<Network>> {
    let path = std::env::var("NIZHO_HOME_NETWORK_HELPER").ok().filter(|s| !s.is_empty()).unwrap_or_else(|| HOME_HELPER.into());
    let out = Command::new(path).arg("list").stdin(Stdio::null()).output().ok()?;
    out.status.success().then(|| parse_list(&String::from_utf8_lossy(&out.stdout)))
}

pub fn any_home(nets: &[Network]) -> bool {
    nets.iter().any(|n| n.home)
}

/// The network this computer is on now, if it is a saved Wi-Fi or wired one that is not home yet.
pub fn current_not_home(nets: &[Network]) -> Option<&Network> {
    nets.iter().find(|n| n.active && !n.home)
}

pub fn set_home(uuid: &str, on: bool) -> Outcome {
    let mut c = helper("NIZHO_HOME_NETWORK_HELPER", HOME_HELPER);
    c.args(["set", uuid, if on { "on" } else { "off" }]);
    run(c, "The home network helper is not available on this system.")
}

#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct Remote {
    pub on: bool,
    pub passwords: bool,
    pub timer: bool,
}

const REMOTE_STATE: &str = "/etc/nizho/remote-login";
const REMOTE_DROPIN: &str = "/etc/ssh/sshd_config.d/10-nizho-remote-login.conf";

/// Reading the status needs no password: the state file and the sshd drop-in are readable by everyone.
pub fn remote_status() -> Remote {
    let on = std::fs::read_to_string(REMOTE_STATE).map(|t| t.trim() == "on").unwrap_or(false);
    let passwords = std::fs::read_to_string(REMOTE_DROPIN)
        .map(|t| t.lines().any(|l| l.trim() == "PasswordAuthentication yes")).unwrap_or(false);
    let timer = Command::new("systemctl").args(["is-active", "--quiet", "nizho-remote-login-off.timer"])
        .stdin(Stdio::null()).status().map(|s| s.success()).unwrap_or(false);
    Remote { on, passwords: on && passwords, timer: on && timer }
}

/// Turns remote login on (with these choices) or off.
pub fn set_remote(on: bool, passwords: bool, timer: bool) -> Outcome {
    let mut c = helper("NIZHO_REMOTE_LOGIN_HELPER", REMOTE_HELPER);
    if on {
        c.arg("on");
        if passwords { c.arg("passwords"); }
        if timer { c.arg("1h"); }
    } else {
        c.arg("off");
    }
    run(c, "The remote login helper is not available on this system.")
}

pub fn remote_subtitle(r: Remote, any_home: bool) -> String {
    if !r.on { return REMOTE_OFF.into(); }
    let mut s = String::from(if r.passwords { REMOTE_ON_PASSWORDS } else { REMOTE_ON_KEYS });
    if !any_home { s.push_str(REMOTE_NO_HOME); }
    if r.timer { s.push_str(REMOTE_TIMER); }
    s
}

// ---- keys in ~/.ssh/authorized_keys ----

const KEY_TYPES: &[&str] = &[
    "ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com",
];

pub fn authorized_keys() -> PathBuf {
    gtk4::glib::home_dir().join(".ssh").join("authorized_keys")
}

/// Keys in an authorized_keys text: lines that are not blank or comments.
pub fn count_keys(text: &str) -> usize {
    text.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).count()
}

/// A public key as `ssh-keygen` prints it: a known type, base64 data, an optional comment, one line.
/// Options in front of the type (from=, command=) are not accepted here; add those by hand.
pub fn valid_key(line: &str) -> bool {
    let line = line.trim();
    if line.contains('\n') || line.contains('\r') { return false; }
    let mut words = line.split_whitespace();
    let (Some(kind), Some(data)) = (words.next(), words.next()) else { return false };
    KEY_TYPES.contains(&kind)
        && data.len() >= 68
        && data.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'+' || b == b'/' || b == b'=')
}

pub fn keys_subtitle(n: usize) -> String {
    match n {
        0 => "No keys yet. Without one, nobody can sign in unless you also allow passwords.".into(),
        1 => "1 key can sign in to this account.".into(),
        n => format!("{n} keys can sign in to this account."),
    }
}

/// Appends a key to this account's authorized_keys, creating ~/.ssh (0700) and the file (0600).
pub fn add_key(path: &Path, key: &str) -> Result<(), &'static str> {
    use std::io::Write;
    use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
    if !valid_key(key) { return Err("That doesn't look like a public key. It starts with ssh-ed25519, ssh-rsa or ecdsa-, all on one line."); }
    let dir = path.parent().ok_or("No home folder.")?;
    std::fs::create_dir_all(dir).map_err(|_| "Couldn't create the .ssh folder.")?;
    let _ = std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700));
    let existing = std::fs::read_to_string(path).unwrap_or_default();
    if existing.lines().any(|l| l.trim() == key.trim()) { return Err("That key is already added."); }
    let mut f = std::fs::OpenOptions::new().create(true).append(true).mode(0o600).open(path)
        .map_err(|_| "Couldn't open the list of keys.")?;
    let sep = if existing.is_empty() || existing.ends_with('\n') { "" } else { "\n" };
    f.write_all(format!("{sep}{}\n", key.trim()).as_bytes()).map_err(|_| "Couldn't save the key.")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const KEY: &str = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGq3lFQh4C2bq5n4mFjI4Kq2xXz6aGvB0lRZ1m3vYbQx ram@laptop";

    #[test]
    fn the_list_is_parsed_and_odd_lines_are_skipped() {
        let text = "u1\twifi\tyes\tno\tCafé: Ram\\x\nu2\tethernet\tno\tyes\tWired connection 1\n\
                    u3\tbridge\tno\tno\tbr9\nu4\twifi\tmaybe\tno\tx\nbroken line\n\tswifi\tno\tno\tempty uuid\n";
        let nets = parse_list(text);
        assert_eq!(nets.len(), 2);
        assert_eq!(nets[0], Network { uuid: "u1".into(), kind: "wifi".into(), home: true, active: false, name: "Café: Ram\\x".into() });
        assert!(nets[1].active && !nets[1].home);
        assert!(any_home(&nets));
        assert_eq!(current_not_home(&nets).map(|n| n.uuid.as_str()), Some("u2"));
        assert_eq!(parse_list("u\twifi\tno\tno\tname\twith\ttabs")[0].name, "name\twith\ttabs");
    }

    #[test]
    fn remote_subtitles_say_what_is_true() {
        assert_eq!(remote_subtitle(Remote::default(), true), REMOTE_OFF);
        assert_eq!(remote_subtitle(Remote { on: true, ..Default::default() }, true), REMOTE_ON_KEYS);
        let s = remote_subtitle(Remote { on: true, passwords: true, timer: true }, false);
        assert!(s.starts_with(REMOTE_ON_PASSWORDS) && s.contains("No network is marked as home") && s.ends_with("within an hour."));
    }

    #[test]
    fn only_real_public_keys_are_accepted() {
        assert!(valid_key(KEY));
        assert!(valid_key(&format!("  {KEY}  ")));
        assert!(valid_key("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGq3lFQh4C2bq5n4mFjI4Kq2xXz6aGvB0lRZ1m3vYbQx"));
        for bad in ["", "hello", "ssh-ed25519", "ssh-ed25519 short", "ssh-dss AAAAB3NzaC1kc3MAAACBAP+long+enough+data+here+for+the+length+check+to+pass+ok",
                    "-----BEGIN OPENSSH PRIVATE KEY-----", "command=\"rm -rf /\" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGq3lFQh4C2bq5n4mFjI4Kq2xXz6aGvB0lRZ1m3vYbQx",
                    &format!("{KEY}\nssh-rsa AAAA"), "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGq3lFQh4C2bq5n4mFjI4Kq2xXz6aGvB0lRZ1m3vYb$x"] {
            assert!(!valid_key(bad), "{bad}");
        }
    }

    #[test]
    fn keys_are_counted_and_added_once_with_safe_permissions() {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("nizho-keys-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let path = dir.join(".ssh").join("authorized_keys");
        assert_eq!(count_keys(""), 0);
        assert_eq!(count_keys("# comment\n\nssh-rsa AAA x\n  ssh-ed25519 BBB\n"), 2);
        add_key(&path, KEY).unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), format!("{KEY}\n"));
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(std::fs::metadata(path.parent().unwrap()).unwrap().permissions().mode() & 0o777, 0o700);
        assert_eq!(add_key(&path, KEY), Err("That key is already added."));
        std::fs::write(&path, "ssh-rsa AAAA old").unwrap();
        add_key(&path, KEY).unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), format!("ssh-rsa AAAA old\n{KEY}\n"), "a missing newline is added first");
        assert!(add_key(&path, "nonsense").is_err());
        assert_eq!(keys_subtitle(0).split('.').next(), Some("No keys yet"));
        assert_eq!(keys_subtitle(2), "2 keys can sign in to this account.");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
