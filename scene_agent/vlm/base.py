"""
Base VLM provider interface.
Abstract class for different vision-language model providers.
"""
from abc import ABC, abstractmethod
from typing import Any


class BaseVLMProvider(ABC):
    """
    Abstract base class for VLM providers.
    Allows swapping between OpenAI, Anthropic, Gemini, etc.
    """
    
    def __init__(self, api_key: str, model: str = None):
        """
        Initialize the VLM provider.
        
        Args:
            api_key: API key for the provider
            model: Optional model override
        """
        self.api_key = api_key
        self.model = model or self.get_default_model()
    
    @abstractmethod
    def get_default_model(self) -> str:
        """Get the default model name for this provider"""
        pass
    
    @abstractmethod
    def get_chat_model(self) -> Any:
        """
        Get the LangChain chat model instance.
        
        Returns:
            LangChain chat model compatible with bind_tools()
        """
        pass
    
    def supports_vision(self) -> bool:
        """Check if the model supports vision/image inputs"""
        return True  # All modern models support vision
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model})"
