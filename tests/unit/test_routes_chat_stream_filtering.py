from scene_agent.interfaces.api import routes_chat


def test_internal_non_user_message_nodes_hide_reference_helper_streams():
    assert "initialize_request" in routes_chat._INTERNAL_NON_USER_MESSAGE_NODES
    assert "verify" in routes_chat._INTERNAL_NON_USER_MESSAGE_NODES
    assert "sync_reference_catalog" in routes_chat._INTERNAL_NON_USER_MESSAGE_NODES
    assert "prepare_reference_context" in routes_chat._INTERNAL_NON_USER_MESSAGE_NODES
