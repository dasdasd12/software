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

static void _notify_state_change(const session_info_t* sess);

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

static const char* _bounded_strstr(const char* start, const char* end, const char* needle)
{
    size_t needle_len = strlen(needle);
    if (needle_len == 0) {
        return start;
    }
    while (start && start + needle_len <= end) {
        if (strncmp(start, needle, needle_len) == 0) {
            return start;
        }
        start++;
    }
    return NULL;
}

static const char* _json_find_value(const char* obj_start, const char* obj_end, const char* key)
{
    char pattern[48];
    int len = snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    if (len < 0 || (size_t)len >= sizeof(pattern)) {
        return NULL;
    }

    const char* pos = _bounded_strstr(obj_start, obj_end, pattern);
    if (!pos) {
        return NULL;
    }
    pos += len;
    while (pos < obj_end && (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')) {
        pos++;
    }
    if (pos >= obj_end || *pos != ':') {
        return NULL;
    }
    pos++;
    while (pos < obj_end && (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')) {
        pos++;
    }
    return pos < obj_end ? pos : NULL;
}

static bool _json_get_string(const char* obj_start, const char* obj_end,
                             const char* key, char* out, size_t out_len)
{
    const char* pos = _json_find_value(obj_start, obj_end, key);
    if (!pos || *pos != '"' || out_len == 0) {
        return false;
    }
    pos++;
    size_t written = 0;
    while (pos < obj_end && *pos != '"') {
        if (*pos == '\\' && pos + 1 < obj_end) {
            pos++;
        }
        if (written + 1 < out_len) {
            out[written++] = *pos;
        }
        pos++;
    }
    if (pos >= obj_end || *pos != '"') {
        return false;
    }
    out[written] = '\0';
    return written > 0;
}

static bool _json_get_u32(const char* obj_start, const char* obj_end,
                          const char* key, uint32_t* out)
{
    const char* pos = _json_find_value(obj_start, obj_end, key);
    if (!pos || pos >= obj_end || *pos < '0' || *pos > '9') {
        return false;
    }
    uint32_t value = 0;
    while (pos < obj_end && *pos >= '0' && *pos <= '9') {
        value = (value * 10u) + (uint32_t)(*pos - '0');
        pos++;
    }
    *out = value;
    return true;
}

static bool _parse_session_object(const char* obj_start, const char* obj_end, session_info_t* out)
{
    char agent_str[AGENT_TYPE_MAX_LEN];
    char state_str[AGENT_STATE_MAX_LEN];
    memset(out, 0, sizeof(*out));

    if (!_json_get_string(obj_start, obj_end, "session_id", out->session_id, sizeof(out->session_id))) {
        return false;
    }
    if (!_json_get_string(obj_start, obj_end, "agent", agent_str, sizeof(agent_str))) {
        return false;
    }
    if (!_json_get_string(obj_start, obj_end, "state", state_str, sizeof(state_str))) {
        return false;
    }
    if (!_json_get_u32(obj_start, obj_end, "created_at", &out->created_at)) {
        return false;
    }
    if (!_json_get_u32(obj_start, obj_end, "updated_at", &out->updated_at)) {
        return false;
    }

    out->agent = agent_type_from_str(agent_str);
    out->state = agent_state_from_str(state_str);
    return true;
}

static void _merge_session(const session_info_t* incoming)
{
    session_info_t* sess = _find_session(incoming->session_id);
    if (!sess) {
        sess = _alloc_session_slot();
    }
    *sess = *incoming;
    _notify_state_change(sess);
}

static uint8_t _merge_session_list_payload(const char* payload)
{
    if (!payload || payload[0] == '\0') {
        return 0;
    }

    const char* payload_end = payload + strlen(payload);
    const char* sessions_key = strstr(payload, "\"sessions\"");
    if (!sessions_key) {
        return 0;
    }
    const char* array = strchr(sessions_key, '[');
    if (!array || array >= payload_end) {
        return 0;
    }

    uint8_t merged = 0;
    const char* pos = array + 1;
    while (pos < payload_end && merged < AGENT_SESSION_CACHE_MAX) {
        const char* obj_start = strchr(pos, '{');
        if (!obj_start || obj_start >= payload_end) {
            break;
        }

        int depth = 0;
        bool in_string = false;
        bool escaped = false;
        const char* cursor = obj_start;
        const char* obj_end = NULL;
        while (cursor < payload_end) {
            char ch = *cursor;
            if (in_string) {
                if (escaped) {
                    escaped = false;
                } else if (ch == '\\') {
                    escaped = true;
                } else if (ch == '"') {
                    in_string = false;
                }
            } else {
                if (ch == '"') {
                    in_string = true;
                } else if (ch == '{') {
                    depth++;
                } else if (ch == '}') {
                    depth--;
                    if (depth == 0) {
                        obj_end = cursor + 1;
                        break;
                    }
                } else if (ch == ']') {
                    return merged;
                }
            }
            cursor++;
        }

        if (!obj_end) {
            break;
        }

        session_info_t parsed;
        if (_parse_session_object(obj_start, obj_end, &parsed)) {
            _merge_session(&parsed);
            merged++;
        }
        pos = obj_end;
    }

    return merged;
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

static void _clear_pending_permission(void)
{
    s_pending_request_id[0] = '\0';
    s_permission_deadline = 0;
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
    _clear_pending_permission();

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

        case MSG_PERMISSION_ACK: {
            if (strncmp(s_pending_request_id, msg->request_id, AGENT_REQUEST_ID_MAX_LEN) == 0) {
                _clear_pending_permission();
            }
            break;
        }

        case MSG_TASK_COMPLETED: {
            session_info_t* sess = _find_session(msg->session_id);
            if (sess) {
                sess->state = STATE_COMPLETED;
                sess->updated_at = msg->timestamp;
                _notify_state_change(sess);
            }
            _clear_pending_permission();
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
            _clear_pending_permission();
            session_manager_persist();
            break;
        }

        case MSG_SESSION_LIST: {
            _merge_session_list_payload(msg->payload);
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
    if (!request_id || request_id[0] == '\0') {
        return -1;
    }
    uint32_t now = (uint32_t)time(NULL);
    return _send_json(
        "{\"type\":\"permission_response\",\"request_id\":\"%s\",\"approved\":%s,\"timestamp\":%u}",
        request_id, approved ? "true" : "false", now
    );
}

const char* agent_manager_get_pending_request_id(void)
{
    return agent_manager_has_pending_permission() ? s_pending_request_id : NULL;
}

bool agent_manager_has_pending_permission(void)
{
    if (s_pending_request_id[0] == '\0') {
        return false;
    }
    if (s_permission_deadline == 0) {
        return true;
    }
    return (uint32_t)time(NULL) <= s_permission_deadline;
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
