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
    vlm_api_key: str = Field(
        ...,
        description="API key for the VLM provider"
    )
    vlm_model: str | None = Field(
        default=None,
        description="Optional model override (uses provider default if not set)"
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
    
    @property
    def blender_mcp_url(self) -> str:
        """Get the full Blender MCP server URL"""
        return f"http://{self.mcp_server_host}:{self.mcp_server_port}/mcp"
    
    @property
    def retrieval_api_url(self) -> str:
        """Get the full retrieval API URL"""
        return f"http://{self.retrieval_api_host}:{self.retrieval_api_port}"


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
