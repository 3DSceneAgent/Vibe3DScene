import scene_agent.agent.graph as graph_module


def test_route_after_initialize_request_always_syncs_reference_catalog_first():
    assert graph_module._route_after_initialize_request({}) == "sync_reference_catalog"


def test_route_after_sync_reference_catalog_always_prepares_reference_context():
    assert graph_module._route_after_sync_reference_catalog({}) == "prepare_reference_context"


def test_route_after_prepare_reference_context_goes_to_router():
    assert graph_module._route_after_prepare_reference_context({}) == "router"


def test_route_after_router_uses_plan_when_routed_to_plan():
    assert graph_module._route_after_router({"routed_to_plan": True}) == "plan_node"
