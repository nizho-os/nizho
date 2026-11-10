//! Devices: rows that open Plasma's own settings for screens, keyboards and printers.
//!
//! Each row names a Plasma settings module. A row is shown only when that module is installed,
//! so a missing package never leaves a row that opens nothing. Plasma 6 installs a module as
//! `plasma/kcms/systemsettings/<name>.so` under a Qt plugin folder (kcmutils_add_qml_kcm).

use std::path::{Path, PathBuf};

/// One row: the Plasma module it opens, its title and its caption.
pub struct Device {
    pub module: &'static str,
    pub title: &'static str,
    pub subtitle: &'static str,
    pub icon: &'static str,
}

pub const DEVICES: &[Device] = &[
    Device {
        module: "kcm_kscreen",
        title: "Display",
        subtitle: "Resolution, scale, refresh rate and how screens are arranged.",
        icon: "video-display-symbolic",
    },
    Device {
        module: "kcm_keyboard",
        title: "Keyboard",
        subtitle: "Layouts, languages and key repeat.",
        icon: "input-keyboard-symbolic",
    },
    Device {
        module: "kcm_printer_manager",
        title: "Printers",
        subtitle: "Add a printer and see what is waiting to print.",
        icon: "printer-symbolic",
    },
];

/// Where Qt looks for plugins: QT_PLUGIN_PATH first, then Fedora's and other common folders.
pub fn plugin_dirs(qt_plugin_path: Option<&str>) -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = qt_plugin_path
        .unwrap_or("")
        .split(':')
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .collect();
    for d in ["/usr/lib64/qt6/plugins", "/usr/lib/qt6/plugins", "/usr/lib/x86_64-linux-gnu/qt6/plugins"] {
        dirs.push(PathBuf::from(d));
    }
    dirs
}

/// True when a Plasma 6 settings module called `module` is installed under one of `dirs`.
pub fn module_installed(module: &str, dirs: &[PathBuf]) -> bool {
    if module.is_empty() || module.contains('/') {
        return false;
    }
    dirs.iter().any(|d| is_file(&d.join("plasma/kcms/systemsettings").join(format!("{module}.so"))))
}

fn is_file(p: &Path) -> bool {
    p.metadata().map(|m| m.is_file()).unwrap_or(false)
}

/// The rows to show, in order: those whose module `installed` says is there.
pub fn available<'a>(devices: &'a [Device], installed: impl Fn(&str) -> bool) -> Vec<&'a Device> {
    devices.iter().filter(|d| installed(d.module)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Dir(PathBuf);
    impl Dir {
        fn new(tag: &str) -> Self {
            let p = std::env::temp_dir().join(format!("nizho-devices-test-{}-{tag}", std::process::id()));
            let _ = std::fs::remove_dir_all(&p);
            std::fs::create_dir_all(&p).unwrap();
            Dir(p)
        }
        fn module(&self, name: &str) {
            let d = self.0.join("plasma/kcms/systemsettings");
            std::fs::create_dir_all(&d).unwrap();
            std::fs::write(d.join(format!("{name}.so")), b"").unwrap();
        }
    }
    impl Drop for Dir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn only_installed_modules_get_a_row() {
        let d = Dir::new("some");
        d.module("kcm_kscreen");
        d.module("kcm_keyboard");
        let dirs = vec![d.0.clone()];
        let rows: Vec<_> = available(DEVICES, |m| module_installed(m, &dirs)).iter().map(|r| r.module).collect();
        assert_eq!(rows, ["kcm_kscreen", "kcm_keyboard"]);
    }

    #[test]
    fn no_modules_means_no_rows() {
        let d = Dir::new("none");
        let dirs = vec![d.0.clone()];
        assert!(available(DEVICES, |m| module_installed(m, &dirs)).is_empty());
    }

    #[test]
    fn a_module_in_any_plugin_folder_counts() {
        let a = Dir::new("a");
        let b = Dir::new("b");
        b.module("kcm_printer_manager");
        let dirs = vec![a.0.clone(), b.0.clone()];
        assert!(module_installed("kcm_printer_manager", &dirs));
        assert!(!module_installed("kcm_kscreen", &dirs));
    }

    #[test]
    fn a_folder_with_the_module_name_is_not_a_module() {
        let d = Dir::new("folder");
        std::fs::create_dir_all(d.0.join("plasma/kcms/systemsettings/kcm_kscreen.so")).unwrap();
        assert!(!module_installed("kcm_kscreen", &[d.0.clone()]));
    }

    #[test]
    fn odd_names_are_refused() {
        let d = Dir::new("odd");
        d.module("kcm_kscreen");
        assert!(!module_installed("", &[d.0.clone()]));
        assert!(!module_installed("../systemsettings/kcm_kscreen", &[d.0.clone()]));
    }

    #[test]
    fn qt_plugin_path_comes_first_and_empty_parts_are_skipped() {
        let dirs = plugin_dirs(Some("/opt/a::/opt/b"));
        assert_eq!(dirs[0], PathBuf::from("/opt/a"));
        assert_eq!(dirs[1], PathBuf::from("/opt/b"));
        assert!(dirs.contains(&PathBuf::from("/usr/lib64/qt6/plugins")));
        assert_eq!(plugin_dirs(None)[0], PathBuf::from("/usr/lib64/qt6/plugins"));
    }

    #[test]
    fn every_row_opens_a_plasma_module() {
        for d in DEVICES {
            assert!(d.module.starts_with("kcm_"), "{}", d.module);
            assert!(!d.title.is_empty() && d.subtitle.ends_with('.'));
        }
    }
}
