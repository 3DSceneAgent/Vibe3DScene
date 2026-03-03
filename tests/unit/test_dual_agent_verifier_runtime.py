from scene_agent.agent.graph import _resolve_dual_agent_verifier_runtime


class _FakeSettings:
    def __init__(
        self,
        *,
        verifier_provider: str | None = None,
        verifier_model: str | None = None,
        keys: dict[str, str | None] | None = None,
        defaults: dict[str, str] | None = None,
    ) -> None:
        self.dual_agent_verifier_vlm_provider = verifier_provider
        self.dual_agent_verifier_vlm_model = verifier_model
        self._keys = keys or {}
        self._defaults = defaults or {}

    def get_vlm_default_model(self, provider: str) -> str:
        return self._defaults.get(provider, f"default-{provider}")

    def get_vlm_api_key(self, provider: str) -> str | None:
        return self._keys.get(provider)


def test_resolve_dual_agent_verifier_runtime_prefers_dedicated_provider_when_key_exists():
    settings = _FakeSettings(
        verifier_provider="anthropic",
        verifier_model="claude-3-5-sonnet-20241022",
        keys={"openai": "main-key", "anthropic": "verifier-key"},
        defaults={"anthropic": "claude-default"},
    )

    provider, model, api_key = _resolve_dual_agent_verifier_runtime(
        settings=settings,
        default_provider="openai",
        default_model="gpt-4o",
        default_api_key="main-key",
    )

    assert provider == "anthropic"
    assert model == "claude-3-5-sonnet-20241022"
    assert api_key == "verifier-key"


def test_resolve_dual_agent_verifier_runtime_falls_back_when_dedicated_key_missing():
    settings = _FakeSettings(
        verifier_provider="anthropic",
        verifier_model="claude-3-5-sonnet-20241022",
        keys={"openai": "main-key", "anthropic": None},
        defaults={"anthropic": "claude-default"},
    )

    provider, model, api_key = _resolve_dual_agent_verifier_runtime(
        settings=settings,
        default_provider="openai",
        default_model="gpt-4o-mini",
        default_api_key="main-key",
    )

    assert provider == "openai"
    assert model == "gpt-4o-mini"
    assert api_key == "main-key"


def test_resolve_dual_agent_verifier_runtime_preserves_default_model_without_override():
    settings = _FakeSettings(
        verifier_provider=None,
        verifier_model=None,
        keys={"openai": "main-key"},
        defaults={"openai": "gpt-4o"},
    )

    provider, model, api_key = _resolve_dual_agent_verifier_runtime(
        settings=settings,
        default_provider="openai",
        default_model="gpt-4.1-mini",
        default_api_key="main-key",
    )

    assert provider == "openai"
    assert model == "gpt-4.1-mini"
    assert api_key == "main-key"
