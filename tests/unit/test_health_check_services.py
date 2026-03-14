from scene_agent.utils.health_check_services import ServiceHealthChecker


class DummyResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_sam_reconstruct_health_check_uses_base_url(monkeypatch):
    checker = ServiceHealthChecker(timeout=1.5)
    monkeypatch.setenv("SAM_HTTP_BASE_URL", "http://127.0.0.1:8123")

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


def test_sam_reconstruct_health_check_applies_host_override_to_base_url(monkeypatch):
    checker = ServiceHealthChecker(host_override="10.0.0.9", timeout=2.0)
    monkeypatch.setenv("SAM_HTTP_BASE_URL", "http://127.0.0.1:8123")

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


def test_scenesmith_hssd_health_check_uses_retrieval_host_and_port(monkeypatch):
    checker = ServiceHealthChecker(timeout=1.25)
    monkeypatch.setenv("RETRIEVAL_API_HOST", "127.0.0.1")
    monkeypatch.setenv("RETRIEVAL_API_PORT", "8124")

    captured: dict[str, object] = {}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return DummyResponse(200, {"status": "ok", "service": "hssd", "ready": True})

    monkeypatch.setattr(checker._session, "get", fake_get)

    result = checker.check_service("scenesmith_hssd")

    assert result.ok is True
    assert result.url == "http://127.0.0.1:8124/hssd/healthz"
    assert captured["url"] == "http://127.0.0.1:8124/hssd/healthz"
    assert captured["timeout"] == 1.25


def test_scenesmith_ambientcg_health_check_uses_host_override(monkeypatch):
    checker = ServiceHealthChecker(host_override="10.0.0.9", timeout=2.5)
    monkeypatch.setenv("RETRIEVAL_API_HOST", "127.0.0.1")
    monkeypatch.setenv("RETRIEVAL_API_PORT", "8124")

    captured: dict[str, object] = {}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return DummyResponse(200, {"status": "ok", "service": "ambientcg", "ready": True})

    monkeypatch.setattr(checker._session, "get", fake_get)

    result = checker.check_service("scenesmith_ambientcg")

    assert result.ok is True
    assert result.url == "http://10.0.0.9:8124/ambientcg/healthz"
    assert captured["url"] == "http://10.0.0.9:8124/ambientcg/healthz"
    assert captured["timeout"] == 2.5
