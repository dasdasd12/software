/**
 * @file agent_launcher.c
 * @brief Keyboard Event Mapping — One-click AI Agent Launch / Switch
 *
 * Integrates with the keyboard routing layer. Maps physical key combinations
 * to AI agent control functions. Runs on V5F core.
 */

#include "agent_manager.h"
#include <string.h>

/*============================================================================*
 *  Configurable Key Mappings
 *============================================================================*
 *  These map to the keycodes produced by the keyboard logic layer.
 *  Adjust according to your keymap / layer configuration.
 */

#ifndef KEY_FN
#define KEY_FN          0xFF  /* Fn/meta key placeholder */
#endif

#ifndef KEY_F1
#define KEY_F1          0x3A
#endif

#ifndef KEY_F2
#define KEY_F2          0x3B
#endif

#ifndef KEY_ENTER
#define KEY_ENTER       0x28
#endif

#ifndef KEY_ESC
#define KEY_ESC         0x29
#endif

/* AI control key assignments (Fn + key) */
#define KEY_AI_SWITCH   KEY_F1   /* Fn+F1: toggle active agent (Claude <-> Codex) */
#define KEY_AI_LAUNCH   KEY_F2   /* Fn+F2: launch / resume current agent */
#define KEY_AI_CONFIRM  KEY_ENTER /* Enter: confirm permission request */
#define KEY_AI_CANCEL   KEY_ESC   /* Esc: deny permission request / cancel */

/*============================================================================*
 *  Persistent State
 *============================================================================*/

/* Current active AI agent — persisted across reboots via LittleFS */
static agent_type_t g_active_agent = AGENT_CLAUDE;

/* Flag indicating whether Fn key is currently held */
static bool g_fn_held = false;

/*============================================================================*
 *  UI Stubs
 *============================================================================*
 *  These functions should be wired to the LVGL / screen renderer.
 *  Provided as weak stubs for compilation without full UI stack.
 */

__attribute__((weak)) void ui_show_agent_switch(agent_type_t agent)
{
    /* TODO: render agent icon + name on screen (e.g., "Claude" or "Codex") */
    (void)agent;
}

__attribute__((weak)) void ui_show_launch_feedback(agent_type_t agent, const char* session_id)
{
    /* TODO: show "Launching <Agent>..." or "Resuming <session_id>" toast */
    (void)agent;
    (void)session_id;
}

__attribute__((weak)) void ui_show_permission_dialog(const char* request_id,
                                                      const char* tool,
                                                      const char* description,
                                                      uint32_t timeout_sec)
{
    /* TODO: render modal dialog with Approve / Deny buttons + countdown */
    (void)request_id;
    (void)tool;
    (void)description;
    (void)timeout_sec;
}

__attribute__((weak)) void ui_hide_permission_dialog(void)
{
    /* TODO: dismiss modal dialog */
}

/*============================================================================*
 *  Persistence Stubs
 *============================================================================*/

__attribute__((weak)) int persist_active_agent(agent_type_t agent)
{
    /* TODO: write to LittleFS (e.g., /flash/agent_config.bin) */
    (void)agent;
    return 0;
}

__attribute__((weak)) agent_type_t load_active_agent(void)
{
    /* TODO: read from LittleFS; default to AGENT_CLAUDE if not found */
    return AGENT_CLAUDE;
}

/*============================================================================*
 *  Core Handlers
 *============================================================================*/

static void on_ai_switch_key(void)
{
    /* Toggle between Claude and Codex */
    g_active_agent = (g_active_agent == AGENT_CLAUDE) ? AGENT_CODEX : AGENT_CLAUDE;

    /* Update screen */
    ui_show_agent_switch(g_active_agent);

    /* Persist preference */
    persist_active_agent(g_active_agent);
}

static void on_ai_launch_key(void)
{
    const session_info_t* sess = session_manager_get_latest(g_active_agent);

    if (sess && (sess->state == STATE_WORKING || sess->state == STATE_PAUSED)) {
        /* Resume existing active session */
        ui_show_launch_feedback(g_active_agent, sess->session_id);
        agent_send_launch(g_active_agent, sess->session_id, "");
    } else {
        /* Start fresh session */
        ui_show_launch_feedback(g_active_agent, "new");
        agent_send_launch(g_active_agent, "new", "");
    }
}

static void on_ai_confirm_key(void)
{
    /* Approve pending permission request */
    /* In a real implementation, track the pending request_id from the dialog */
    /* For now, send approval for the most recent request (simplified) */
    /* TODO: integrate with agent_manager pending request tracking */
    agent_send_permission_response("last_request", true);
    ui_hide_permission_dialog();
}

static void on_ai_cancel_key(void)
{
    /* Deny pending permission request or interrupt current task */
    agent_send_permission_response("last_request", false);

    const session_info_t* sess = session_manager_get_latest(g_active_agent);
    if (sess && (sess->state == STATE_WORKING || sess->state == STATE_WAITING_PERMISSION)) {
        agent_send_interrupt(sess->session_id);
    }
    ui_hide_permission_dialog();
}

/*============================================================================*
 *  Public API — Keyboard Event Router
 *============================================================================*/

void agent_launcher_init(void)
{
    g_active_agent = load_active_agent();
    g_fn_held = false;
}

void agent_launcher_handle_key_event(uint8_t keycode, bool pressed)
{
    /* Track Fn key state */
    if (keycode == KEY_FN) {
        g_fn_held = pressed;
        return;
    }

    /* Only act on key press (not release), and only when Fn is held */
    if (!pressed || !g_fn_held) {
        return;
    }

    switch (keycode) {
        case KEY_AI_SWITCH:
            on_ai_switch_key();
            break;

        case KEY_AI_LAUNCH:
            on_ai_launch_key();
            break;

        case KEY_AI_CONFIRM:
            on_ai_confirm_key();
            break;

        case KEY_AI_CANCEL:
            on_ai_cancel_key();
            break;

        default:
            /* Not an AI control key; ignore */
            break;
    }
}

agent_type_t agent_launcher_get_active(void)
{
    return g_active_agent;
}

void agent_launcher_set_active(agent_type_t agent)
{
    g_active_agent = agent;
    persist_active_agent(agent);
}
