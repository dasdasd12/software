#ifndef AGENT_PROTOCOL_H
#define AGENT_PROTOCOL_H

#include <stdint.h>
#include <stdbool.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Maximum lengths for string fields */
#define AGENT_SESSION_ID_MAX_LEN    32
#define AGENT_REQUEST_ID_MAX_LEN    32
#define AGENT_TYPE_MAX_LEN          16
#define AGENT_STATE_MAX_LEN         24
#define AGENT_TOOL_NAME_MAX_LEN     32
#define AGENT_DESCRIPTION_MAX_LEN   256
#define AGENT_DELTA_MAX_LEN         1024
#define AGENT_ERROR_MSG_MAX_LEN     256
#define AGENT_SUMMARY_MAX_LEN       256
#define AGENT_PAYLOAD_MAX_LEN       2048
#define AGENT_DEVICE_ID_MAX_LEN     32

/* Maximum number of sessions in local cache */
#define AGENT_SESSION_CACHE_MAX     50

/* Default permission request timeout in seconds */
#define AGENT_PERMISSION_TIMEOUT_SEC 30

/**
 * @brief AI Agent type enumeration
 */
typedef enum {
    AGENT_CLAUDE = 0,
    AGENT_CODEX  = 1,
    AGENT_ALL    = 2
} agent_type_t;

/**
 * @brief AI Agent task state enumeration (13-state FSM)
 */
typedef enum {
    STATE_IDLE = 0,
    STATE_CONNECTING,
    STATE_SUBMITTED,
    STATE_WORKING,
    STATE_RUNNING,
    STATE_THINKING,
    STATE_EXECUTING,
    STATE_WAITING_PERMISSION,
    STATE_WAITING_INPUT,
    STATE_PAUSED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_CANCELLED,
    STATE_ERROR,
    STATE_TIMEOUT,
    STATE_OFFLINE
} agent_state_t;

/**
 * @brief Convert agent_type_t to string
 */
static inline const char* agent_type_to_str(agent_type_t agent) {
    switch (agent) {
        case AGENT_CLAUDE: return "claude";
        case AGENT_CODEX:  return "codex";
        case AGENT_ALL:    return "all";
        default:           return "unknown";
    }
}

/**
 * @brief Convert string to agent_type_t
 */
static inline agent_type_t agent_type_from_str(const char* str) {
    if (!str) return AGENT_CLAUDE;
    if (str[0] == 'c' && str[1] == 'l') return AGENT_CLAUDE;
    if (str[0] == 'c' && str[1] == 'o') return AGENT_CODEX;
    if (str[0] == 'a') return AGENT_ALL;
    return AGENT_CLAUDE;
}

/**
 * @brief Convert agent_state_t to string
 */
static inline const char* agent_state_to_str(agent_state_t state) {
    switch (state) {
        case STATE_IDLE:              return "IDLE";
        case STATE_CONNECTING:        return "CONNECTING";
        case STATE_SUBMITTED:         return "SUBMITTED";
        case STATE_WORKING:           return "WORKING";
        case STATE_RUNNING:           return "RUNNING";
        case STATE_THINKING:          return "THINKING";
        case STATE_EXECUTING:         return "EXECUTING";
        case STATE_WAITING_PERMISSION: return "WAITING_PERMISSION";
        case STATE_WAITING_INPUT:     return "WAITING_INPUT";
        case STATE_PAUSED:            return "PAUSED";
        case STATE_COMPLETED:         return "COMPLETED";
        case STATE_FAILED:            return "FAILED";
        case STATE_CANCELLED:         return "CANCELLED";
        case STATE_ERROR:             return "ERROR";
        case STATE_TIMEOUT:           return "TIMEOUT";
        case STATE_OFFLINE:           return "OFFLINE";
        default:                      return "UNKNOWN";
    }
}

/**
 * @brief Convert string to agent_state_t
 */
static inline agent_state_t agent_state_from_str(const char* str) {
    if (!str) return STATE_IDLE;
    if (strcmp(str, "IDLE") == 0) return STATE_IDLE;
    if (strcmp(str, "CONNECTING") == 0) return STATE_CONNECTING;
    if (strcmp(str, "SUBMITTED") == 0) return STATE_SUBMITTED;
    if (strcmp(str, "WORKING") == 0) return STATE_WORKING;
    if (strcmp(str, "RUNNING") == 0) return STATE_RUNNING;
    if (strcmp(str, "THINKING") == 0) return STATE_THINKING;
    if (strcmp(str, "EXECUTING") == 0) return STATE_EXECUTING;
    if (strcmp(str, "WAITING_PERMISSION") == 0) return STATE_WAITING_PERMISSION;
    if (strcmp(str, "WAITING_INPUT") == 0) return STATE_WAITING_INPUT;
    if (strcmp(str, "PAUSED") == 0) return STATE_PAUSED;
    if (strcmp(str, "COMPLETED") == 0) return STATE_COMPLETED;
    if (strcmp(str, "FAILED") == 0) return STATE_FAILED;
    if (strcmp(str, "CANCELLED") == 0) return STATE_CANCELLED;
    if (strcmp(str, "ERROR") == 0) return STATE_ERROR;
    if (strcmp(str, "TIMEOUT") == 0) return STATE_TIMEOUT;
    if (strcmp(str, "OFFLINE") == 0) return STATE_OFFLINE;
    return STATE_IDLE;
}

/**
 * @brief Session information structure
 */
typedef struct {
    char     session_id[AGENT_SESSION_ID_MAX_LEN];
    agent_type_t agent;
    agent_state_t state;
    uint32_t created_at;
    uint32_t updated_at;
} session_info_t;

/**
 * @brief Unified message type enumeration
 */
typedef enum {
    MSG_AGENT_LAUNCH = 0,
    MSG_PERMISSION_RESPONSE,
    MSG_INTERRUPT,
    MSG_LIST_SESSIONS,
    MSG_HEARTBEAT,
    MSG_TASK_UPDATE,
    MSG_AGENT_MESSAGE_DELTA,
    MSG_PERMISSION_REQUEST,
    MSG_PERMISSION_ACK,
    MSG_TASK_COMPLETED,
    MSG_TASK_FAILED,
    MSG_SESSION_LIST,
    MSG_ERROR,
    MSG_UNKNOWN
} msg_type_t;

/**
 * @brief Convert msg_type_t to string
 */
static inline const char* msg_type_to_str(msg_type_t type) {
    switch (type) {
        case MSG_AGENT_LAUNCH:         return "agent_launch";
        case MSG_PERMISSION_RESPONSE:  return "permission_response";
        case MSG_INTERRUPT:            return "interrupt";
        case MSG_LIST_SESSIONS:        return "list_sessions";
        case MSG_HEARTBEAT:            return "heartbeat";
        case MSG_TASK_UPDATE:          return "task_update";
        case MSG_AGENT_MESSAGE_DELTA:  return "agent_message_delta";
        case MSG_PERMISSION_REQUEST:   return "permission_request";
        case MSG_PERMISSION_ACK:       return "permission_ack";
        case MSG_TASK_COMPLETED:       return "task_completed";
        case MSG_TASK_FAILED:          return "task_failed";
        case MSG_SESSION_LIST:         return "session_list";
        case MSG_ERROR:                return "error";
        default:                       return "unknown";
    }
}

/**
 * @brief Unified message structure (device <-> Bridge Server)
 */
typedef struct {
    msg_type_t type;
    char       session_id[AGENT_SESSION_ID_MAX_LEN];
    agent_type_t agent;
    agent_state_t state;
    char       request_id[AGENT_REQUEST_ID_MAX_LEN];
    bool       approved;
    char       tool[AGENT_TOOL_NAME_MAX_LEN];
    char       description[AGENT_DESCRIPTION_MAX_LEN];
    char       delta[AGENT_DELTA_MAX_LEN];
    char       summary[AGENT_SUMMARY_MAX_LEN];
    char       error_code[AGENT_TOOL_NAME_MAX_LEN];
    char       error_message[AGENT_ERROR_MSG_MAX_LEN];
    char       device_id[AGENT_DEVICE_ID_MAX_LEN];
    uint32_t   timeout_sec;
    uint32_t   timestamp;
    char       payload[AGENT_PAYLOAD_MAX_LEN];  /* Raw JSON or extra data */
} agent_message_t;

/**
 * @brief Session list payload structure
 */
typedef struct {
    session_info_t sessions[AGENT_SESSION_CACHE_MAX];
    uint8_t        count;
} session_list_t;

#ifdef __cplusplus
}
#endif

#endif /* AGENT_PROTOCOL_H */
