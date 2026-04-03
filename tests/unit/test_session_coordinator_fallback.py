from scene_agent.config import reload_settings
from scene_agent.session import session_coordinator as coordinator_module


def test_session_coordinator_falls_back_to_local_owner(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "invalid://redis-url")
    reload_settings()
    coordinator_module._coordinator = None

    coordinator = coordinator_module.get_session_coordinator()
    resolution = coordinator.claim_or_get_owner("thread-fallback-1")

    assert resolution.is_owner
    assert resolution.owner_worker_id == coordinator.worker_id
    assert coordinator.list_threads() == []


def test_session_coordinator_forwards_record_activity_flag():
    class _RegistryStub:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def claim_or_get_owner(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "mode": "owner",
                "owner_worker_id": "worker-test",
                "owner_url": "http://127.0.0.1:8000",
                "lease_epoch": 7,
                "lease_token": "lease-token",
                "lease_ttl_ms": 20000,
            }

    registry = _RegistryStub()
    coordinator = _make_coordinator_with_registry(registry)
    coordinator.worker_id = "worker-test"
    coordinator.owner_url = "http://127.0.0.1:8000"
    coordinator.lease_ttl_seconds = 20
    resolution = coordinator.claim_or_get_owner("thread-readonly", record_activity=False)

    assert resolution.is_owner
    assert registry.calls == [
        {
            "thread_id": "thread-readonly",
            "worker_id": "worker-test",
            "owner_url": "http://127.0.0.1:8000",
            "ttl_seconds": 20,
            "record_activity": False,
        }
    ]


def _make_coordinator_with_registry(registry):
    coordinator = object.__new__(coordinator_module.SessionCoordinator)
    coordinator._registry = registry
    return coordinator


def test_reserve_port_detailed_marks_registry_unavailable_when_no_registry():
    coordinator = _make_coordinator_with_registry(None)
    reservation = coordinator.reserve_port_detailed(
        host="localhost",
        kind="headless",
        base_port=9876,
        range_size=4,
        seed="thread-a",
    )
    assert reservation.status == "registry_unavailable"
    assert reservation.port is None


def test_reserve_port_detailed_marks_exhausted_when_registry_raises_runtime_error():
    class _RegistryStub:
        def reserve_port(self, **_kwargs):
            raise RuntimeError("no port left")

    coordinator = _make_coordinator_with_registry(_RegistryStub())
    reservation = coordinator.reserve_port_detailed(
        host="localhost",
        kind="headless",
        base_port=9876,
        range_size=2,
        seed="thread-b",
    )
    assert reservation.status == "exhausted"
    assert reservation.port is None


def test_reserve_port_detailed_marks_reserved_when_registry_returns_port():
    class _RegistryStub:
        def reserve_port(self, **_kwargs):
            return 9881

    coordinator = _make_coordinator_with_registry(_RegistryStub())
    reservation = coordinator.reserve_port_detailed(
        host="localhost",
        kind="mcp",
        base_port=9880,
        range_size=4,
        seed="thread-c",
    )
    assert reservation.status == "reserved"
    assert reservation.port == 9881
