from scene_agent.utils.health_check_services import ServiceHealthChecker


class DummyResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_sam_reconstruct_health_check_uses_host_and_port(monkeypatch):
    checker = ServiceHealthChecker(timeout=1.5)
    monkeypatch.setenv("SAM_HOST", "127.0.0.1")
    monkeypatch.setenv("SAM_PORT", "8123")

    captured: dict[str, object] = {}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return DummyResponse(
            200,
            {"status": "ok", "sam_service": True, "sam3d_service": True},
        )

    monkeypatch.setattr(checker._session, "get", fake_get)

    result = checker.check_service("sam_reconstruct")

    assert result.ok is True
    assert result.url == "http://127.0.0.1:8123/healthz"
    assert captured["url"] == "http://127.0.0.1:8123/healthz"
    assert captured["timeout"] == 1.5


def test_sam_reconstruct_health_check_applies_host_override(monkeypatch):
    checker = ServiceHealthChecker(host_override="10.0.0.9", timeout=2.0)
    monkeypatch.setenv("SAM_HOST", "127.0.0.1")
    monkeypatch.setenv("SAM_PORT", "8123")

    captured: dict[str, object] = {}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return DummyResponse(
            200,
            {"status": "ok", "sam_service": True, "sam3d_service": True},
        )

    monkeypatch.setattr(checker._session, "get", fake_get)

    result = checker.check_service("sam_reconstruct")

    assert result.ok is True
    assert result.url == "http://10.0.0.9:8123/healthz"
    assert captured["url"] == "http://10.0.0.9:8123/healthz"
    assert captured["timeout"] == 2.0


def test_sam_reconstruct_health_check_accepts_cache_mode(monkeypatch):
    checker = ServiceHealthChecker(timeout=1.0)
    monkeypatch.setenv("SAM_HOST", "127.0.0.1")
    monkeypatch.setenv("SAM_PORT", "8123")

    def fake_get(url, timeout):
        return DummyResponse(
            200,
            {
                "status": "ok",
                "runtime_mode": "cache",
                "internal_services_expected": False,
                "sam_service": False,
                "sam3d_service": False,
            },
        )

    monkeypatch.setattr(checker._session, "get", fake_get)

    result = checker.check_service("sam_reconstruct")

    assert result.ok is True
    assert result.missing_fields == []


def test_sam_reconstruct_health_check_rejects_unready_non_cache_mode(monkeypatch):
    checker = ServiceHealthChecker(timeout=1.0)
    monkeypatch.setenv("SAM_HOST", "127.0.0.1")
    monkeypatch.setenv("SAM_PORT", "8123")

    def fake_get(url, timeout):
        return DummyResponse(
            200,
            {
                "status": "ok",
                "runtime_mode": "serve",
                "internal_services_expected": True,
                "sam_service": False,
                "sam3d_service": False,
            },
        )

    monkeypatch.setattr(checker._session, "get", fake_get)

    result = checker.check_service("sam_reconstruct")

    assert result.ok is False
    assert result.missing_fields == ["sam_service=True", "sam3d_service=True"]


def test_objaverse_health_check_uses_objaverse_host_and_port(monkeypatch):
    checker = ServiceHealthChecker(timeout=1.25)
    monkeypatch.setenv("ASSET_RETRIEVAL_BACKEND", "objaverse")
    monkeypatch.setenv("OBJAVERSE_HOST", "127.0.0.1")
    monkeypatch.setenv("OBJAVERSE_PORT", "8124")

    captured: dict[str, object] = {}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return DummyResponse(200, {"status": "running"})

    monkeypatch.setattr(checker._session, "get", fake_get)

    result = checker.check_service("objaverse_retrieval")

    assert result.ok is True
    assert result.url == "http://127.0.0.1:8124/"
    assert captured["url"] == "http://127.0.0.1:8124/"
    assert captured["timeout"] == 1.25


def test_scenesmith_hssd_health_check_uses_scenesmith_host_and_port(monkeypatch):
    checker = ServiceHealthChecker(timeout=1.25)
    monkeypatch.setenv("ASSET_RETRIEVAL_BACKEND", "scenesmith")
    monkeypatch.setenv("SCENESMITH_COMPAT_HOST", "127.0.0.1")
    monkeypatch.setenv("SCENESMITH_COMPAT_PORT", "8125")

    captured: dict[str, object] = {}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return DummyResponse(200, {"status": "ok", "service": "hssd", "ready": True})

    monkeypatch.setattr(checker._session, "get", fake_get)

    result = checker.check_service("scenesmith_hssd")

    assert result.ok is True
    assert result.url == "http://127.0.0.1:8125/hssd/healthz"
    assert captured["url"] == "http://127.0.0.1:8125/hssd/healthz"
    assert captured["timeout"] == 1.25


def test_scenesmith_ambientcg_health_check_uses_host_override(monkeypatch):
    checker = ServiceHealthChecker(host_override="10.0.0.9", timeout=2.5)
    monkeypatch.setenv("ASSET_RETRIEVAL_BACKEND", "objaverse")
    monkeypatch.setenv("SCENESMITH_COMPAT_HOST", "127.0.0.1")
    monkeypatch.setenv("SCENESMITH_COMPAT_PORT", "8125")

    captured: dict[str, object] = {}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return DummyResponse(200, {"status": "ok", "service": "ambientcg", "ready": True})

    monkeypatch.setattr(checker._session, "get", fake_get)

    result = checker.check_service("scenesmith_ambientcg")

    assert result.ok is True
    assert result.url == "http://10.0.0.9:8125/ambientcg/healthz"
    assert captured["url"] == "http://10.0.0.9:8125/ambientcg/healthz"
    assert captured["timeout"] == 2.5


def test_scenesmith_hssd_health_check_uses_shared_tool_service_host(monkeypatch):
    checker = ServiceHealthChecker(timeout=1.0)
    monkeypatch.setenv("ASSET_RETRIEVAL_BACKEND", "scenesmith")
    monkeypatch.delenv("SCENESMITH_COMPAT_HOST", raising=False)
    monkeypatch.delenv("SCENESMITH_COMPAT_PORT", raising=False)
    monkeypatch.setenv("TOOL_SERVICE_HOST", "10.0.0.9")

    captured: dict[str, object] = {}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return DummyResponse(200, {"status": "ok", "service": "hssd", "ready": True})

    monkeypatch.setattr(checker._session, "get", fake_get)

    result = checker.check_service("scenesmith_hssd")

    assert result.ok is True
    assert result.url == "http://10.0.0.9:8005/hssd/healthz"
    assert captured["url"] == "http://10.0.0.9:8005/hssd/healthz"


def test_sam_reconstruct_health_check_uses_shared_tool_service_host(monkeypatch):
    checker = ServiceHealthChecker(timeout=1.0)
    monkeypatch.delenv("SAM_HOST", raising=False)
    monkeypatch.delenv("SAM_PORT", raising=False)
    monkeypatch.setenv("TOOL_SERVICE_HOST", "10.0.0.9")

    captured: dict[str, object] = {}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return DummyResponse(
            200,
            {"status": "ok", "sam_service": True, "sam3d_service": True},
        )

    monkeypatch.setattr(checker._session, "get", fake_get)

    result = checker.check_service("sam_reconstruct")

    assert result.ok is True
    assert result.url == "http://10.0.0.9:8004/healthz"
    assert captured["url"] == "http://10.0.0.9:8004/healthz"
