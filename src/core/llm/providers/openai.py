from langchain_openai import ChatOpenAI
from pydantic_settings import BaseSettings
from src.core.llm.registry import BaseLLM, register_llm


@register_llm
class OpenAILLM(BaseLLM):
    """OpenAI LLM implementation."""
    
    name = "openai"
    env_vars = ["llm_model", "llm_temperature", "llm_api_key"]
    
    def create_instance(self, settings: BaseSettings):
        """Create OpenAI LLM instance."""
        api_key = settings.llm_api_key
        if not api_key:
            raise ValueError("OpenAI API key is required")
        
        config = self.get_required_settings(settings)
        
        return ChatOpenAI(**config)
    
    @classmethod
    def get_required_settings(cls, settings: BaseSettings):
        """Return dict of required setting keys for OpenAI LLM."""
        settings = {key: getattr(settings, key) for key in cls.env_vars}
        return {
            "model": settings["llm_model"],
            "temperature": settings["llm_temperature"],
            "openai_api_key": settings["llm_api_key"]
        }