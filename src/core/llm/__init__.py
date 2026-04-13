# Core package initialization
from pydantic_settings import BaseSettings
from src.core.llm.registry import llm_registry
from src.core.settings import settings

# Import providers so their @register_llm decorators run and populate the registry
from src.core.llm.providers import anthropic, openai  # noqa: F401


def get_llm(settings: BaseSettings):
    """
    Get LLM instance based on settings using registry pattern.
    Reads the provider from settings and initializes the appropriate LLM.
    """
    provider_name = settings.llm_provider
    provider_class = llm_registry.get_provider(provider_name)
    
    if provider_class is None:
        # Fallback to first available provider if configured provider not found
        available_providers = llm_registry.list_providers()
        if available_providers:
            provider_name = available_providers[0]
            provider_class = llm_registry.get_provider(provider_name)
        else:
            raise ValueError("No LLM providers registered")
    
    return provider_class().create_instance(settings)


llm = get_llm(settings)