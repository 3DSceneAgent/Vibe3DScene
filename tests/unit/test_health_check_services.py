from scene_agent.utils.health_check_services import ServiceHealthChecker


def test_pcg_integrator_health_path_matches_service_contract():
    checker = ServiceHealthChecker()
    spec = checker._specs["pcg_integrator"]  # Internal contract for MCP gating.

    assert spec.path == "/health"
    assert spec.expected_fields == {"status": "healthy", "service": "PCGIntegrator3D"}
