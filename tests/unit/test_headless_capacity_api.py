from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from scene_agent.blender.session_manager import SessionResourceError
from scene_agent.interfaces import api as api_module


class _CoordinatorStub:
    def __init__(self) -> None:
        self.release_calls: list[tuple[str, str, int | None]] = []
        self.runtime_updates: list[tuple[str, dict[str, object]]] = []
        self.deleted_threads: list[str] = []
        self.meta_by_thread: dict[str, dict[str, str]] = {}

    def release_port(self, *, host: str, kind: str, port: int | None) -> None:
        self.release_calls.append((host, kind, port))

    def update_session_runtime_fields(self, thread_id: str, fields: dict[str, object]) -> None:
        self.runtime_updates.append((thread_id, fields))

    def delete_session_metadata(self, thread_id: str) -> None:
        self.deleted_threads.append(thread_id)

    def get_session_meta(self, thread_id: str) -> dict[str, str] | None:
        return self.meta_by_thread.get(thread_id)

    def is_owned_by_current_worker(self, _thread_id: str) -> bool:
        return True


class _ManagerStub:
    def __init__(self, session: SimpleNamespace | None) -> None:
        self.session = session
        self.removed: list[str] = []
        self.shutdown_calls: list[str] = []

    def get(self, _thread_id: str) -> SimpleNamespace | None:
        return self.session

    def persist_session_blend(self, _thread_id: str) -> bool:
        return True

    def terminate_session_processes(self, _thread_id: str, timeout: float = 0.0) -> None:
        _ = timeout
        if self.session is not None:
            # Simulate cleanup path that clears in-memory ports.
            self.session.port = None
            self.session.mcp_port = None

    def remove(self, thread_id: str) -> None:
        self.removed.append(thread_id)
        self.session = None

    def get_idle_sessions(self) -> list[SimpleNamespace]:
        if self.session is None:
            return []
        return [self.session]

    def shutdown_if_idle(self, thread_id: str) -> tuple[bool, bool]:
        self.shutdown_calls.append(thread_id)
        if self.session is None:
            return False, False
        # Simulate shutdown clearing endpoint fields.
        self.session.port = None
        self.session.mcp_port = None
        return True, True


def test_teardown_thread_session_releases_snapshot_ports(monkeypatch):
    thread_id = "thread-teardown-snapshot"
    session = SimpleNamespace(
        session_id=thread_id,
        mode="headless",
        host="127.0.0.1",
        port=9876,
        mcp_host="127.0.0.1",
        mcp_port=9877,
        blend_path="/tmp/scene.blend",
    )
    manager = _ManagerStub(session)
    coordinator = _CoordinatorStub()
    settings = SimpleNamespace(blender_host="localhost")

    monkeypatch.setattr(api_module, "get_session_manager", lambda: manager)
    monkeypatch.setattr(api_module, "get_session_coordinator", lambda: coordinator)
    monkeypatch.setattr(api_module, "get_settings", lambda: settings)

    result = api_module._teardown_thread_session(thread_id)

    assert ("127.0.0.1", "headless", 9876) in coordinator.release_calls
    assert ("127.0.0.1", "mcp", 9877) in coordinator.release_calls
    assert "headless_port:9876" in result["cleaned"]
    assert "mcp_port:9877" in result["cleaned"]
    assert thread_id in manager.removed


def test_teardown_thread_session_releases_ports_from_meta_when_session_missing(monkeypatch):
    thread_id = "thread-teardown-meta"
    manager = _ManagerStub(None)
    coordinator = _CoordinatorStub()
    coordinator.meta_by_thread[thread_id] = {
        "host": "meta-headless-host",
        "blender_port": "9976",
        "mcp_host": "meta-mcp-host",
        "mcp_port": "9977",
    }
    settings = SimpleNamespace(blender_host="localhost")

    monkeypatch.setattr(api_module, "get_session_manager", lambda: manager)
    monkeypatch.setattr(api_module, "get_session_coordinator", lambda: coordinator)
    monkeypatch.setattr(api_module, "get_settings", lambda: settings)

    result = api_module._teardown_thread_session(thread_id)

    assert ("meta-headless-host", "headless", 9976) in coordinator.release_calls
    assert ("meta-mcp-host", "mcp", 9977) in coordinator.release_calls
    assert "headless_port:9976" in result["cleaned"]
    assert "mcp_port:9977" in result["cleaned"]


def test_idle_sweeper_releases_snapshot_ports(monkeypatch):
    thread_id = "thread-idle-snapshot"
    session = SimpleNamespace(
        session_id=thread_id,
        mode="headless",
        host="idle-headless-host",
        port=9966,
        mcp_host="idle-mcp-host",
        mcp_port=9967,
        blend_path="/tmp/scene.blend",
        idle_timeout_seconds=1,
    )
    manager = _ManagerStub(session)
    coordinator = _CoordinatorStub()
    settings = SimpleNamespace(
        blender_host="localhost",
        session_sweep_interval_seconds=1,
    )

    sleep_calls = 0

    async def fake_sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise asyncio.CancelledError()

    async def fake_to_thread(func, *args, **kwargs):  # type: ignore[no-untyped-def]
        return func(*args, **kwargs)

    monkeypatch.setattr(api_module, "get_session_manager", lambda: manager)
    monkeypatch.setattr(api_module, "get_session_coordinator", lambda: coordinator)
    monkeypatch.setattr(api_module, "get_settings", lambda: settings)
    monkeypatch.setattr(api_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(api_module.asyncio, "to_thread", fake_to_thread)

    async def run_once() -> None:
        with pytest.raises(asyncio.CancelledError):
            await api_module._idle_session_sweeper()

    asyncio.run(run_once())

    assert manager.shutdown_calls == [thread_id]
    assert ("idle-headless-host", "headless", 9966) in coordinator.release_calls
    assert ("idle-mcp-host", "mcp", 9967) in coordinator.release_calls
    assert any(
        fields.get("status") == "closed"
        and fields.get("blender_port") == ""
        and fields.get("mcp_port") == ""
        for _, fields in coordinator.runtime_updates
    )


def test_idle_sweeper_still_cleans_local_idle_session_when_not_owner(monkeypatch):
    class _CoordinatorNotOwner(_CoordinatorStub):
        def is_owned_by_current_worker(self, _thread_id: str) -> bool:
            return False

    thread_id = "thread-idle-non-owner"
    session = SimpleNamespace(
        session_id=thread_id,
        mode="headless",
        host="idle-headless-host",
        port=9970,
        mcp_host="idle-mcp-host",
        mcp_port=9971,
        blend_path="/tmp/scene.blend",
        idle_timeout_seconds=1,
    )
    manager = _ManagerStub(session)
    coordinator = _CoordinatorNotOwner()
    settings = SimpleNamespace(
        blender_host="localhost",
        session_sweep_interval_seconds=1,
    )

    sleep_calls = 0

    async def fake_sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise asyncio.CancelledError()

    async def fake_to_thread(func, *args, **kwargs):  # type: ignore[no-untyped-def]
        return func(*args, **kwargs)

    monkeypatch.setattr(api_module, "get_session_manager", lambda: manager)
    monkeypatch.setattr(api_module, "get_session_coordinator", lambda: coordinator)
    monkeypatch.setattr(api_module, "get_settings", lambda: settings)
    monkeypatch.setattr(api_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(api_module.asyncio, "to_thread", fake_to_thread)

    async def run_once() -> None:
        with pytest.raises(asyncio.CancelledError):
            await api_module._idle_session_sweeper()

    asyncio.run(run_once())

    assert manager.shutdown_calls == [thread_id]
    assert ("idle-headless-host", "headless", 9970) in coordinator.release_calls
    assert ("idle-mcp-host", "mcp", 9971) in coordinator.release_calls


def test_get_mcp_tools_returns_structured_503_for_capacity_errors(monkeypatch):
    async def fake_get_agent(_thread_id: str):
        raise SessionResourceError(
            error="Cannot create new headless session: no available Blender port in configured range.",
            reason="blender_port_exhausted",
            limits={"worker_capacity": 2, "headless_port_capacity": 2},
            in_use={"active_headless_sessions": 2},
        )

    async def fake_claim_or_proxy_request(*, request, thread_id):  # type: ignore[no-untyped-def]
        _ = request, thread_id
        return SimpleNamespace(owner_worker_id="", lease_epoch=None), None

    monkeypatch.setattr(api_module, "get_agent", fake_get_agent)
    monkeypatch.setattr(api_module, "_claim_or_proxy_request", fake_claim_or_proxy_request)

    with TestClient(api_module.app) as client:
        response = client.get("/threads/thread-resource-error/mcp-tools")

    assert response.status_code == 503
    payload = response.json()
    assert payload["detail"]["reason"] == "blender_port_exhausted"
    assert payload["detail"]["limits"]["worker_capacity"] == 2
    assert payload["detail"]["in_use"]["active_headless_sessions"] == 2


def test_ensure_frontend_client_can_manage_thread_rejects_foreign_client(monkeypatch):
    class _Coordinator:
        def __init__(self) -> None:
            self.meta_by_thread = {
                "thread-1": {"frontend_client_id": "client-a"},
            }

        def get_session_meta(self, thread_id: str) -> dict[str, str] | None:
            return self.meta_by_thread.get(thread_id)

        def update_session_runtime_fields(self, thread_id: str, fields: dict[str, object]) -> None:
            _ = thread_id, fields

    coordinator = _Coordinator()
    monkeypatch.setattr(api_module, "get_session_coordinator", lambda: coordinator)

    with api_module._thread_client_lock:
        api_module._thread_frontend_clients.clear()

    with pytest.raises(HTTPException) as exc_info:
        api_module._ensure_frontend_client_can_manage_thread("thread-1", "client-b")
    assert exc_info.value.status_code == 403


def test_ensure_frontend_client_can_manage_thread_binds_legacy_default(monkeypatch):
    class _Coordinator:
        def __init__(self) -> None:
            self.meta_by_thread = {"thread-legacy": {}}
            self.runtime_updates: list[tuple[str, dict[str, object]]] = []

        def get_session_meta(self, thread_id: str) -> dict[str, str] | None:
            return self.meta_by_thread.get(thread_id)

        def update_session_runtime_fields(self, thread_id: str, fields: dict[str, object]) -> None:
            self.runtime_updates.append((thread_id, fields))
            meta = self.meta_by_thread.setdefault(thread_id, {})
            for key, value in fields.items():
                if value is None:
                    continue
                meta[str(key)] = str(value)

    coordinator = _Coordinator()
    monkeypatch.setattr(api_module, "get_session_coordinator", lambda: coordinator)

    with api_module._thread_client_lock:
        api_module._thread_frontend_clients.clear()

    api_module._ensure_frontend_client_can_manage_thread("thread-legacy", "client-new")

    assert coordinator.runtime_updates
    assert coordinator.meta_by_thread["thread-legacy"]["frontend_client_id"] == "client-new"
    with api_module._thread_client_lock:
        assert api_module._thread_frontend_clients["thread-legacy"] == "client-new"


def test_collect_headless_runtime_entries_adopts_legacy_default_frontend_owner(monkeypatch):
    class _Coordinator:
        def __init__(self) -> None:
            self.meta_by_thread = {
                "thread-legacy": {
                    "status": "active",
                    "blender_port": "9876",
                }
            }
            self.runtime_updates: list[tuple[str, dict[str, object]]] = []

        def list_threads(self, limit: int = 2000) -> list[str]:
            _ = limit
            return list(self.meta_by_thread.keys())

        def get_session_meta(self, thread_id: str) -> dict[str, str] | None:
            return self.meta_by_thread.get(thread_id)

        def update_session_runtime_fields(self, thread_id: str, fields: dict[str, object]) -> None:
            self.runtime_updates.append((thread_id, fields))
            meta = self.meta_by_thread.setdefault(thread_id, {})
            for key, value in fields.items():
                if value is None:
                    continue
                meta[str(key)] = str(value)

    class _Manager:
        @staticmethod
        def list_sessions():
            return []

    coordinator = _Coordinator()
    monkeypatch.setattr(api_module, "get_session_coordinator", lambda: coordinator)
    monkeypatch.setattr(api_module, "get_session_manager", lambda: _Manager())
    monkeypatch.setattr(api_module, "get_settings", lambda: SimpleNamespace(blender_mode="headless"))

    with api_module._thread_client_lock:
        api_module._thread_frontend_clients.clear()

    entries = api_module._collect_headless_runtime_entries(frontend_client_id="client-z")

    assert len(entries) == 1
    assert entries[0]["thread_id"] == "thread-legacy"
    assert entries[0]["frontend_client_id"] == "client-z"
    assert coordinator.meta_by_thread["thread-legacy"]["frontend_client_id"] == "client-z"


def test_collect_headless_runtime_entries_prefers_local_cleared_ports(monkeypatch):
    class _Coordinator:
        @staticmethod
        def list_threads(limit: int = 2000) -> list[str]:
            _ = limit
            return ["thread-stale"]

        @staticmethod
        def get_session_meta(thread_id: str) -> dict[str, str] | None:
            if thread_id != "thread-stale":
                return None
            return {
                "frontend_client_id": "client-a",
                "status": "closed",
                "blender_port": "9876",
                "mcp_port": "9877",
                "last_active_ms": "1000",
            }

    session = SimpleNamespace(
        session_id="thread-stale",
        mode="headless",
        status="closed",
        port=None,
        mcp_port=None,
        last_active_at=0.0,
    )

    class _Manager:
        @staticmethod
        def list_sessions():
            return [session]

    monkeypatch.setattr(api_module, "get_session_coordinator", lambda: _Coordinator())
    monkeypatch.setattr(api_module, "get_session_manager", lambda: _Manager())
    monkeypatch.setattr(api_module, "get_settings", lambda: SimpleNamespace(blender_mode="headless"))

    entries = api_module._collect_headless_runtime_entries(frontend_client_id="client-a")

    assert len(entries) == 1
    entry = entries[0]
    assert entry["thread_id"] == "thread-stale"
    assert entry["blender_port"] is None
    assert entry["mcp_port"] is None
    assert entry["occupying_resources"] is False


def test_headless_session_debug_reports_idle_countdown(monkeypatch):
    class _Coordinator:
        @staticmethod
        def list_threads(limit: int = 5000) -> list[str]:
            _ = limit
            return ["thread-debug"]

        @staticmethod
        def get_session_meta(thread_id: str) -> dict[str, str] | None:
            if thread_id != "thread-debug":
                return None
            return {
                "frontend_client_id": "client-a",
                "status": "active",
                "last_active_ms": "1",
            }

        @staticmethod
        def get_owner(_thread_id: str):
            return None

    class _AliveProcess:
        @staticmethod
        def poll():
            return None

    session = SimpleNamespace(
        session_id="thread-debug",
        mode="headless",
        status="ready",
        port=9876,
        mcp_port=9877,
        process=_AliveProcess(),
        mcp_process=_AliveProcess(),
        last_active_at=time.time() - 5.0,
        idle_timeout_seconds=20,
    )

    class _Manager:
        @staticmethod
        def list_sessions():
            return [session]

    monkeypatch.setattr(api_module, "get_session_coordinator", lambda: _Coordinator())
    monkeypatch.setattr(api_module, "get_session_manager", lambda: _Manager())
    monkeypatch.setattr(api_module, "get_settings", lambda: SimpleNamespace(blender_mode="headless"))

    now_ms, entries = api_module._collect_headless_runtime_debug_entries(
        frontend_client_id="client-a",
        include_all_clients=False,
    )
    assert now_ms > 0
    assert len(entries) == 1
    entry = entries[0]
    assert entry["thread_id"] == "thread-debug"
    assert entry["occupying_resources"] is True
    assert entry["effective_headless_port"] == 9876
    assert entry["effective_mcp_port"] == 9877
    assert entry["seconds_until_idle_cleanup"] is not None
    assert 0.0 <= float(entry["seconds_until_idle_cleanup"]) <= 20.0
