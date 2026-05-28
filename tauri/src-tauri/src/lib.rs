//! Companion Tauri 2.x desktop wrapper.
//!
//! Responsibilities:
//! * Spawn the bundled `companion-bin` (PyInstaller-frozen FastAPI server)
//!   on startup, with the bind set to `127.0.0.1:8082`.
//! * Poll the port until it accepts connections (5 s budget) before
//!   revealing the main window — this prevents the user from staring at a
//!   blank webview while uvicorn boots.
//! * Tray icon: Open Dashboard / Start / Stop / Quit.
//! * Window close → hide (server keeps running), not exit. Quit only via tray.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, Runtime};
use tauri_plugin_updater::UpdaterExt;
use serde::Serialize;

/// Global handle to the companion-bin child process.
struct ServerProcess(Mutex<Option<Child>>);

/// Locate the bundled `companion-bin` executable next to the current binary.
fn find_companion_bin(app: &AppHandle) -> PathBuf {
    // In release bundles Tauri places bundle resources alongside the binary.
    let path = app
        .path()
        .resource_dir()
        .expect("resource dir must exist")
        .join("companion-bin");

    // Windows uses .exe extension; shadow with the extended path on Windows so
    // non-Windows targets don't pay an unused `mut` warning.
    #[cfg(target_os = "windows")]
    let path = path.with_extension("exe");

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

/// Event payload emitted during update lifecycle.
#[derive(Clone, Serialize)]
struct UpdaterStatus {
    phase: String,
    message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    progress_pct: Option<f64>,
}

/// Check GitHub releases for a newer version, download + install + restart.
///
/// Non-blocking: runs in the background after the window is already shown.
/// Failures are swallowed and logged — we never want a network hiccup to
/// stop the user from working.
async fn maybe_update(app: AppHandle) {
    let _ = app.emit(
        "updater://status",
        UpdaterStatus {
            phase: "checking".into(),
            message: "Checking for updates…".into(),
            progress_pct: None,
        },
    );

    let updater = match app.updater() {
        Ok(u) => u,
        Err(e) => {
            eprintln!("companion: updater init failed: {e}");
            let _ = app.emit(
                "updater://status",
                UpdaterStatus {
                    phase: "error".into(),
                    message: format!("Updater init failed: {e}"),
                    progress_pct: None,
                },
            );
            return;
        }
    };

    let update = match updater.check().await {
        Ok(Some(update)) => update,
        Ok(None) => {
            let _ = app.emit(
                "updater://status",
                UpdaterStatus {
                    phase: "up-to-date".into(),
                    message: "Companion is up to date".into(),
                    progress_pct: None,
                },
            );
            return;
        }
        Err(e) => {
            eprintln!("companion: update check failed: {e}");
            let _ = app.emit(
                "updater://status",
                UpdaterStatus {
                    phase: "error".into(),
                    message: format!("Update check failed: {e}"),
                    progress_pct: None,
                },
            );
            return;
        }
    };

    eprintln!(
        "companion: update available — current {}, latest {}",
        update.current_version, update.version
    );

    let _ = app.emit(
        "updater://status",
        UpdaterStatus {
            phase: "downloading".into(),
            message: format!(
                "Downloading {} → {} (0%)",
                update.current_version, update.version
            ),
            progress_pct: Some(0.0),
        },
    );

    // Clone for the download progress callbacks.
    let app_clone = app.clone();
    let version = update.version.clone();
    let current = update.current_version.clone();

    match update
        .download_and_install(
            |downloaded, total| {
                let pct = match total {
                    Some(t) if t > 0 => (downloaded as f64 / t as f64 * 100.0).min(99.0),
                    _ => 0.0,
                };
                let _ = app_clone.emit(
                    "updater://status",
                    UpdaterStatus {
                        phase: "downloading".into(),
                        message: format!(
                            "Downloading {current} → {version} ({pct:.0}%)",
                        ),
                        progress_pct: Some(pct),
                    },
                );
            },
            || {
                // Download complete callback — about to restart.
                let _ = app_clone.emit(
                    "updater://status",
                    UpdaterStatus {
                        phase: "installing".into(),
                        message: "Download complete, installing…".into(),
                        progress_pct: Some(100.0),
                    },
                );
            },
        )
        .await
    {
        Ok(()) => {
            let _ = app.emit(
                "updater://status",
                UpdaterStatus {
                    phase: "restarting".into(),
                    message: "Restarting…".into(),
                    progress_pct: None,
                },
            );
            // Stop the Python server cleanly, then let Tauri restart.
            stop_server(&app);
            app.restart();
        }
        Err(e) => {
            eprintln!("companion: update install failed: {e}");
            let _ = app.emit(
                "updater://status",
                UpdaterStatus {
                    phase: "error".into(),
                    message: format!("Update install failed: {e}"),
                    progress_pct: None,
                },
            );
        }
    }
}

/// Tauri command: manually check for updates from the Settings page.
#[tauri::command]
async fn check_update(app: AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn(async move {
        maybe_update(app).await;
    });
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .manage(ServerProcess(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![check_update])
        .setup(|app| {
            // Spawn the Python server immediately.
            spawn_server(app.handle());

            // Block setup until the server is ready or we time out. The
            // bundled FastAPI does plugin discovery + MCP bootstrap on
            // startup, which can take 25-30 s on a cold boot. If we show
            // the window before the URL responds, WebKit caches a load
            // failure and the user gets a black screen with no retry.
            let ready = wait_for_server(Duration::from_secs(60));
            if !ready {
                eprintln!(
                    "companion: server did not become ready within 60 s; showing window anyway"
                );
            }
            if let Some(win) = app.get_webview_window("main") {
                // Force a navigation even if the WebView started loading
                // before the server was up — eval()'d location.reload
                // bypasses WebKit's stale-load cache.
                let _ = win.eval(
                    "window.location.replace('http://127.0.0.1:8082/ui/');",
                );
                let _ = win.show();
                let _ = win.set_focus();
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

            // Fire-and-forget update check after the window is up.
            // Runs in the Tauri async runtime so it doesn't block startup.
            let update_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                maybe_update(update_handle).await;
            });

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
