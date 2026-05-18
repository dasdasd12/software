#ifndef AGENT_MANAGER_H
#define AGENT_MANAGER_H

#include "agent_protocol.h"

#ifdef __cplusplus
extern "C" {
#endif

/*============================================================================*
 *  AI Agent Manager — Session Local Cache + 13-State FSM
 *============================================================================*
 *  Responsibilities:
 *    - Maintain local session cache (up to AGENT_SESSION_CACHE_MAX entries)
 *    - Drive the 13-state FSM based on unified events from Bridge Server
 *    - Provide callbacks for UI layer to render state changes
 *    - Persist active agent selection and session metadata
 *============================================================================*/

/**
 * @brief UI callback function type for state change notifications
 */
typedef void (*ui_state_callback_t)(const session_info_t* session);

/**
 * @brief UI callback function type for incoming message deltas (stream text)
 */
typedef void (*ui_delta_callback_t)(const char* session_id, const char* delta);

/**
 * @brief UI callback function type for permission request popups
 */
typedef void (*ui_permission_callback_t)(const char* request_id,
                                          const char* tool,
                                          const char* description,
                                          uint32_t timeout_sec);

/**
 * @brief Callback function type for sending messages to Bridge Server
 */
typedef int (*transport_send_t)(const char* json_line, uint32_t len);

/**
 * @brief Agent manager configuration
 */
typedef struct {
    transport_send_t    send_fn;           /* WebSocket/transport send function */
    ui_state_callback_t state_cb;          /* Called on any state transition */
    ui_delta_callback_t delta_cb;          /* Called on agent_message_delta */
    ui_permission_callback_t permission_cb; /* Called on permission_request */
    uint32_t            permission_timeout_sec; /* Default 30s */
} agent_manager_config_t;

/**
 * @brief Initialize the agent manager
 *
 * @param cfg  Pointer to configuration structure (must remain valid)
 * @return 0 on success, negative on error
 */
int agent_manager_init(const agent_manager_config_t* cfg);

/**
 * @brief Deinitialize the agent manager and free resources
 */
void agent_manager_deinit(void);

/**
 * @brief Handle an incoming unified message from Bridge Server
 *
 * This function drives the 13-state FSM and invokes UI callbacks.
 *
 * @param msg  Pointer to parsed message
 * @return 0 on success, negative on error
 */
int agent_manager_handle_message(const agent_message_t* msg);

/**
 * @brief Send an agent_launch request to Bridge Server
 *
 * @param agent      Target AI agent
 * @param session_id "new" to create, or existing session ID to resume
 * @param context    Optional initial context / prompt
 * @return 0 on success, negative on error
 */
int agent_send_launch(agent_type_t agent,
                      const char* session_id,
                      const char* context);

/**
 * @brief Send a permission_response to Bridge Server
 *
 * @param request_id  The request_id from permission_request
 * @param approved    true to approve, false to deny
 * @return 0 on success, negative on error
 */
int agent_send_permission_response(const char* request_id, bool approved);

/**
 * @brief Return the current pending permission request id, if any.
 *
 * @return Pointer to request id string, or NULL when no permission is pending
 */
const char* agent_manager_get_pending_request_id(void);

/**
 * @brief Check whether a permission request is currently pending.
 *
 * @return true if a pending request id is available
 */
bool agent_manager_has_pending_permission(void);

/**
 * @brief Send an interrupt request to Bridge Server
 *
 * @param session_id  Target session to interrupt
 * @return 0 on success, negative on error
 */
int agent_send_interrupt(const char* session_id);

/**
 * @brief Send a list_sessions query to Bridge Server
 *
 * @param agent  AGENT_ALL for all, or specific agent
 * @return 0 on success, negative on error
 */
int agent_send_list_sessions(agent_type_t agent);

/**
 * @brief Send a heartbeat message
 *
 * @param device_id  Unique device identifier
 * @return 0 on success, negative on error
 */
int agent_send_heartbeat(const char* device_id);

/**
 * @brief Get the latest session for a given agent
 *
 * @param agent  Target AI agent
 * @return Pointer to session_info_t, or NULL if none found
 */
const session_info_t* session_manager_get_latest(agent_type_t agent);

/**
 * @brief Get session by ID
 *
 * @param session_id  Session identifier
 * @return Pointer to session_info_t, or NULL if not found
 */
const session_info_t* session_manager_get_by_id(const char* session_id);

/**
 * @brief Get all cached sessions
 *
 * @param out_list  Output buffer (caller provides array of AGENT_SESSION_CACHE_MAX)
 * @return Number of sessions written
 */
uint8_t session_manager_get_all(session_info_t* out_list);

/**
 * @brief Persist session cache to non-volatile storage (LittleFS/SDRAM)
 *
 * Should be called periodically or after significant state changes.
 * @return 0 on success, negative on error
 */
int session_manager_persist(void);

/**
 * @brief Load session cache from non-volatile storage
 *
 * Called once during agent_manager_init.
 * @return 0 on success, negative on error
 */
int session_manager_load(void);

/**
 * @brief Remove expired / completed sessions beyond retention limit
 *
 * LRU eviction: keeps AGENT_SESSION_CACHE_MAX most recently updated sessions.
 * @return Number of sessions removed
 */
uint8_t session_manager_gc(void);

#ifdef __cplusplus
}
#endif

#endif /* AGENT_MANAGER_H */
