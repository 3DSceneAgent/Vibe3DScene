"""
Configuration management using Pydantic Settings.
Loads configuration from environment variables with validation.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator


class Settings(BaseSettings):
    """
    Application settings with environment variable support.
    Reads from .env file and environment variables.
    """
    
    # VLM Configuration
    vlm_provider: str = Field(
        default="openai",
        description="VLM provider: openai, anthropic, or gemini"
    )
    vlm_api_key: str | None = Field(
        default=None,
        description="Fallback API key for VLM providers (used if provider-specific key is not set)"
    )
    vlm_model: str | None = Field(
        default=None,
        description="Optional model override (uses provider default if not set)"
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
        description="Rodin backend mode: MAIN_SITE or FAL_AI"
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
    headless_request_timeout_seconds: int = Field(
        default=15,
        description="Max seconds to allow a single headless scene/render request"
    )

    # Session durability (headless mode)
    session_blend_root: str = Field(
        default="/tmp/scene_agent_sessions",
        description="Root directory for per-session .blend persistence"
    )
    session_idle_timeout_seconds: int = Field(
        default=600,
        description="Seconds of inactivity before stopping headless session processes"
    )
    session_sweep_interval_seconds: int = Field(
        default=30,
        description="Seconds between idle-session sweep checks"
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
        default="/tmp/scene_agent_reference_images",
        description="Filesystem directory for short-term reference image storage"
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
        valid_providers = {"openai", "anthropic", "gemini"}
        v_lower = v.lower()
        if v_lower not in valid_providers:
            raise ValueError(
                f"Invalid VLM provider: {v}. "
                f"Must be one of: {', '.join(valid_providers)}"
            )
        return v_lower

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
        valid_modes = {"MAIN_SITE", "FAL_AI"}
        v_upper = v.upper()
        if v_upper not in valid_modes:
            raise ValueError(
                f"Invalid Rodin mode: {v}. "
                f"Must be one of: {', '.join(sorted(valid_modes))}"
            )
        return v_upper
    
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
        }
        raw_by_provider = {
            "openai": self.vlm_openai_models,
            "anthropic": self.vlm_anthropic_models,
            "gemini": self.vlm_gemini_models,
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
        }
        return fallback.get(provider_lower, "gpt-4o")

    def get_vlm_api_key(self, provider: str) -> str | None:
        provider_lower = provider.lower()
        provider_keys = {
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
        }
        return provider_keys.get(provider_lower) or self.vlm_api_key


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
