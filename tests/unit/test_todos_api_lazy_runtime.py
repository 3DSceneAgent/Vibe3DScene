from types import SimpleNamespace

from fastapi.testclient import TestClient

from scene_agent.interfaces import api as api_module
from scene_agent.interfaces.api import routes_assets as api_routes_assets


def test_get_todos_reads_persisted_state_without_starting_headless_runtime(monkeypatch):
    thread_id = "thread-lazy-todos"
    persisted_todos = [
        {
            "id": "todo-1",
            "description": "Sketch the room layout",
            "status": "pending",
            "created_at": "2026-03-08T10:00:00Z",
            "completed_at": None,
        }
    ]

    async def fake_claim_or_proxy_request(*, request, thread_id: str):
        _ = request
        assert thread_id == "thread-lazy-todos"
        return SimpleNamespace(owner_worker_id="", lease_epoch=None), None

    async def fail_get_agent(_thread_id: str | None = None):
        raise AssertionError("get_agent should not be called for released headless runtime")

    monkeypatch.setattr(api_routes_assets, "claim_or_proxy_request", fake_claim_or_proxy_request)
    monkeypatch.setattr(
        api_routes_assets,
        "get_settings",
        lambda: SimpleNamespace(blender_mode="headless"),
    )
    monkeypatch.setattr(api_routes_assets, "_thread_runtime_occupies_resources", lambda _thread_id: False)
    monkeypatch.setattr(api_routes_assets, "_load_persisted_thread_todos", lambda _thread_id: persisted_todos)
    monkeypatch.setattr(api_routes_assets, "get_agent", fail_get_agent)

    with TestClient(api_module.app) as client:
        response = client.get(f"/todos/{thread_id}")

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": thread_id,
        "todos": persisted_todos,
    }


def test_get_todos_uses_live_agent_when_headless_runtime_is_occupied(monkeypatch):
    thread_id = "thread-live-todos"
    live_todos = [
        {
            "id": "todo-2",
            "description": "Place the table asset",
            "status": "in_progress",
            "created_at": "2026-03-08T10:05:00Z",
            "completed_at": None,
        }
    ]

    class DummyAgent:
        async def aget_state(self, config):
            assert config == {"configurable": {"thread_id": thread_id}}
            return SimpleNamespace(values={"todos": live_todos})

    async def fake_claim_or_proxy_request(*, request, thread_id: str):
        _ = request
        assert thread_id == "thread-live-todos"
        return SimpleNamespace(owner_worker_id="", lease_epoch=None), None

    async def fake_get_agent(request_thread_id: str | None = None):
        assert request_thread_id == thread_id
        return DummyAgent()

    monkeypatch.setattr(api_routes_assets, "claim_or_proxy_request", fake_claim_or_proxy_request)
    monkeypatch.setattr(
        api_routes_assets,
        "get_settings",
        lambda: SimpleNamespace(blender_mode="headless"),
    )
    monkeypatch.setattr(api_routes_assets, "_thread_runtime_occupies_resources", lambda _thread_id: True)
    monkeypatch.setattr(api_routes_assets, "get_agent", fake_get_agent)

    with TestClient(api_module.app) as client:
        response = client.get(f"/todos/{thread_id}")

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": thread_id,
        "todos": live_todos,
    }


def test_load_persisted_thread_todos_projects_latest_todo_versions(monkeypatch):
    thread_id = "thread-persisted-todos"

    class Checkpointer:
        @staticmethod
        def get_tuple(config):
            assert config == {"configurable": {"thread_id": thread_id}}
            return SimpleNamespace(
                checkpoint={
                    "channel_values": {
                        "todo_versions": [
                            {
                                "event_id": "todo_evt_1",
                                "todo_id": "todo-3",
                                "version": 1,
                                "prev_event_id": None,
                                "title": "Add a floor lamp",
                                "status": "pending",
                                "reason": "initial_plan",
                                "source": "planner",
                                "created_at": "2026-03-08T10:10:00Z",
                                "created_by_role": "assistant",
                                "render_path": None,
                            },
                            {
                                "event_id": "todo_evt_2",
                                "todo_id": "todo-3",
                                "version": 2,
                                "prev_event_id": "todo_evt_1",
                                "title": "Add a floor lamp",
                                "status": "completed",
                                "reason": "tool_success",
                                "source": "executor",
                                "created_at": "2026-03-08T10:12:00Z",
                                "created_by_role": "assistant",
                                "render_path": None,
                            },
                        ]
                    }
                }
            )

    monkeypatch.setattr(api_routes_assets, "get_graph_checkpointer", lambda: Checkpointer())

    todos = api_routes_assets._load_persisted_thread_todos(thread_id)

    assert todos == [
        {
            "id": "todo-3",
            "description": "Add a floor lamp",
            "status": "completed",
            "created_at": "2026-03-08T10:10:00Z",
            "completed_at": "2026-03-08T10:12:00Z",
        }
    ]
