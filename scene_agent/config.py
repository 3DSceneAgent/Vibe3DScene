"""
Configuration management using Pydantic Settings.
Loads configuration from environment variables with validation.
"""
import os
import socket
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_CACHE_ROOT = os.path.expanduser("~/.cache/vibe3dscene")


class Settings(BaseSettings):
    """
    Application settings with environment variable support.
    Reads from .env file and environment variables.
    """
    
    # VLM Configuration
    vlm_provider: str = Field(
        default="gemini",
        description="VLM provider: openai, anthropic, gemini, or qwen"
    )
    vlm_model: str | None = Field(
        default=None,
        description="Optional model override (uses provider default if not set)"
    )
    dual_agent_verifier_vlm_provider: str | None = Field(
        default=None,
        description=(
            "Optional dedicated verifier provider for dual-agent plan mode; "
            "falls back to VLM_PROVIDER when unset or invalid"
        ),
    )
    dual_agent_verifier_vlm_model: str | None = Field(
        default=None,
        description=(
            "Optional dedicated verifier model for dual-agent plan mode; "
            "falls back to the selected provider default model when unset"
        ),
    )
    openai_api_key: str | None = Field(
        default=None,
        description="OpenAI API key override"
    )
    anthropic_api_key: str | None = Field(
        default=None,
        description="Anthropic API key override"
    )
    gemini_api_key: str | None = Field(
        default=None,
        description="Google Gemini API key override"
    )
    gemini_include_thoughts: bool = Field(
        default=True,
        description="Whether Gemini responses should include provider thinking summaries"
    )
    gemini_thinking_budget: int | None = Field(
        default=None,
        description="Optional Gemini thinking budget; unset keeps provider defaults"
    )
    qwen_api_key: str | None = Field(
        default=None,
        description="Qwen (DashScope) API key override"
    )
    qwen_enable_thinking: bool = Field(
        default=False,
        description="Whether Qwen should emit hidden reasoning_content while thinking; enabling this may reduce visible pre-tool text"
    )
    qwen_thinking_budget: int | None = Field(
        default=None,
        description="Optional Qwen thinking budget; unset keeps provider defaults"
    )
    vlm_openai_models: str = Field(
        default="gpt-4o,gpt-4.1,gpt-4.1-mini,gpt-4o-mini",
        description="Comma-separated OpenAI model names exposed to clients"
    )
    vlm_anthropic_models: str = Field(
        default="claude-3-5-sonnet-20241022,claude-3-7-sonnet-20250219",
        description="Comma-separated Anthropic model names exposed to clients"
    )
    vlm_gemini_models: str = Field(
        default="gemini-2.5-pro,gemini-2.5-flash",
        description="Comma-separated Gemini model names exposed to clients"
    )
    vlm_qwen_models: str = Field(
        default="qwen-vl-max-latest,qwen-vl-plus-latest,qwen-plus-latest",
        description="Comma-separated Qwen model names exposed to clients"
    )
    
    # Blender addon socket (local-client/headless)
    blender_host: str = Field(
        default="localhost",
        description="Blender addon socket host"
    )
    blender_port: int = Field(
        default=9876,
        description="Blender addon socket port"
    )
    blender_mode: str = Field(
        default="local-client",
        description="Blender connection mode: local-client or headless"
    )
    enable_polyhaven: bool = Field(
        default=True,
        description="Enable PolyHaven MCP tools"
    )
    enable_rodin: bool = Field(
        default=False,
        description="Enable Rodin MCP tools in local-client mode"
    )
    rodin_api_key: str = Field(
        default="",
        description="Rodin API key used by MCP server"
    )
    rodin_mode: str = Field(
        default="MAIN_SITE",
        description="Rodin backend mode (currently only MAIN_SITE is supported)"
    )
    # MCP Server
    mcp_server_host: str = Field(
        default="localhost",
        description="MCP server host"
    )
    mcp_server_port: int = Field(
        default=9877,
        description="MCP server port"
    )
    blender_headless_startup_timeout: int = Field(
        default=10,
        description="Seconds to wait for headless Blender startup"
    )

    # API Runtime
    api_port: int = Field(
        default=8000,
        description="Port for the main API service"
    )
    api_workers: int = Field(
        default=1,
        description="Number of API worker processes to run"
    )
    api_stream_timeout_seconds: int = Field(
        default=120,
        description="Max seconds to allow a single streaming response"
    )
    api_plan_stream_timeout_seconds: int = Field(
        default=1800,
        description="Max seconds to allow an idle plan_mode stream before timing out; <= 0 disables the idle timeout"
    )
    fast_mode_default: bool = Field(
        default=False,
        description="Default fast mode for chat requests when the client omits fast_mode"
    )
    plan_mode_max_agent_turns: int = Field(
        default=50,
        description="Maximum agent turns allowed for a single plan_mode request"
    )
    plan_mode_max_tool_batches: int = Field(
        default=40,
        description="Maximum tool batches allowed for a single plan_mode request"
    )
    plan_mode_max_replans: int = Field(
        default=3,
        description="Maximum replans allowed for a single plan_mode request"
    )
    headless_request_timeout_seconds: int = Field(
        default=15,
        description="Max seconds to allow a single headless scene/render request"
    )
    enable_penetration_verify: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "SCENE_AGENT_ENABLE_PENETRATION_VERIFY",
            "ENABLE_PENETRATION_VERIFY",
        ),
        description="Enable internal penetration checking during single-agent verify"
    )
    penetration_threshold_m: float = Field(
        default=0.02,
        validation_alias=AliasChoices(
            "SCENE_AGENT_PENETRATION_THRESHOLD_M",
            "PENETRATION_THRESHOLD_M",
        ),
        ge=0.0,
        description="Minimum penetration depth (meters) before verify reports a geometry failure"
    )
    penetration_max_candidate_pairs: int = Field(
        default=32,
        validation_alias=AliasChoices(
            "SCENE_AGENT_PENETRATION_MAX_CANDIDATE_PAIRS",
            "PENETRATION_MAX_CANDIDATE_PAIRS",
        ),
        ge=1,
        description="Maximum candidate pairs to narrow-phase check during internal penetration verify"
    )
    penetration_max_reported_pairs: int = Field(
        default=8,
        validation_alias=AliasChoices(
            "SCENE_AGENT_PENETRATION_MAX_REPORTED_PAIRS",
            "PENETRATION_MAX_REPORTED_PAIRS",
        ),
        ge=1,
        description="Maximum confirmed penetration pairs to include in verification payloads"
    )
    api_worker_id: str = Field(
        default_factory=lambda: f"{socket.gethostname()}-{os.getpid()}",
        description="Unique worker ID used for Redis ownership and diagnostics"
    )
    api_worker_advertise_url: str | None = Field(
        default=None,
        description="Worker base URL used for owner proxy routing between API workers"
    )

    # Redis control plane
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL for session control plane and checkpointer"
    )
    redis_key_prefix: str = Field(
        default="sa",
        description="Redis key prefix"
    )
    session_lease_ttl_seconds: int = Field(
        default=20,
        description="Lease TTL for session ownership"
    )
    session_heartbeat_interval_seconds: int = Field(
        default=5,
        description="Heartbeat interval for lease/activity refresh"
    )
    session_owner_unreachable_grace_seconds: int = Field(
        default=10,
        description="Grace seconds before takeover after owner unreachable"
    )

    # Session durability (headless mode)
    session_shared_storage_root: str = Field(
        default=os.path.join(_CACHE_ROOT, "sessions"),
        validation_alias=AliasChoices("SESSION_SHARED_STORAGE_ROOT", "SESSION_BLEND_ROOT"),
        description="Shared root directory for per-session .blend persistence"
    )
    session_idle_timeout_seconds: int = Field(
        default=600,
        description="Seconds of inactivity before stopping headless session processes"
    )
    session_sweep_interval_seconds: int = Field(
        default=30,
        description="Seconds between idle-session sweep checks"
    )
    frontend_session_quota_default: int = Field(
        default=1,
        description="Default max active headless sessions per frontend client"
    )
    frontend_session_quota_overrides: str = Field(
        default="",
        description="Per-client headless session quota overrides, format: clientA:2,clientB:1,*:1"
    )
    session_max_snapshots: int = Field(
        default=20,
        description="Maximum retained snapshots per session for undo"
    )

    # Reference Image Uploads
    reference_image_max_count: int = Field(
        default=3,
        description="Maximum number of reference images per conversation"
    )
    reference_image_max_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Maximum size (bytes) per reference image upload"
    )
    reference_image_storage_dir: str = Field(
        default=os.path.join(_CACHE_ROOT, "reference_images"),
        description="Filesystem directory for short-term reference image storage"
    )
    reference_image_helper_openai_model: str = Field(
        default="gpt-4.1-mini",
        description="Lightweight OpenAI model for reference-image naming and retrieval decisions"
    )
    reference_image_helper_gemini_model: str = Field(
        default="gemini-2.5-flash",
        description="Lightweight Gemini model for reference-image naming and retrieval decisions"
    )
    reference_image_helper_anthropic_model: str = Field(
        default="claude-3-5-haiku-20241022",
        description="Lightweight Anthropic model for reference-image naming and retrieval decisions"
    )
    reference_image_helper_qwen_model: str = Field(
        default="qwen-vl-plus-latest",
        description="Lightweight Qwen model for reference-image naming and retrieval decisions"
    )
    context_summary_helper_openai_model: str = Field(
        default="gpt-4.1-mini",
        description="Lightweight OpenAI model for context compression summaries"
    )
    context_summary_helper_gemini_model: str = Field(
        default="gemini-2.5-flash",
        description="Lightweight Gemini model for context compression summaries"
    )
    context_summary_helper_anthropic_model: str = Field(
        default="claude-3-5-haiku-20241022",
        description="Lightweight Anthropic model for context compression summaries"
    )
    context_summary_helper_qwen_model: str = Field(
        default="qwen-plus-latest",
        description="Lightweight Qwen model for context compression summaries"
    )
    
    # 3D Asset Retrieval API
    retrieval_api_host: str = Field(
        default="localhost",
        description="3D asset retrieval API host"
    )
    retrieval_api_port: int = Field(
        default=8001,
        description="3D asset retrieval API port"
    )
    enable_retrieval: bool = Field(
        default=False,
        description="Enable retrieval MCP tools"
    )
    enable_infinigen: bool = Field(
        default=False,
        description="Enable Infinigen MCP tools"
    )
    
    # RAG Configuration
    rag_enabled: bool = Field(
        default=False,
        description="Enable RAG for BPY script retrieval"
    )
    
    # Model configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )
    
    @field_validator("vlm_provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        """Validate that provider is supported"""
        valid_providers = {"openai", "anthropic", "gemini", "qwen"}
        v_lower = v.lower()
        if v_lower not in valid_providers:
            raise ValueError(
                f"Invalid VLM provider: {v}. "
                f"Must be one of: {', '.join(valid_providers)}"
            )
        return v_lower

    @field_validator("dual_agent_verifier_vlm_provider")
    @classmethod
    def validate_optional_verifier_provider(cls, v: str | None) -> str | None:
        if v is None:
            return None
        normalized = v.strip().lower()
        if not normalized:
            return None
        valid_providers = {"openai", "anthropic", "gemini", "qwen"}
        if normalized not in valid_providers:
            raise ValueError(
                f"Invalid dual-agent verifier VLM provider: {v}. "
                f"Must be one of: {', '.join(valid_providers)}"
            )
        return normalized

    @field_validator("dual_agent_verifier_vlm_model")
    @classmethod
    def normalize_optional_verifier_model(cls, v: str | None) -> str | None:
        if v is None:
            return None
        normalized = v.strip()
        return normalized or None

    @field_validator("blender_mode")
    @classmethod
    def validate_blender_mode(cls, v: str) -> str:
        valid_modes = {"local-client", "headless"}
        v_lower = v.lower()
        if v_lower not in valid_modes:
            raise ValueError(
                f"Invalid Blender mode: {v}. "
                f"Must be one of: {', '.join(sorted(valid_modes))}"
            )
        return v_lower

    @field_validator("rodin_mode")
    @classmethod
    def validate_rodin_mode(cls, v: str) -> str:
        valid_modes = {"MAIN_SITE"}
        v_upper = v.upper()
        if v_upper not in valid_modes:
            raise ValueError(
                f"Invalid Rodin mode: {v}. "
                f"Must be one of: {', '.join(sorted(valid_modes))}"
            )
        return v_upper

    @field_validator(
        "session_shared_storage_root",
        "reference_image_storage_dir",
        mode="before",
    )
    @classmethod
    def expand_user_storage_paths(cls, v: str | None) -> str | None:
        if v is None:
            return None
        text = str(v).strip()
        if not text:
            return text
        return os.path.expanduser(text)
    
    @property
    def blender_mcp_url(self) -> str:
        """Get the full Blender MCP server URL"""
        return f"http://{self.mcp_server_host}:{self.mcp_server_port}/mcp"
    
    @property
    def retrieval_api_url(self) -> str:
        """Get the full retrieval API URL"""
        return f"http://{self.retrieval_api_host}:{self.retrieval_api_port}"

    def get_vlm_provider_models(self, provider: str) -> list[str]:
        provider_lower = provider.lower()
        default_models = {
            "openai": ["gpt-4o"],
            "anthropic": ["claude-3-5-sonnet-20241022"],
            "gemini": ["gemini-2.5-pro"],
            "qwen": ["qwen-vl-max-latest"],
        }
        raw_by_provider = {
            "openai": self.vlm_openai_models,
            "anthropic": self.vlm_anthropic_models,
            "gemini": self.vlm_gemini_models,
            "qwen": self.vlm_qwen_models,
        }
        raw = raw_by_provider.get(provider_lower, "")
        models: list[str] = []
        for item in raw.split(","):
            name = item.strip()
            if name and name not in models:
                models.append(name)
        if not models:
            return default_models.get(provider_lower, [])
        return models

    def get_vlm_default_model(self, provider: str) -> str:
        provider_lower = provider.lower()
        if provider_lower == self.vlm_provider and self.vlm_model:
            return self.vlm_model
        models = self.get_vlm_provider_models(provider_lower)
        if models:
            return models[0]
        fallback = {
            "openai": "gpt-4o",
            "anthropic": "claude-3-5-sonnet-20241022",
            "gemini": "gemini-2.5-pro",
            "qwen": "qwen-vl-max-latest",
        }
        return fallback.get(provider_lower, "gpt-4o")

    def get_vlm_api_key(self, provider: str) -> str | None:
        provider_lower = provider.lower()
        provider_keys = {
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
            "qwen": self.qwen_api_key,
        }
        value = provider_keys.get(provider_lower)
        if isinstance(value, str):
            value = value.strip()
        return value or None

    def get_reference_image_helper_model(self, provider: str) -> str:
        provider_lower = provider.lower()
        helper_models = {
            "openai": self.reference_image_helper_openai_model,
            "anthropic": self.reference_image_helper_anthropic_model,
            "gemini": self.reference_image_helper_gemini_model,
            "qwen": self.reference_image_helper_qwen_model,
        }
        return helper_models.get(provider_lower) or helper_models["openai"]

    def get_context_summary_helper_model(self, provider: str) -> str:
        provider_lower = provider.lower()
        helper_models = {
            "openai": self.context_summary_helper_openai_model,
            "anthropic": self.context_summary_helper_anthropic_model,
            "gemini": self.context_summary_helper_gemini_model,
            "qwen": self.context_summary_helper_qwen_model,
        }
        return helper_models.get(provider_lower) or helper_models["openai"]

    def resolve_frontend_session_quota(self, client_id: str) -> int:
        default_quota = max(1, int(self.frontend_session_quota_default))
        raw = self.frontend_session_quota_overrides.strip()
        if not raw:
            return default_quota
        fallback_quota: int | None = None
        for token in raw.split(","):
            item = token.strip()
            if not item:
                continue
            if ":" in item:
                key, raw_value = item.split(":", 1)
            elif "=" in item:
                key, raw_value = item.split("=", 1)
            else:
                continue
            key = key.strip()
            try:
                quota_value = max(1, int(raw_value.strip()))
            except ValueError:
                continue
            if key == client_id:
                return quota_value
            if key == "*":
                fallback_quota = quota_value
        return fallback_quota if fallback_quota is not None else default_quota

    @property
    def session_blend_root(self) -> str:
        # Backward-compatible alias for existing call-sites.
        return self.session_shared_storage_root


# Global settings instance
_settings: Settings | None = None


def get_settings() -> Settings:
    """
    Get the global settings instance (singleton pattern).
    
    Returns:
        Settings instance
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings():
    """Reload settings from environment (useful for testing)"""
    global _settings
    _settings = None
    return get_settings()
