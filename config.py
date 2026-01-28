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
    
    # Blender MCP Server
    blender_host: str = Field(
        default="localhost",
        description="Blender MCP server host"
    )
    blender_port: int = Field(
        default=9876,
        description="Blender MCP server port"
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
    
    @property
    def blender_mcp_url(self) -> str:
        """Get the full Blender MCP server URL"""
        return f"http://{self.blender_host}:{self.blender_port}/mcp"
    
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
