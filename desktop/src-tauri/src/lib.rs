mod bridge;

use bridge::{bridge_request, bridge_status, BridgeManager};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(BridgeManager::default())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![bridge_request, bridge_status])
        .run(tauri::generate_context!())
        .expect("error while running KIIIe Control Lab");
}
