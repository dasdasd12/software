#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "../../src/device/agent_manager.c"

static int g_state_cb_count = 0;

static void state_cb(const session_info_t* session)
{
    (void)session;
    g_state_cb_count++;
}

static void reset_manager(void)
{
    agent_manager_deinit();
    agent_manager_config_t cfg;
    memset(&cfg, 0, sizeof(cfg));
    cfg.state_cb = state_cb;
    assert(agent_manager_init(&cfg) == 0);
    g_state_cb_count = 0;
}

static void send_task_update(const char* session_id, agent_type_t agent,
                             agent_state_t state, uint32_t updated_at)
{
    agent_message_t msg;
    memset(&msg, 0, sizeof(msg));
    msg.type = MSG_TASK_UPDATE;
    strncpy(msg.session_id, session_id, sizeof(msg.session_id) - 1);
    msg.agent = agent;
    msg.state = state;
    msg.timestamp = updated_at;
    assert(agent_manager_handle_message(&msg) == 0);
}

static void send_session_list(const char* payload)
{
    agent_message_t msg;
    memset(&msg, 0, sizeof(msg));
    msg.type = MSG_SESSION_LIST;
    strncpy(msg.payload, payload, sizeof(msg.payload) - 1);
    assert(agent_manager_handle_message(&msg) == 0);
}

static void test_merge_single_session(void)
{
    reset_manager();
    send_session_list("{\"type\":\"session_list\",\"sessions\":[{\"session_id\":\"sess_a\",\"agent\":\"codex\",\"state\":\"WORKING\",\"created_at\":10,\"updated_at\":20}],\"timestamp\":30}");

    session_info_t sessions[AGENT_SESSION_CACHE_MAX];
    uint8_t count = session_manager_get_all(sessions);
    assert(count == 1);
    assert(strcmp(sessions[0].session_id, "sess_a") == 0);
    assert(sessions[0].agent == AGENT_CODEX);
    assert(sessions[0].state == STATE_WORKING);
    assert(sessions[0].created_at == 10);
    assert(sessions[0].updated_at == 20);
    assert(g_state_cb_count == 1);
}

static void test_update_existing_session(void)
{
    reset_manager();
    send_session_list("{\"sessions\":[{\"session_id\":\"sess_a\",\"agent\":\"codex\",\"state\":\"WORKING\",\"created_at\":10,\"updated_at\":20}]}");
    send_session_list("{\"sessions\":[{\"session_id\":\"sess_a\",\"agent\":\"claude\",\"state\":\"COMPLETED\",\"created_at\":10,\"updated_at\":40}]}");

    session_info_t sessions[AGENT_SESSION_CACHE_MAX];
    uint8_t count = session_manager_get_all(sessions);
    assert(count == 1);
    assert(sessions[0].agent == AGENT_CLAUDE);
    assert(sessions[0].state == STATE_COMPLETED);
    assert(sessions[0].updated_at == 40);
    assert(g_state_cb_count == 2);
}

static void test_skip_invalid_session(void)
{
    reset_manager();
    send_session_list("{\"sessions\":[{\"session_id\":\"bad\",\"agent\":\"codex\",\"created_at\":1,\"updated_at\":2},{\"session_id\":\"good\",\"agent\":\"claude\",\"state\":\"FAILED\",\"created_at\":3,\"updated_at\":4}]}");

    session_info_t sessions[AGENT_SESSION_CACHE_MAX];
    uint8_t count = session_manager_get_all(sessions);
    assert(count == 1);
    assert(strcmp(sessions[0].session_id, "good") == 0);
    assert(sessions[0].state == STATE_FAILED);
}

static void test_empty_payload_keeps_cache(void)
{
    reset_manager();
    send_session_list("{\"sessions\":[{\"session_id\":\"sess_a\",\"agent\":\"codex\",\"state\":\"WORKING\",\"created_at\":10,\"updated_at\":20}]}");
    send_session_list("");

    session_info_t sessions[AGENT_SESSION_CACHE_MAX];
    uint8_t count = session_manager_get_all(sessions);
    assert(count == 1);
    assert(strcmp(sessions[0].session_id, "sess_a") == 0);
}

static void test_lru_replacement_when_cache_full(void)
{
    reset_manager();
    char session_id[AGENT_SESSION_ID_MAX_LEN];
    for (int i = 0; i < AGENT_SESSION_CACHE_MAX; i++) {
        snprintf(session_id, sizeof(session_id), "sess_%02d", i);
        send_task_update(session_id, AGENT_CODEX, STATE_WORKING, (uint32_t)(100 + i));
    }

    send_session_list("{\"sessions\":[{\"session_id\":\"sess_new\",\"agent\":\"claude\",\"state\":\"RUNNING\",\"created_at\":1000,\"updated_at\":1000}]}");

    session_info_t sessions[AGENT_SESSION_CACHE_MAX];
    uint8_t count = session_manager_get_all(sessions);
    assert(count == AGENT_SESSION_CACHE_MAX);
    assert(session_manager_get_by_id("sess_00") == NULL);
    assert(session_manager_get_by_id("sess_new") != NULL);
}

int main(void)
{
    test_merge_single_session();
    test_update_existing_session();
    test_skip_invalid_session();
    test_empty_payload_keeps_cache();
    test_lru_replacement_when_cache_full();
    agent_manager_deinit();
    printf("agent_manager host tests passed\n");
    return 0;
}
