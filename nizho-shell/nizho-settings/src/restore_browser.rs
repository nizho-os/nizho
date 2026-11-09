//! The "Get a file back" window: browse the files in a saved backup, tick one, and restore it.
//! The listing logic lives in backup.rs; this file only draws it.

use crate::backup;
use adw::prelude::*;
use gtk4::glib;
use libadwaita as adw;
use std::cell::{Cell, RefCell};
use std::rc::Rc;

/// What the person chose in the window.
pub enum Pick {
    /// A path from the list, and the backup it was listed in.
    Item { path: String, snapshot: String },
    /// They asked to type a path instead.
    TypeInstead,
}

fn today() -> (i32, u32, u32) {
    glib::DateTime::now_local()
        .map(|d| (d.year(), d.month() as u32, d.day_of_month() as u32))
        .unwrap_or((1970, 1, 1))
}

struct State {
    snaps: RefCell<Vec<backup::Snap>>,
    folder: RefCell<String>,
    selected: RefCell<Option<String>>,
    checks: RefCell<Vec<(String, gtk4::CheckButton)>>,
    generation: Cell<u32>,
}

/// Opens the window. `done` runs once, when the person restores something or asks to type a path.
pub fn open(parent: &adw::ApplicationWindow, done: impl Fn(Pick) + 'static) {
    let win = adw::Window::builder()
        .transient_for(parent)
        .modal(true)
        .title("Get a file back")
        .default_width(520)
        .default_height(560)
        .build();
    let done: Rc<dyn Fn(Pick)> = Rc::new(done);
    // Folder rows reload the list through this cell; it is emptied when the window closes.
    let load_cell: LoadCell = Rc::new(RefCell::new(None));
    {
        let cell = load_cell.clone();
        win.connect_close_request(move |_| {
            *cell.borrow_mut() = None;
            glib::Propagation::Proceed
        });
    }
    let st = Rc::new(State {
        snaps: RefCell::new(vec![]),
        folder: RefCell::new(String::new()),
        selected: RefCell::new(None),
        checks: RefCell::new(vec![]),
        generation: Cell::new(0),
    });

    let header = adw::HeaderBar::new();
    let back = gtk4::Button::with_label("Back");
    back.set_sensitive(false);
    header.pack_start(&back);

    let when = gtk4::DropDown::from_strings(&[]);
    when.set_hexpand(true);
    when.set_sensitive(false);
    let when_label = gtk4::Label::new(Some("Backup from"));
    when_label.add_css_class("dim-label");
    let path_label = gtk4::Label::new(Some("Home folder"));
    path_label.set_xalign(0.0);
    path_label.set_wrap(true);
    path_label.set_selectable(false);
    path_label.add_css_class("heading");

    let top = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
    top.append(&when_label);
    top.append(&when);

    let list = gtk4::ListBox::new();
    list.set_selection_mode(gtk4::SelectionMode::None);
    list.add_css_class("boxed-list");
    list.set_activate_on_single_click(true);
    let scroll = gtk4::ScrolledWindow::new();
    scroll.set_vexpand(true);
    scroll.set_hscrollbar_policy(gtk4::PolicyType::Never);
    scroll.set_child(Some(&list));

    let message = adw::StatusPage::new();
    message.set_vexpand(true);
    let spinner = gtk4::Spinner::new();
    spinner.set_halign(gtk4::Align::Center);
    spinner.set_valign(gtk4::Align::Center);
    spinner.set_size_request(32, 32);

    let stack = gtk4::Stack::new();
    stack.add_named(&scroll, Some("list"));
    stack.add_named(&message, Some("message"));
    stack.add_named(&spinner, Some("loading"));
    stack.set_vexpand(true);

    let typed = gtk4::Button::with_label("Type a path instead");
    typed.add_css_class("flat");
    typed.add_css_class("link");
    typed.set_halign(gtk4::Align::Start);
    let restore = gtk4::Button::with_label("Restore selected");
    restore.add_css_class("suggested-action");
    restore.set_sensitive(false);
    let bottom = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
    typed.set_hexpand(true);
    bottom.append(&typed);
    bottom.append(&restore);

    let body = gtk4::Box::new(gtk4::Orientation::Vertical, 12);
    body.set_margin_start(16);
    body.set_margin_end(16);
    body.set_margin_top(12);
    body.set_margin_bottom(16);
    body.append(&top);
    body.append(&path_label);
    body.append(&stack);
    body.append(&bottom);

    let outer = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
    outer.append(&header);
    outer.append(&body);
    win.set_content(Some(&outer));

    let show_message = {
        let (stack, message) = (stack.clone(), message.clone());
        Rc::new(move |text: &str| {
            message.set_title(text);
            stack.set_visible_child_name("message");
        })
    };

    // Loads the current folder of the chosen backup without blocking the window.
    let load: Rc<dyn Fn()> = {
        let (st, list, stack, spinner, show_message, path_label, back, restore, when, load_cell) = (
            st.clone(), list.clone(), stack.clone(), spinner.clone(), show_message.clone(),
            path_label.clone(), back.clone(), restore.clone(), when.clone(), load_cell.clone(),
        );
        Rc::new(move || {
            let folder = st.folder.borrow().clone();
            path_label.set_text(&if folder.is_empty() { "Home folder".to_string() } else { format!("Home folder / {}", folder.replace('/', " / ")) });
            back.set_sensitive(!folder.is_empty());
            *st.selected.borrow_mut() = None;
            st.checks.borrow_mut().clear();
            restore.set_sensitive(false);
            while let Some(r) = list.first_child() {
                list.remove(&r);
            }
            let snaps = st.snaps.borrow();
            let Some(snap) = snaps.get(when.selected() as usize).cloned() else {
                return;
            };
            drop(snaps);
            let gen = st.generation.get() + 1;
            st.generation.set(gen);
            spinner.start();
            stack.set_visible_child_name("loading");
            let (st, list, stack, spinner, show_message, restore, load_cell) =
                (st.clone(), list.clone(), stack.clone(), spinner.clone(), show_message.clone(), restore.clone(), load_cell.clone());
            glib::spawn_future_local(async move {
                let f = folder.clone();
                let res = gtk4::gio::spawn_blocking(move || backup::list(&f, &snap.id))
                    .await
                    .unwrap_or_else(|_| Err("The list of saved files did not load.".into()));
                if st.generation.get() != gen {
                    return; // a newer folder or backup was asked for meanwhile
                }
                spinner.stop();
                match res {
                    Err(e) => show_message(&e),
                    Ok(entries) if entries.is_empty() => show_message("This folder is empty."),
                    Ok(entries) => {
                        let now = today();
                        for e in entries {
                            let row = adw::ActionRow::new();
                            row.set_use_markup(false);
                            row.set_title(&e.name);
                            let when_text = backup::friendly_time(&e.mtime, now);
                            row.set_subtitle(&if e.is_dir {
                                format!("Folder, changed {when_text}")
                            } else {
                                format!("{}, changed {when_text}", backup::friendly_size(e.size))
                            });
                            row.set_activatable(true);
                            let path = backup::child_path(&folder, &e.name);
                            let check = gtk4::CheckButton::new();
                            check.set_valign(gtk4::Align::Center);
                            check.update_property(&[gtk4::accessible::Property::Label(&format!("Select {}", e.name))]);
                            {
                                let (st, restore, path) = (st.clone(), restore.clone(), path.clone());
                                check.connect_toggled(move |c| {
                                    if c.is_active() {
                                        *st.selected.borrow_mut() = Some(path.clone());
                                        for (p, other) in st.checks.borrow().iter() {
                                            if *p != path && other.is_active() {
                                                other.set_active(false);
                                            }
                                        }
                                    } else if st.selected.borrow().as_deref() == Some(path.as_str()) {
                                        *st.selected.borrow_mut() = None;
                                    }
                                    restore.set_sensitive(st.selected.borrow().is_some());
                                });
                            }
                            row.add_prefix(&check);
                            st.checks.borrow_mut().push((path.clone(), check.clone()));
                            if e.is_dir {
                                row.add_suffix(&gtk4::Image::from_icon_name("go-next-symbolic"));
                                let open_folder = row_opener(&st, &load_cell, &path);
                                row.connect_activated(move |_| open_folder());
                            } else {
                                let check = check.clone();
                                row.connect_activated(move |_| check.set_active(!check.is_active()));
                            }
                            list.append(&row);
                        }
                        stack.set_visible_child_name("list");
                        if let Some(first) = list.row_at_index(0) {
                            first.grab_focus();
                        }
                    }
                }
            });
        })
    };
    *load_cell.borrow_mut() = Some(load.clone());

    {
        let (st, load) = (st.clone(), load.clone());
        back.connect_clicked(move |_| {
            let up = backup::parent_path(&st.folder.borrow());
            *st.folder.borrow_mut() = up;
            load();
        });
    }
    {
        let load = load.clone();
        when.connect_selected_notify(move |_| load());
    }
    {
        let (done, win) = (done.clone(), win.clone());
        typed.connect_clicked(move |_| {
            win.close();
            done(Pick::TypeInstead);
        });
    }
    {
        let (st, done, win, when) = (st.clone(), done.clone(), win.clone(), when.clone());
        restore.connect_clicked(move |_| {
            let path = st.selected.borrow().clone();
            let snap = st.snaps.borrow().get(when.selected() as usize).cloned();
            if let (Some(path), Some(snap)) = (path, snap) {
                win.close();
                done(Pick::Item { path, snapshot: snap.id });
            }
        });
    }
    {
        let win2 = win.clone();
        let keys = gtk4::EventControllerKey::new();
        keys.connect_key_pressed(move |_, key, _, _| {
            if key == gtk4::gdk::Key::Escape {
                win2.close();
                glib::Propagation::Stop
            } else {
                glib::Propagation::Proceed
            }
        });
        win.add_controller(keys);
    }

    win.present();

    // Load the list of backups first, newest at the top, then the home folder of the newest.
    spinner.start();
    stack.set_visible_child_name("loading");
    {
        let (st, when, show_message, load, spinner) = (st.clone(), when.clone(), show_message.clone(), load.clone(), spinner.clone());
        glib::spawn_future_local(async move {
            let res = gtk4::gio::spawn_blocking(backup::snapshots)
                .await
                .unwrap_or_else(|_| Err("The list of backups did not load.".into()));
            spinner.stop();
            match res {
                Err(e) => show_message(&e),
                Ok(s) if s.is_empty() => show_message("There are no backups yet. Back up now first."),
                Ok(s) => {
                    let now = today();
                    let labels: Vec<String> = s.iter().map(|x| backup::friendly_time(&x.time, now)).collect();
                    let refs: Vec<&str> = labels.iter().map(|x| x.as_str()).collect();
                    *st.snaps.borrow_mut() = s;
                    when.set_model(Some(&gtk4::StringList::new(&refs)));
                    when.set_selected(0);
                    when.set_sensitive(true);
                    load();
                }
            }
        });
    }
}

type LoadCell = Rc<RefCell<Option<Rc<dyn Fn()>>>>;

/// A closure that enters the folder `target` and reloads the list.
fn row_opener(st: &Rc<State>, cell: &LoadCell, target: &str) -> impl Fn() + 'static {
    let (st, cell, target) = (st.clone(), cell.clone(), target.to_string());
    move || {
        *st.folder.borrow_mut() = target.clone();
        let load = cell.borrow().clone();
        if let Some(load) = load {
            load();
        }
    }
}
