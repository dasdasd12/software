/**
 * @file agent_manager.c
 * @brief AI Agent Manager — Session Local Cache + 13-State FSM
 *
 * Runs on V5F core. Handles unified events from Bridge Server,
 * maintains local session cache, and notifies UI layer via callbacks.
 */

#include "agent_manager.h"
#include <string.h>
#include <stdio.h>
#include <stdarg.h>

/*============================================================================*
 *  Internal State
 *============================================================================*/

static agent_manager_config_t s_cfg;
static bool                   s_initialized = false;

/* Local session cache */
static session_info_t s_cache[AGENT_SESSION_CACHE_MAX];
static uint8_t        s_cache_count = 0;

/* Current permission request being waited on */
static char           s_pending_request_id[AGENT_REQUEST_ID_MAX_LEN];
static uint32_t       s_permission_deadline = 0;

/*============================================================================*
 *  Helpers
 *============================================================================*/

static int _send_json(const char* fmt, ...)
{
    char buf[AGENT_PAYLOAD_MAX_LEN];
    va_list args;
    va_start(args, fmt);
    int len = vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);

    if (len < 0 || (size_t)len >= sizeof(buf)) {
        return -1;
    }
    if (s_cfg.send_fn) {
        return s_cfg.send_fn(buf, (uint32_t)len);
    }
    return -1;
}

static session_info_t* _find_session(const char* session_id)
{
    for (uint8_t i = 0; i < s_cache_count; i++) {
        if (strncmp(s_cache[i].session_id, session_id, AGENT_SESSION_ID_MAX_LEN) == 0) {
            return &s_cache[i];
        }
    }
    return NULL;
}

static session_info_t* _alloc_session_slot(void)
{
    if (s_cache_count < AGENT_SESSION_CACHE_MAX) {
        return &s_cache[s_cache_count++];
    }
    /* LRU eviction: find oldest updated session */
    session_info_t* oldest = &s_cache[0];
    for (uint8_t i = 1; i < AGENT_SESSION_CACHE_MAX; i++) {
        if (s_cache[i].updated_at < oldest->updated_at) {
            oldest = &s_cache[i];
        }
    }
    return oldest;
}

static void _notify_state_change(const session_info_t* sess)
{
    if (s_cfg.state_cb) {
        s_cfg.state_cb(sess);
    }
}

static void _notify_delta(const char* session_id, const char* delta)
{
    if (s_cfg.delta_cb) {
        s_cfg.delta_cb(session_id, delta);
    }
}

static void _notify_permission(const char* req_id, const char* tool,
                                const char* desc, uint32_t timeout)
{
    if (s_cfg.permission_cb) {
        s_cfg.permission_cb(req_id, tool, desc, timeout);
    }
}

/*============================================================================*
 *  Public API
 *============================================================================*/

int agent_manager_init(const agent_manager_config_t* cfg)
{
    if (s_initialized) {
        return -1;
    }
    memset(&s_cfg, 0, sizeof(s_cfg));
    memset(s_cache, 0, sizeof(s_cache));
    s_cache_count = 0;
    s_pending_request_id[0] = '\0';
    s_permission_deadline = 0;

    if (cfg) {
        s_cfg = *cfg;
    }
    s_initialized = true;

    /* Attempt to load persisted sessions */
    session_manager_load();
    return 0;
}

void agent_manager_deinit(void)
{
    if (!s_initialized) {
        return;
    }
    session_manager_persist();
    memset(&s_cfg, 0, sizeof(s_cfg));
    memset(s_cache, 0, sizeof(s_cache));
    s_cache_count = 0;
    s_initialized = false;
}

int agent_manager_handle_message(const agent_message_t* msg)
{
    if (!s_initialized || !msg) {
        return -1;
    }

    switch (msg->type) {
        case MSG_TASK_UPDATE: {
            session_info_t* sess = _find_session(msg->session_id);
            if (!sess) {
                sess = _alloc_session_slot();
                strncpy(sess->session_id, msg->session_id, AGENT_SESSION_ID_MAX_LEN - 1);
                sess->session_id[AGENT_SESSION_ID_MAX_LEN - 1] = '\0';
                sess->agent = msg->agent;
                sess->created_at = msg->timestamp;
            }
            sess->state = msg->state;
            sess->updated_at = msg->timestamp;
            _notify_state_change(sess);
            break;
        }

        case MSG_AGENT_MESSAGE_DELTA: {
            session_info_t* sess = _find_session(msg->session_id);
            if (sess) {
                sess->updated_at = msg->timestamp;
            }
            _notify_delta(msg->session_id, msg->delta);
            break;
        }

        case MSG_PERMISSION_REQUEST: {
            session_info_t* sess = _find_session(msg->session_id);
            if (sess) {
                sess->state = STATE_WAITING_PERMISSION;
                sess->updated_at = msg->timestamp;
                _notify_state_change(sess);
            }
            strncpy(s_pending_request_id, msg->request_id, AGENT_REQUEST_ID_MAX_LEN - 1);
            s_pending_request_id[AGENT_REQUEST_ID_MAX_LEN - 1] = '\0';
            s_permission_deadline = msg->timestamp + msg->timeout_sec;
            _notify_permission(msg->request_id, msg->tool, msg->description, msg->timeout_sec);
            break;
        }

        case MSG_TASK_COMPLETED: {
            session_info_t* sess = _find_session(msg->session_id);
            if (sess) {
                sess->state = STATE_COMPLETED;
                sess->updated_at = msg->timestamp;
                _notify_state_change(sess);
            }
            session_manager_persist();
            break;
        }

        case MSG_TASK_FAILED: {
            session_info_t* sess = _find_session(msg->session_id);
            if (sess) {
                sess->state = STATE_FAILED;
                sess->updated_at = msg->timestamp;
                _notify_state_change(sess);
            }
            session_manager_persist();
            break;
        }

        case MSG_SESSION_LIST: {
            /* Device-side session list refresh from server */
            /* Payload contains JSON array; parse and merge into cache */
            /* Simplified: in full implementation, parse msg->payload */
            break;
        }

        case MSG_ERROR: {
            /* Bridge Server reported an error; log and optionally notify UI */
            break;
        }

        default:
            break;
    }

    return 0;
}

int agent_send_launch(agent_type_t agent, const char* session_id, const char* context)
{
    const char* agent_str = agent_type_to_str(agent);
    const char* ctx = context ? context : "";
    uint32_t now = (uint32_t)time(NULL); /* Use RT-Thread time API in production */

    return _send_json(
        "{\"type\":\"agent_launch\",\"agent\":\"%s\",\"session_id\":\"%s\",\"context\":\"%s\",\"timestamp\":%u}",
        agent_str, session_id, ctx, now
    );
}

int agent_send_permission_response(const char* request_id, bool approved)
{
    uint32_t now = (uint32_t)time(NULL);
    return _send_json(
        "{\"type\":\"permission_response\",\"request_id\":\"%s\",\"approved\":%s,\"timestamp\":%u}",
        request_id, approved ? "true" : "false", now
    );
}

int agent_send_interrupt(const char* session_id)
{
    uint32_t now = (uint32_t)time(NULL);
    return _send_json(
        "{\"type\":\"interrupt\",\"session_id\":\"%s\",\"timestamp\":%u}",
        session_id, now
    );
}

int agent_send_list_sessions(agent_type_t agent)
{
    uint32_t now = (uint32_t)time(NULL);
    return _send_json(
        "{\"type\":\"list_sessions\",\"agent\":\"%s\",\"timestamp\":%u}",
        agent_type_to_str(agent), now
    );
}

int agent_send_heartbeat(const char* device_id)
{
    uint32_t now = (uint32_t)time(NULL);
    return _send_json(
        "{\"type\":\"heartbeat\",\"device_id\":\"%s\",\"timestamp\":%u}",
        device_id, now
    );
}

/*============================================================================*
 *  Session Manager
 *============================================================================*/

const session_info_t* session_manager_get_latest(agent_type_t agent)
{
    session_info_t* latest = NULL;
    for (uint8_t i = 0; i < s_cache_count; i++) {
        if (agent != AGENT_ALL && s_cache[i].agent != agent) {
            continue;
        }
        if (!latest || s_cache[i].updated_at > latest->updated_at) {
            latest = &s_cache[i];
        }
    }
    return latest;
}

const session_info_t* session_manager_get_by_id(const char* session_id)
{
    return _find_session(session_id);
}

uint8_t session_manager_get_all(session_info_t* out_list)
{
    if (!out_list) {
        return 0;
    }
    uint8_t count = s_cache_count < AGENT_SESSION_CACHE_MAX ? s_cache_count : AGENT_SESSION_CACHE_MAX;
    memcpy(out_list, s_cache, count * sizeof(session_info_t));
    return count;
}

int session_manager_persist(void)
{
    /* TODO: Implement LittleFS write
     * In production, serialize s_cache to JSON and write to
     * /flash/agent_sessions.json or equivalent.
     */
    return 0;
}

int session_manager_load(void)
{
    /* TODO: Implement LittleFS read
     * In production, read /flash/agent_sessions.json and populate s_cache.
     */
    return 0;
}

uint8_t session_manager_gc(void)
{
    uint8_t removed = 0;
    uint32_t now = (uint32_t)time(NULL);
    /* Remove terminal sessions older than 24h */
    for (int i = (int)s_cache_count - 1; i >= 0; i--) {
        agent_state_t st = s_cache[i].state;
        bool terminal = (st == STATE_COMPLETED || st == STATE_FAILED ||
                         st == STATE_CANCELLED || st == STATE_ERROR ||
                         st == STATE_TIMEOUT);
        if (terminal && (now - s_cache[i].updated_at) > 86400) {
            /* Shift remaining entries left */
            memmove(&s_cache[i], &s_cache[i + 1],
                    (s_cache_count - i - 1) * sizeof(session_info_t));
            s_cache_count--;
            removed++;
        }
    }
    return removed;
}
