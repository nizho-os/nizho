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

pub async fn check(conn: &Connection, header: &Header<'_>) -> zbus::fdo::Result<()> {
    let sender = header
        .sender()
        .ok_or_else(|| denied("Missing authenticated bus sender"))?;
    let interactive = header
        .primary()
        .flags()
        .contains(zbus::message::Flags::AllowInteractiveAuth);
    check_sender(conn, sender.as_str(), interactive, Duration::from_secs(60)).await
}

fn denied(message: &str) -> zbus::fdo::Error {
    zbus::fdo::Error::AccessDenied(message.into())
}

async fn check_sender(
    conn: &Connection,
    sender: &str,
    interactive: bool,
    timeout: Duration,
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
        HashMap::<&str, &str>::new(),
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
        Ok(Err(_)) => return Err(denied("Authorization service unavailable; no changes made")),
        Err(_) => {
            let _ = tokio::time::timeout(
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
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

    #[derive(Default)]
    struct Replies {
        allow: bool,
        delay: Duration,
        cancelled: usize,
        calls: Vec<(String, String, u32)>,
    }
    struct Authority(Arc<Mutex<Replies>>);

    #[zbus::interface(name = "org.freedesktop.PolicyKit1.Authority")]
    impl Authority {
        async fn check_authorization(
            &self,
            subject: (String, HashMap<String, OwnedValue>),
            action: String,
            _details: HashMap<String, String>,
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
        assert!(check_sender(&client, sender, false, Duration::from_secs(1))
            .await
            .is_err());
        assert_eq!(
            replies.lock().unwrap().calls[0],
            (sender.into(), ACTION.into(), 0)
        );
        replies.lock().unwrap().allow = true;
        assert!(check_sender(&client, sender, true, Duration::from_secs(1))
            .await
            .is_ok());
        assert_eq!(replies.lock().unwrap().calls[1].2, 1);
        replies.lock().unwrap().delay = Duration::from_millis(100);
        assert!(
            check_sender(&client, sender, true, Duration::from_millis(5))
                .await
                .is_err()
        );
        assert_eq!(replies.lock().unwrap().cancelled, 1);
        authority.close().await.unwrap();
        assert!(check_sender(&client, sender, false, Duration::from_secs(1))
            .await
            .is_err());
    }
}
