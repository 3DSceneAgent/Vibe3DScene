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
