//! Real GTK/service contract test. Run only in the disposable native fixture.
use adw::prelude::*;
use gtk4::{glib, prelude::*};
use libadwaita as adw;
use std::time::{Duration, Instant};
fn descendants(widget: &gtk4::Widget) -> Vec<gtk4::Widget> {
    let mut result = vec![widget.clone()];
    let mut child = widget.first_child();
    while let Some(current) = child {
        result.extend(descendants(&current));
        child = current.next_sibling()
    }
    result
}
fn wait(mut condition: impl FnMut() -> bool) {
    let until = Instant::now() + Duration::from_secs(30);
    loop {
        while glib::MainContext::default().pending() {
            glib::MainContext::default().iteration(false);
        }
        if condition() {
            return;
        }
        assert!(Instant::now() < until, "UI did not reach expected state");
        std::thread::sleep(Duration::from_millis(10));
    }
}
fn contains(root: &gtk4::Widget, needle: &str) -> bool {
    descendants(root).iter().any(|w| {
        w.is_mapped()
            && w.downcast_ref::<gtk4::Label>()
                .is_some_and(|l| l.text().contains(needle))
    })
}
fn button(root: &gtk4::Widget, label: &str) -> gtk4::Button {
    descendants(root)
        .into_iter()
        .filter(|w| w.is_mapped())
        .filter_map(|w| w.downcast::<gtk4::Button>().ok())
        .find(|b| b.label().as_deref() == Some(label))
        .expect("visible button")
}
fn dialog(heading: &str) -> Option<adw::MessageDialog> {
    gtk4::Window::list_toplevels()
        .into_iter()
        .filter_map(|w| w.downcast::<adw::MessageDialog>().ok())
        .find(|d| d.is_visible() && d.heading().as_deref().is_some_and(|s| s.contains(heading)))
}
fn capture(name: &str) {
    if let Ok(directory) = std::env::var("NIZHO_UI_CAPTURE_DIR") {
        let path = std::path::Path::new(&directory).join(format!("{name}.png"));
        if std::path::Path::new("/usr/bin/import").is_file() {
            let _ = std::process::Command::new("/usr/bin/import")
                .args(["-window", "root"])
                .arg(path)
                .status();
        }
    }
}

#[test]
#[ignore = "requires Xvfb and the real signed disposable repository/service fixture"]
fn actual_service_search_categories_details_confirmation_and_recovery() {
    assert_eq!(
        std::env::var("NIZHO_ARCHON_TEST_SESSION").as_deref(),
        Ok("1")
    );
    adw::init().expect("GTK display required");
    let page = super::packages::page();
    let window = adw::Window::builder()
        .title("Archon UI contract test")
        .default_width(900)
        .default_height(700)
        .content(&page)
        .build();
    window.present();
    let root: gtk4::Widget = page.clone().upcast();
    wait(|| contains(&root, "org.nizho.ArchonTest"));
    let search = descendants(&root)
        .into_iter()
        .find_map(|w| w.downcast::<gtk4::SearchEntry>().ok())
        .unwrap();
    capture("discover");
    search.grab_focus();
    assert!(search.has_focus() || search.focus_child().is_some());
    search.set_text("NoSuchArchonFixture");
    wait(|| contains(&root, "No matching applications"));
    search.set_text("ArchonTest");
    wait(|| contains(&root, "org.nizho.ArchonTest"));
    let category = descendants(&root)
        .into_iter()
        .find_map(|w| w.downcast::<gtk4::DropDown>().ok())
        .unwrap();
    category.set_selected(1);
    wait(|| contains(&root, "No matching applications"));
    category.set_selected(0);
    wait(|| contains(&root, "org.nizho.ArchonTest"));
    button(&root, "Details").emit_clicked();
    wait(|| dialog("org.nizho.ArchonTest").is_some());
    let details = dialog("org.nizho.ArchonTest").unwrap();
    assert!(details.body().contains("ArchonTestPlatform"));
    capture("details");
    details.response("action");
    wait(|| dialog("Review install").is_some());
    let review = dialog("Review install").unwrap();
    assert!(review.body().contains("ArchonTestPlatform"));
    capture("confirmation");
    review.response("cancel");
    wait(|| contains(&root, "Plan cancelled"));
    let stack = descendants(&root)
        .into_iter()
        .find_map(|w| w.downcast::<gtk4::Stack>().ok())
        .unwrap();
    stack.set_visible_child_name("installed");
    wait(|| contains(&root, "No matching applications"));
    stack.set_visible_child_name("updates");
    wait(|| contains(&root, "No matching applications"));
    stack.set_visible_child_name("repositories");
    wait(|| contains(&root, "archon-test"));
    stack.set_visible_child_name("history");
    wait(|| contains(&root, "cancelled"));
    stack.set_visible_child_name("settings");
    wait(|| contains(&root, "verified builder"));
    // Only the runner's disposable, unprivileged service process is signalled.
    let pid: u32 = std::env::var("NIZHO_UI_SERVICE_PID")
        .expect("fixture service PID")
        .parse()
        .unwrap();
    assert!(pid > 1);
    assert!(std::process::Command::new("/usr/bin/kill")
        .args(["-TERM", &pid.to_string()])
        .status()
        .unwrap()
        .success());
    stack.set_visible_child_name("installed");
    wait(|| contains(&root, "Archon unavailable"));
    window.close();
}
