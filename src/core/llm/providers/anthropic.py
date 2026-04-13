from langchain_anthropic import ChatAnthropic
from pydantic_settings import BaseSettings
from src.core.llm.registry import BaseLLM, register_llm


@register_llm
class AnthropicLLM(BaseLLM):
    """Anthropic LLM implementation."""
    
    name = "anthropic"
    env_vars = ["llm_model", "llm_temperature", "llm_api_key"]
    
    def create_instance(self, settings: BaseSettings):
        """Create Anthropic LLM instance."""
        api_key = settings.llm_api_key
        if not api_key:
            raise ValueError("Anthropic API key is required")
        
        config = self.get_required_settings(settings)
        
        return ChatAnthropic(**config)
    
    @classmethod
    def get_required_settings(cls, settings: BaseSettings) -> dict:
        """Return dict of required setting keys for Anthropic LLM."""
        settings = {key: getattr(settings, key) for key in cls.env_vars}
        return {
            "model": settings["llm_model"],
            "temperature": settings["llm_temperature"],
            "anthropic_api_key": settings["llm_api_key"]
        }