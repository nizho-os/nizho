use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;
use zbus::{message::Header, zvariant::Value, Connection};

const ACTION: &str = "org.nizho.archon.manage";
const AUTHORITY: &str = "org.freedesktop.PolicyKit1";
const PATH: &str = "/org/freedesktop/PolicyKit1/Authority";
const INTERFACE: &str = "org.freedesktop.PolicyKit1.Authority";
static REQUEST_ID: AtomicU64 = AtomicU64::new(0);

#[derive(serde::Deserialize, serde::Serialize, zbus::zvariant::Type)]
struct AuthorizationResult {
    authorized: bool,
    challenge: bool,
    details: HashMap<String, String>,
}

/// Builds the polkit `details` for one transaction so the password prompt
/// names what is about to change. polkit shows `polkit.message` in place of
/// the fixed text from the policy file and replaces `$(app_id)` with the value
/// of the `app_id` key. polkit accepts non-empty details only from a caller
/// that is root or owns the action; this service runs as root.
///
/// The map never decides who is authorised. That stays with the action
/// defaults and the administrator's polkit rules.
///
/// An operation or reference that is not in the expected form gives an empty
/// map, and the prompt then shows the fixed text from the policy file.
fn prompt_details(operation: &str, package: &str) -> HashMap<&'static str, String> {
    let message = match operation {
        "install" => "Administrator authentication is required to install the application $(app_id)",
        "remove" => "Administrator authentication is required to remove the application $(app_id)",
        "update" => "Administrator authentication is required to update the application $(app_id)",
        _ => return HashMap::new(),
    };
    let Some(app_id) = app_id(package) else {
        return HashMap::new();
    };
    HashMap::from([
        ("polkit.message", message.to_owned()),
        ("app_id", app_id.to_owned()),
        ("operation", operation.to_owned()),
    ])
}

/// The application ID inside a full Flatpak reference `app/ID/ARCH/BRANCH`.
/// Only the characters Flatpak allows in an ID pass, so the prompt text cannot
/// carry spaces, markup, control characters or another `$(key)` reference.
fn app_id(package: &str) -> Option<&str> {
    let mut parts = package.split('/');
    let (kind, id) = (parts.next()?, parts.next()?);
    if kind != "app" || parts.count() != 2 {
        return None;
    }
    let allowed = |c: char| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-');
    (!id.is_empty() && id.len() <= 255 && id.chars().all(allowed)).then_some(id)
}

/// `operation` and `package` come from the stored plan record, never from the
/// commit call. They only choose the prompt text.
pub async fn check(
    conn: &Connection,
    header: &Header<'_>,
    operation: &str,
    package: &str,
) -> zbus::fdo::Result<()> {
    let sender = header
        .sender()
        .ok_or_else(|| denied("Missing authenticated bus sender"))?;
    let interactive = header
        .primary()
        .flags()
        .contains(zbus::message::Flags::AllowInteractiveAuth);
    check_sender(
        conn,
        sender.as_str(),
        interactive,
        Duration::from_secs(60),
        prompt_details(operation, package),
    )
    .await
}

fn denied(message: &str) -> zbus::fdo::Error {
    zbus::fdo::Error::AccessDenied(message.into())
}

async fn check_sender(
    conn: &Connection,
    sender: &str,
    interactive: bool,
    timeout: Duration,
    details: HashMap<&'static str, String>,
) -> zbus::fdo::Result<()> {
    // This identity comes from the message bus header, never method arguments
    // or a caller-supplied PID (which could be reused before authorization).
    let subject = (
        "system-bus-name",
        HashMap::from([("name", Value::from(sender))]),
    );
    let cancellation = format!(
        "nizho-archon-{}",
        REQUEST_ID.fetch_add(1, Ordering::Relaxed)
    );
    let args = (
        subject,
        ACTION,
        details,
        u32::from(interactive),
        &cancellation,
    );
    let call = conn.call_method(
        Some(AUTHORITY),
        PATH,
        Some(INTERFACE),
        "CheckAuthorization",
        &args,
    );
    let response = match tokio::time::timeout(timeout, call).await {
        Ok(Ok(reply)) => reply,
        Ok(Err(error)) => {
            // The caller gets a fixed text. The cause goes to the journal.
            eprintln!("Archon authorization check could not be made; request denied: {error}");
            return Err(denied("Authorization service unavailable; no changes made"));
        }
        Err(_) => {
            // The request is denied either way. A failed cancel only means the
            // prompt may stay on screen, so it is logged and nothing else.
            let cancel = tokio::time::timeout(
                Duration::from_secs(1),
                conn.call_method(
                    Some(AUTHORITY),
                    PATH,
                    Some(INTERFACE),
                    "CancelCheckAuthorization",
                    &(&cancellation,),
                ),
            )
            .await;
            match cancel {
                Ok(Ok(_)) => (),
                Ok(Err(error)) => eprintln!("Archon could not cancel the timed-out authorization request; its prompt may stay open: {error}"),
                Err(_) => eprintln!("Archon got no reply cancelling the timed-out authorization request; its prompt may stay open"),
            }
            return Err(denied(
                "Administrator authentication timed out; no changes made",
            ));
        }
    };
    let (result,): (AuthorizationResult,) = response
        .body()
        .deserialize()
        .map_err(|_| denied("Invalid authorization reply; no changes made"))?;
    if result.authorized {
        Ok(())
    } else {
        Err(denied(
            "Administrator authentication is required to manage applications",
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        io::{BufRead, BufReader},
        process::{Child, Command, Stdio},
        sync::{Arc, Mutex},
    };
    use zbus::zvariant::OwnedValue;

    struct TestBus(Child);
    impl Drop for TestBus {
        fn drop(&mut self) {
            if let Err(error) = self.0.kill() {
                eprintln!("could not stop the private test bus: {error}");
            }
            if let Err(error) = self.0.wait() {
                eprintln!("could not reap the private test bus: {error}");
            }
        }
    }

    #[derive(Default)]
    struct Replies {
        allow: bool,
        delay: Duration,
        cancelled: usize,
        calls: Vec<(String, String, u32)>,
        details: Vec<HashMap<String, String>>,
    }
    struct Authority(Arc<Mutex<Replies>>);

    #[zbus::interface(name = "org.freedesktop.PolicyKit1.Authority")]
    impl Authority {
        async fn check_authorization(
            &self,
            subject: (String, HashMap<String, OwnedValue>),
            action: String,
            details: HashMap<String, String>,
            flags: u32,
            _cancellation: String,
        ) -> (AuthorizationResult,) {
            assert_eq!(subject.0, "system-bus-name");
            let sender = <&str>::try_from(subject.1.get("name").unwrap())
                .unwrap()
                .to_owned();
            let (allow, delay) = {
                let mut state = self.0.lock().unwrap();
                state.calls.push((sender, action, flags));
                state.details.push(details);
                (state.allow, state.delay)
            };
            tokio::time::sleep(delay).await;
            (AuthorizationResult {
                authorized: allow,
                challenge: !allow,
                details: HashMap::new(),
            },)
        }

        fn cancel_check_authorization(&self, _id: String) {
            self.0.lock().unwrap().cancelled += 1;
        }
    }

    #[tokio::test]
    async fn authorization_uses_bus_identity_and_fails_closed() {
        let mut bus = TestBus(
            Command::new("dbus-daemon")
                .args(["--session", "--nofork", "--print-address=1"])
                .stdout(Stdio::piped())
                .spawn()
                .unwrap(),
        );
        let mut address = String::new();
        BufReader::new(bus.0.stdout.take().unwrap())
            .read_line(&mut address)
            .unwrap();
        let replies = Arc::new(Mutex::new(Replies::default()));
        let authority = zbus::connection::Builder::address(address.trim())
            .unwrap()
            .name(AUTHORITY)
            .unwrap()
            .serve_at(PATH, Authority(replies.clone()))
            .unwrap()
            .build()
            .await
            .unwrap();
        let client = zbus::connection::Builder::address(address.trim())
            .unwrap()
            .build()
            .await
            .unwrap();
        let sender = client.unique_name().unwrap().as_str();
        let calculator = || prompt_details("install", "app/org.gnome.Calculator/x86_64/stable");
        // A named package changes the prompt only: the authority's answer
        // still decides, and a denial stays a denial.
        assert!(check_sender(&client, sender, false, Duration::from_secs(1), calculator())
            .await
            .is_err());
        assert_eq!(
            replies.lock().unwrap().calls[0],
            (sender.into(), ACTION.into(), 0)
        );
        {
            let seen = replies.lock().unwrap();
            assert_eq!(seen.details[0]["app_id"], "org.gnome.Calculator");
            assert_eq!(seen.details[0]["operation"], "install");
            assert_eq!(
                seen.details[0]["polkit.message"],
                "Administrator authentication is required to install the application $(app_id)"
            );
            assert_eq!(seen.details[0].len(), 3);
        }
        replies.lock().unwrap().allow = true;
        assert!(check_sender(&client, sender, true, Duration::from_secs(1), HashMap::new())
            .await
            .is_ok());
        assert_eq!(replies.lock().unwrap().calls[1].2, 1);
        assert!(replies.lock().unwrap().details[1].is_empty());
        replies.lock().unwrap().delay = Duration::from_millis(100);
        assert!(
            check_sender(&client, sender, true, Duration::from_millis(5), calculator())
                .await
                .is_err()
        );
        assert_eq!(replies.lock().unwrap().cancelled, 1);
        authority.close().await.unwrap();
        assert!(check_sender(&client, sender, false, Duration::from_secs(1), calculator())
            .await
            .is_err());
    }

    #[test]
    fn details_name_the_operation_and_the_application() {
        for (operation, verb) in [("install", "install"), ("remove", "remove"), ("update", "update")] {
            let details = prompt_details(operation, "app/org.kde.kate/x86_64/stable");
            assert_eq!(details.len(), 3);
            assert_eq!(details["app_id"], "org.kde.kate");
            assert_eq!(details["operation"], operation);
            assert_eq!(
                details["polkit.message"],
                format!("Administrator authentication is required to {verb} the application $(app_id)")
            );
            // The only reference in the message is the key that is supplied.
            assert_eq!(details["polkit.message"].matches("$(").count(), 1);
        }
    }

    #[test]
    fn details_are_empty_for_anything_that_is_not_a_known_operation_on_an_app_reference() {
        for operation in ["", "Install", "downgrade", "install "] {
            assert!(prompt_details(operation, "app/org.kde.kate/x86_64/stable").is_empty(), "{operation:?}");
        }
        for package in [
            "",
            "org.kde.kate",
            "app/org.kde.kate",
            "app/org.kde.kate/x86_64",
            "app/org.kde.kate/x86_64/stable/extra",
            "runtime/org.kde.Platform/x86_64/6.9",
            "app//x86_64/stable",
            "app/org.kde kate/x86_64/stable",
            "app/$(polkit.message)/x86_64/stable",
            "app/<b>kate</b>/x86_64/stable",
            "app/org.kde.kate\n/x86_64/stable",
            "app/org.kde.k\u{e9}/x86_64/stable",
        ] {
            assert!(prompt_details("install", package).is_empty(), "{package:?}");
        }
        let long = format!("app/{}/x86_64/stable", "a".repeat(256));
        assert!(prompt_details("install", &long).is_empty());
        assert_eq!(app_id("app/org.example.My_App-2/aarch64/beta"), Some("org.example.My_App-2"));
    }
}
