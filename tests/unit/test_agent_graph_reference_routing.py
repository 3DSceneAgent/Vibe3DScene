import scene_agent.agent.graph as graph_module


def test_route_after_mode_always_syncs_reference_catalog_first():
    assert graph_module._route_after_mode({}) == "sync_reference_catalog"
    assert graph_module._route_after_mode({"router_need_clarification": True}) == "sync_reference_catalog"


def test_route_after_sync_reference_catalog_clarifies_only_after_sync():
    assert (
        graph_module._route_after_sync_reference_catalog({"router_need_clarification": True})
        == "clarification"
    )
    assert graph_module._route_after_sync_reference_catalog({}) == "prepare_reference_context"
