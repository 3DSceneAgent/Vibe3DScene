"""
VLM provider implementations for OpenAI, Anthropic, and Gemini.
"""
from typing import Any
from vlm.base import BaseVLMProvider


class OpenAIProvider(BaseVLMProvider):
    """OpenAI GPT-4o provider with vision support"""
    
    def get_default_model(self) -> str:
        return "gpt-4o"
    
    def get_chat_model(self) -> Any:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=self.model,
            api_key=self.api_key,
            temperature=0.7,
        )


class AnthropicProvider(BaseVLMProvider):
    """Anthropic Claude provider with vision support"""
    
    def get_default_model(self) -> str:
        return "claude-3-5-sonnet-20241022"
    
    def get_chat_model(self) -> Any:
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=self.model,
            api_key=self.api_key,
            temperature=0.7,
        )


class GeminiProvider(BaseVLMProvider):
    """Google Gemini provider with vision support"""
    
    def get_default_model(self) -> str:
        return "gemini-2.5-pro"
    
    def get_chat_model(self) -> Any:
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=self.model,
            google_api_key=self.api_key,
            temperature=0.7,
        )


def get_vlm_provider(
    provider_name: str,
    api_key: str,
    model: str = None
) -> BaseVLMProvider:
    """
    Factory function to get a VLM provider by name.
    
    Args:
        provider_name: "openai", "anthropic", or "gemini"
        api_key: API key for the provider
        model: Optional model override
        
    Returns:
        Configured VLM provider instance
        
    Raises:
        ValueError: If provider name is unknown
    """
    providers = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "gemini": GeminiProvider,
    }
    
    provider_name = provider_name.lower()
    if provider_name not in providers:
        raise ValueError(
            f"Unknown provider: {provider_name}. "
            f"Available providers: {', '.join(providers.keys())}"
        )
    
    return providers[provider_name](api_key=api_key, model=model)
