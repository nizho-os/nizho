//! Live read-only image status. GIO performs the D-Bus call asynchronously.
use gtk4::{gio, glib, prelude::*};

pub fn page() -> gtk4::Box {
    let page = gtk4::Box::new(gtk4::Orientation::Vertical, 16);
    page.set_margin_top(24);
    page.set_margin_start(24);
    page.set_margin_end(24);
    let label = gtk4::Label::new(Some("Loading OS image status…"));
    label.set_wrap(true);
    label.set_selectable(true);
    let refresh = gtk4::Button::with_label("Refresh system status");
    page.append(&label);
    page.append(&refresh);
    let update = move || {
        let label = label.clone();
        glib::MainContext::default().spawn_local(async move {
            let result = async {
                let bus = gio::bus_get_future(if cfg!(debug_assertions) && std::env::var_os("NIZHO_ARCHON_TEST_SESSION").is_some() { gio::BusType::Session } else { gio::BusType::System }).await?;
                bus.call_future(Some("org.nizho.Archon1"), "/org/nizho/Archon1",
                    "org.nizho.Archon1.Manager", "GetSystemStatus", None, None,
                    gio::DBusCallFlags::NONE, 5000).await
            }.await;
            match result {
                Ok(value) => match value.get::<((String, String, String, String, String, bool, bool, bool, bool),)>() {
                    Some(((image, version, staged_image, staged_version, state, reboot, rollback, queued, read_only),)) => label.set_text(&format!(
                        "Installed system: {version}\n{image}\nDeployment: {state} {staged_version}\n{staged_image}\nRestart required: {reboot} · Rollback available: {rollback}\nRollback queued: {queued} · Read-only medium: {read_only}")),
                    None => label.set_text("Archon returned an unsupported status format."),
                },
                Err(error) => label.set_text(&format!("Archon system status unavailable: {error}")),
            }
        });
    };
    update();
    refresh.connect_clicked(move |_| update());
    page
}
