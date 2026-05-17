// Prevents an additional console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, Runtime};

/// Global handle to the companion-bin child process.
struct ServerProcess(Mutex<Option<Child>>);

/// Locate the bundled `companion-bin` executable next to the current binary.
fn find_companion_bin(app: &AppHandle) -> PathBuf {
    // In release bundles Tauri places bundle resources alongside the binary.
    let mut path = app
        .path()
        .resource_dir()
        .expect("resource dir must exist")
        .join("companion-bin");

    // Windows uses .exe extension.
    #[cfg(target_os = "windows")]
    path.set_extension("exe");

    path
}

/// Poll `127.0.0.1:8082` until it accepts connections or the deadline passes.
fn wait_for_server(timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if TcpStream::connect("127.0.0.1:8082").is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    false
}

/// Spawn the Python binary and store the handle.
fn spawn_server(app: &AppHandle) {
    let bin = find_companion_bin(app);
    let state = app.state::<ServerProcess>();
    let mut guard = state.0.lock().unwrap();

    if guard.is_some() {
        // Already running.
        return;
    }

    match Command::new(&bin)
        .env("COMPANION_BIND", "127.0.0.1:8082")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(child) => {
            *guard = Some(child);
        }
        Err(e) => {
            eprintln!("companion: failed to spawn {}: {e}", bin.display());
        }
    }
}

/// Stop the running server child process (SIGTERM / TerminateProcess).
fn stop_server(app: &AppHandle) {
    let state = app.state::<ServerProcess>();
    let mut guard = state.0.lock().unwrap();
    if let Some(mut child) = guard.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn open_dashboard<R: Runtime>(app: &AppHandle<R>) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.set_focus();
    }
}

fn build_tray_menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let open = MenuItem::with_id(app, "open", "Open Dashboard", true, None::<&str>)?;
    let stop = MenuItem::with_id(app, "stop", "Stop Server", true, None::<&str>)?;
    let start = MenuItem::with_id(app, "start", "Start Server", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    Menu::with_items(app, &[&open, &start, &stop, &quit])
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(ServerProcess(Mutex::new(None)))
        .setup(|app| {
            // Spawn the Python server immediately.
            spawn_server(app.handle());

            // Wait up to 5 s for the server to be ready.
            let ready = wait_for_server(Duration::from_secs(5));
            if ready {
                if let Some(win) = app.get_webview_window("main") {
                    let _ = win.show();
                }
            } else {
                eprintln!("companion: server did not become ready within 5 s");
            }

            // Build tray.
            let menu = build_tray_menu(app.handle())?;
            TrayIconBuilder::new()
                .icon(app.default_window_icon().cloned().unwrap())
                .tooltip("Companion")
                .menu(&menu)
                .on_menu_event({
                    let handle = app.handle().clone();
                    move |_tray, event| match event.id().as_ref() {
                        "open" => open_dashboard(&handle),
                        "start" => {
                            spawn_server(&handle);
                            let _ = wait_for_server(Duration::from_secs(5));
                        }
                        "stop" => stop_server(&handle),
                        "quit" => {
                            stop_server(&handle);
                            handle.exit(0);
                        }
                        _ => {}
                    }
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        open_dashboard(tray.app_handle());
                    }
                })
                .build(app)?;

            Ok(())
        })
        .on_window_event(|window, event| {
            // Hide instead of close so the server keeps running.
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                window.hide().unwrap();
                api.prevent_close();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

fn main() {
    run();
}
