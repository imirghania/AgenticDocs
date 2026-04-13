"""
LLM Registry Pattern Implementation with Decorator-based Registration
"""
from abc import ABC, abstractmethod
from typing import Dict, Type, Optional, Any, List


class BaseLLM(ABC):
    """Abstract base class for LLM implementations."""
    
    @abstractmethod
    def create_instance(self) -> Any:
        """Create and return an LLM instance."""
        pass
    
    @classmethod
    @abstractmethod
    def get_required_settings(cls) -> List[str]:
        """Return list of required setting keys for this LLM provider."""
        pass


class LLMRegistry:
    """Registry for LLM implementations."""
    
    def __init__(self):
        self._providers: Dict[str, Type[BaseLLM]] = {}
    
    def register(self, provider_class: Type[BaseLLM]):
        """Register an LLM provider using decorator."""
        provider_name = provider_class.name
        self._providers[provider_name] = provider_class
        return provider_class  # Return the class for decorator usage
    
    def get_provider(self, name: str) -> Optional[Type[BaseLLM]]:
        """Get a registered LLM provider by name."""
        return self._providers.get(name)
    
    def list_providers(self) -> List[str]:
        """List all registered provider names."""
        return list(self._providers.keys())


# Global registry instance
llm_registry = LLMRegistry()

# Decorator for automatic registration
def register_llm(cls):
    """Decorator to automatically register LLM implementations."""
    return llm_registry.register(cls)