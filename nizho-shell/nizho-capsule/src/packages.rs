//! Unprivileged Archon client. All package decisions are made by the service.
use gtk4::{gio, glib, prelude::*};
use libadwaita::{self as adw, prelude::*};
use serde_json::Value;
use std::{cell::Cell, rc::Rc};
thread_local! { static ACTIVE: std::cell::RefCell<Option<String>> = const { std::cell::RefCell::new(None) }; }
const BUS: &str = "org.nizho.Archon1";
const PATH: &str = "/org/nizho/Archon1";
const IFACE: &str = "org.nizho.Archon1.Manager";
type Package = (
    String,
    String,
    String,
    String,
    String,
    String,
    bool,
    u64,
    u64,
    String,
);
async fn call(
    method: &str,
    args: Option<&glib::Variant>,
    interactive: bool,
) -> Result<glib::Variant, glib::Error> {
    let bus = gio::bus_get_future(
        if cfg!(debug_assertions) && std::env::var_os("NIZHO_ARCHON_TEST_SESSION").is_some() {
            gio::BusType::Session
        } else {
            gio::BusType::System
        },
    )
    .await?;
    bus.call_future(
        Some(BUS),
        PATH,
        IFACE,
        method,
        args,
        None,
        if interactive {
            gio::DBusCallFlags::ALLOW_INTERACTIVE_AUTHORIZATION
        } else {
            gio::DBusCallFlags::NONE
        },
        120000,
    )
    .await
}
fn text(value: &str) -> gtk4::Label {
    let label = gtk4::Label::new(Some(value));
    label.set_wrap(true);
    label.set_xalign(0.0);
    label.set_selectable(true);
    label
}
fn version_label(value: &str) -> String {
    if value.len() == 64 && value.bytes().all(|b| b.is_ascii_hexdigit()) {
        format!("Commit {}", &value[..12])
    } else {
        value.to_string()
    }
}

fn local_time(seconds: u64) -> String {
    i64::try_from(seconds)
        .ok()
        .and_then(|s| glib::DateTime::from_unix_local(s).ok())
        .and_then(|d| d.format("%x %X").ok())
        .map(|s| s.to_string())
        .unwrap_or_else(|| "Time unavailable".into())
}

fn readable_plan(raw: &str) -> String {
    let Ok(value) = serde_json::from_str::<Value>(raw) else {
        return "Unsupported plan format".into();
    };
    value["changes"]
        .as_array()
        .map(|changes| {
            changes
                .iter()
                .map(|c| {
                    format!(
                        "{}\nSource: {}\nDownload: {} bytes · Installed: {} bytes",
                        c["package"].as_str().unwrap_or("Unknown"),
                        c["repository"].as_str().unwrap_or("Unknown"),
                        c["download_bytes"],
                        c["installed_bytes"]
                    )
                })
                .collect::<Vec<_>>()
                .join("\n\n")
        })
        .unwrap_or_else(|| "No package changes".into())
}
fn plan(parent: gtk4::Window, action: String, package: Package, status: gtk4::Label) {
    glib::MainContext::default().spawn_local(async move {
        status.set_text("Checking the app and required components…");
        let result=call("PlanTransaction",Some(&(action.clone(),package.0.clone(),package.4.clone()).to_variant()),false).await;
        let value=match result {Ok(value)=>value,Err(e)=>{status.set_text(&format!("Could not plan transaction: {e}"));return}};
        let plan=value.get::<((String,u64,String,String),)>().map(|v|v.0);
        let Some((id,_,_,details))=plan else {status.set_text("Archon returned an unsupported plan format");return};
        let dialog=adw::MessageDialog::builder().transient_for(&parent).modal(true)
            .heading(format!("Review {action}"))
            .body(format!("Apps from Flathub, a third-party app repository\n\n{}\n\nAdministrator approval is required. This review expires in five minutes.",readable_plan(&details))).build();
        dialog.add_responses(&[("cancel","Cancel"),("commit","Authorize and apply")]);
        dialog.set_response_appearance("commit",adw::ResponseAppearance::Suggested);
        dialog.set_default_response(Some("cancel"));dialog.set_close_response("cancel");
        status.set_text("Review the transaction before authorizing");
        dialog.connect_response(None,move |dialog,response| {
            dialog.close();
            let id=id.clone();let status=status.clone();
            let commit=response=="commit";
            glib::MainContext::default().spawn_local(async move {
                if !commit {let _=call("CancelTransaction",Some(&(id,).to_variant()),false).await;status.set_text("Plan cancelled");return}
                match call("CommitTransaction",Some(&(id,).to_variant()),true).await {
                    Ok(value)=>{
                        if let Some((id,))=value.get::<(String,)>() { ACTIVE.with(|active| *active.borrow_mut()=Some(id)); }
                        status.set_text("App change started. Progress and results appear below.");
                    },
                    Err(e)=>status.set_text(&format!("Transaction was not started: {e}")),
                }
            });
        });dialog.present();
    });
}
fn details(parent: gtk4::Window, package: Package, status: gtk4::Label) {
    glib::MainContext::default().spawn_local(async move {
        let result=call("GetPackageDetails",Some(&(package.0.clone(),package.4.clone()).to_variant()),false).await;
        let body=match result {
            Ok(value)=>value.get::<(String,)>().map(|v|v.0).and_then(|s|serde_json::from_str::<Value>(&s).ok()).map(|v|format!("{}\n\nVersion/commit: {}\nRepository: {}\nTrust: {}\nInstalled size: {} bytes\nDownload: {} bytes\n\n{}",package.0,package.3,package.4,package.9,v["installed_bytes"],v["download_bytes"],format!("{}\n\nDependencies: {}\nLicense: {}\nHomepage: {}",v["description"].as_str().unwrap_or("Description not provided"),v["dependencies"].as_str().unwrap_or("Not provided"),v["license"].as_str().unwrap_or("Not provided"),v["homepage"].as_str().unwrap_or("Not provided")))).unwrap_or_else(||"Unsupported details format".into()),
            Err(e)=>format!("Details unavailable: {e}"),
        };
        let dialog=adw::MessageDialog::builder().transient_for(&parent).heading(&package.1).modal(true).body(body).build();
        dialog.add_responses(&[("close","Close"),("action",if package.6 {"Remove…"} else {"Install…"})]);
        dialog.set_default_response(Some("close"));dialog.set_close_response("close");
        dialog.connect_response(Some("action"),move |dialog,_|{dialog.close();plan(parent.clone(),if package.6 {"remove"}else{"install"}.into(),package.clone(),status.clone())});
        dialog.present();
    });
}
fn list_page(kind: &'static str) -> gtk4::Box {
    let page = gtk4::Box::new(gtk4::Orientation::Vertical, 12);
    if kind == "updates" {
        page.append(&text("System image deployment"));
        page.append(&super::archon::page());
        page.append(&text("Application updates · Flatpak"));
    }
    let controls = gtk4::Box::new(gtk4::Orientation::Vertical, 8);
    let filters = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
    let search = gtk4::SearchEntry::new();
    search.set_hexpand(true);
    search.set_placeholder_text(Some("Search applications"));
    let category = gtk4::DropDown::from_strings(&[
        "All categories",
        "AudioVideo",
        "Development",
        "Education",
        "Game",
        "Graphics",
        "Network",
        "Office",
        "Science",
        "System",
        "Utility",
    ]);
    category.set_tooltip_text(Some("Filter by AppStream category"));
    let refresh = gtk4::Button::with_label("Refresh");
    let next = gtk4::Button::with_label("Next page");
    controls.append(&search);
    if kind == "discover" {
        filters.append(&category)
    }
    filters.append(&refresh);
    if kind == "discover" {
        filters.append(&next)
    }
    controls.append(&filters);
    page.append(&controls);
    let status = text("Ready");
    page.append(&status);
    let spinner = gtk4::Spinner::new();
    page.append(&spinner);
    let list = gtk4::ListBox::new();
    list.add_css_class("boxed-list");
    list.set_selection_mode(gtk4::SelectionMode::None);
    let scroll = gtk4::ScrolledWindow::builder()
        .vexpand(true)
        .hscrollbar_policy(gtk4::PolicyType::Never)
        .child(&list)
        .build();
    page.append(&scroll);
    let offset = Rc::new(Cell::new(0u32));
    let generation = Rc::new(Cell::new(0u64));
    let update: Rc<dyn Fn()> = Rc::new({
        let category = category.clone();
        let list = list.clone();
        let search = search.clone();
        let status = status.clone();
        let spinner = spinner.clone();
        let page = page.clone();
        let offset = offset.clone();
        let generation = generation.clone();
        move || {
            generation.set(generation.get() + 1);
            let current = generation.get();
            let generation = generation.clone();
            let list = list.clone();
            let status = status.clone();
            let spinner = spinner.clone();
            let page = page.clone();
            let query = search.text().to_string();
            let start = offset.get();
            let selected = category.selected();
            let category = if selected == 0 {
                String::new()
            } else {
                category
                    .selected_item()
                    .and_downcast::<gtk4::StringObject>()
                    .map(|s| s.string().to_string())
                    .unwrap_or_default()
            };
            spinner.start();
            status.set_text("Loading from Archon…");
            glib::MainContext::default().spawn_local(async move {
                let method = match kind {
                    "installed" => "GetInstalledPackages",
                    "updates" => "GetAvailableUpdates",
                    _ => "SearchPackages",
                };
                let args = (query.clone(), category, start, 50u32).to_variant();
                let result = call(
                    method,
                    if kind == "discover" {
                        Some(&args)
                    } else {
                        None
                    },
                    false,
                )
                .await;
                if generation.get() != current {
                    return;
                }
                spinner.stop();
                while let Some(child) = list.first_child() {
                    list.remove(&child)
                }
                let rows = match result {
                    Ok(value) => value.get::<(Vec<Package>,)>().map(|v| v.0),
                    Err(e) => {
                        status.set_text(&format!("Archon unavailable: {e}. Refresh to retry."));
                        return;
                    }
                };
                let Some(rows) = rows else {
                    status.set_text("Unsupported catalogue format");
                    return;
                };
                let rows: Vec<_> = rows
                    .into_iter()
                    .filter(|p| {
                        kind == "discover"
                            || p.1.to_lowercase().contains(&query.to_lowercase())
                            || p.0.to_lowercase().contains(&query.to_lowercase())
                    })
                    .collect();
                status.set_text(if rows.is_empty() {
                    "No matching applications"
                } else if kind == "updates" {
                    "Application updates · System deployment status is shown below"
                } else {
                    "Apps from Flathub, a third-party repository · Signature verification required"
                });
                for package in rows {
                    let row = adw::ActionRow::builder()
                        .title(&package.1)
                        .subtitle(format!(
                            "{}\n{} · {} · {}",
                            if package.2.is_empty() {
                                "No summary provided"
                            } else {
                                &package.2
                            },
                            version_label(&package.3),
                            package.4,
                            if package.6 { "Installed" } else { "Available" }
                        ))
                        .build();
                    row.set_use_markup(false);
                    row.set_subtitle_lines(2);
                    row.add_prefix(&gtk4::Image::from_icon_name(
                        "application-x-executable-symbolic",
                    ));
                    let action = gtk4::Button::with_label(if kind == "updates" {
                        "Update…"
                    } else {
                        "Details"
                    });
                    action.set_valign(gtk4::Align::Center);
                    row.add_suffix(&action);
                    let status = status.clone();
                    let page = page.clone();
                    action.connect_clicked(move |_| {
                        if let Some(parent) = page.root().and_downcast::<gtk4::Window>() {
                            if kind == "updates" {
                                plan(parent, "update".into(), package.clone(), status.clone())
                            } else {
                                details(parent, package.clone(), status.clone())
                            }
                        }
                    });
                    list.append(&row);
                }
            });
        }
    });
    refresh.connect_clicked({
        let update = update.clone();
        move |_| update()
    });
    search.connect_search_changed({
        let update = update.clone();
        let offset = offset.clone();
        move |_| {
            offset.set(0);
            update()
        }
    });
    category.connect_selected_notify({
        let update = update.clone();
        let offset = offset.clone();
        move |_| {
            offset.set(0);
            update()
        }
    });
    next.connect_clicked({
        let update = update.clone();
        move |_| {
            offset.set(offset.get().saturating_add(50));
            update()
        }
    });
    page.connect_map(move |_| update());
    page
}
fn information_page(history: bool) -> gtk4::Box {
    let page = gtk4::Box::new(gtk4::Orientation::Vertical, 12);
    let label = text("Loading…");
    let refresh = gtk4::Button::with_label("Refresh");
    let scroll = gtk4::ScrolledWindow::builder()
        .vexpand(true)
        .child(&label)
        .build();
    page.append(&refresh);
    page.append(&scroll);
    let update = Rc::new({
        let label = label.clone();
        move || {
            let label = label.clone();
            glib::MainContext::default().spawn_local(async move {
                let args = (0u32, 100u32).to_variant();
                match call(
                    if history {
                        "GetTransactionHistory"
                    } else {
                        "GetRepositories"
                    },
                    if history { Some(&args) } else { None },
                    false,
                )
                .await
                {
                    Ok(value) => {
                        let display = if history {
                            type Row = (String, String, String, String, u64, String);
                            value
                                .get::<(Vec<Row>,)>()
                                .map(|v| {
                                    v.0.into_iter()
                                        .map(|r| {
                                            format!(
                                                "{} · {}\n{}\nApp change: {}\n{}\n{}",
                                                r.3,
                                                r.1,
                                                r.2,
                                                r.0,
                                                local_time(r.4),
                                                r.5
                                            )
                                        })
                                        .collect::<Vec<_>>()
                                        .join("\n\n")
                                })
                                .unwrap_or_else(|| "Unsupported history format".into())
                        } else {
                            value
                                .get::<(String,)>()
                                .and_then(|v| serde_json::from_str::<Value>(&v.0).ok())
                                .and_then(|v| v.as_array().cloned())
                                .map(|v| {
                                    v.iter()
                                        .map(|r| {
                                            format!(
                                                "{}\n{}\nEnabled: {} · Signature verification: {}",
                                                r["id"].as_str().unwrap_or("Unknown"),
                                                r["url"].as_str().unwrap_or(""),
                                                r["enabled"],
                                                r["signature_required"]
                                            )
                                        })
                                        .collect::<Vec<_>>()
                                        .join("\n\n")
                                })
                                .unwrap_or_else(|| "Unsupported repository format".into())
                        };
                        label.set_text(if display.is_empty() {
                            "No records"
                        } else {
                            &display
                        });
                    }
                    Err(e) => {
                        label.set_text(&format!("Archon unavailable: {e}. Refresh to retry."))
                    }
                }
            });
        }
    });
    refresh.connect_clicked({
        let update = update.clone();
        move |_| update()
    });
    page.connect_map(move |_| update());
    page
}
pub fn page() -> gtk4::Box {
    let outer = gtk4::Box::new(gtk4::Orientation::Vertical, 12);
    outer.set_margin_start(16);
    outer.set_margin_end(16);
    outer.set_margin_top(16);
    outer.set_margin_bottom(16);
    let content = gtk4::Box::new(gtk4::Orientation::Horizontal, 12);
    let stack = gtk4::Stack::new();
    stack.set_hexpand(true);
    stack.set_vexpand(true);
    let sidebar = gtk4::StackSidebar::new();
    sidebar.set_stack(&stack);
    content.append(&sidebar);
    content.append(&stack);
    stack.add_titled(&list_page("discover"), Some("discover"), "Discover");
    stack.add_titled(&list_page("installed"), Some("installed"), "Installed");
    stack.add_titled(&list_page("updates"), Some("updates"), "Updates");
    stack.add_titled(
        &information_page(false),
        Some("repositories"),
        "Repositories",
    );
    stack.add_titled(&information_page(true), Some("history"), "History");
    let settings = gtk4::Box::new(gtk4::Orientation::Vertical, 12);
    settings.append(&text("Archon manages the system Flatpak installation. Existing per-user applications remain available through Capsule’s Apps tab. Repository trust changes require the administrator’s native tools. System package image building is unavailable until a verified builder is configured."));

    stack.add_titled(&settings, Some("settings"), "Settings");
    outer.append(&content);
    let progress = text("No active transaction");
    let cancel = gtk4::Button::with_label("Cancel active transaction");
    let footer = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
    progress.set_hexpand(true);
    footer.append(&progress);
    footer.append(&cancel);
    outer.append(&footer);
    cancel.connect_clicked({
        let progress = progress.clone();
        move |_| {
            let id = ACTIVE.with(|active| active.borrow().clone());
            let progress = progress.clone();
            glib::MainContext::default().spawn_local(async move {
                if let Some(id) = id {
                    match call("CancelTransaction", Some(&(id,).to_variant()), false).await {
                        Ok(_) => progress
                            .set_text("Cancellation requested; waiting for the app manager"),
                        Err(e) => progress.set_text(&format!("Cancellation unavailable: {e}")),
                    }
                } else {
                    progress.set_text("No active transaction started by this window")
                }
            });
        }
    });
    let weak = progress.downgrade();
    glib::MainContext::default().spawn_local(async move {
        let Ok(bus) = gio::bus_get_future(
            if cfg!(debug_assertions) && std::env::var_os("NIZHO_ARCHON_TEST_SESSION").is_some() {
                gio::BusType::Session
            } else {
                gio::BusType::System
            },
        )
        .await
        else {
            return;
        };
        let retained = bus.clone();
        let subscription = bus.signal_subscribe(
            Some(BUS),
            Some(IFACE),
            None,
            Some(PATH),
            None,
            gio::DBusSignalFlags::NONE,
            move |_, _, _, _, name, value| {
                let Some(progress) = weak.upgrade() else {
                    return;
                };
                match name {
                    "TransactionProgress" => {
                        if let Some((_, percent, detail)) = value.get::<(String, u32, String)>() {
                            progress.set_text(&format!("{percent}% · {detail}"))
                        }
                    }
                    "TransactionCompleted" => progress.set_text(
                        "Transaction completed. Refresh Installed or Updates to view the result.",
                    ),
                    "TransactionFailed" => {
                        if let Some((_, detail)) = value.get::<(String, String)>() {
                            progress.set_text(&format!("Transaction failed: {detail}"))
                        }
                    }
                    _ => {}
                }
            },
        );
        let subscription = std::cell::RefCell::new(Some(subscription));
        if let Some(progress) = progress.parent() {
            progress.connect_unrealize(move |_| {
                if let Some(id) = subscription.borrow_mut().take() {
                    retained.signal_unsubscribe(id)
                }
            });
        }
    });
    outer
}
