mod bridge;
mod monitor;

use bridge::{bridge_request, bridge_status, BridgeManager};
use monitor::{local_core_start, local_core_status, LocalCoreManager};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let local_core = LocalCoreManager::default();
    let result = tauri::Builder::default()
        .manage(BridgeManager::default())
        .manage(local_core.clone())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            bridge_request,
            bridge_status,
            local_core_start,
            local_core_status
        ])
        .run(tauri::generate_context!());
    local_core.shutdown();
    result.expect("error while running KIIIe Control Lab");
}
