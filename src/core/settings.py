from pydantic_settings import BaseSettings
from typing import Optional, Dict, Any


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # LLM configuration
    llm_provider: str = "anthropic"  # Default provider
    llm_model: str = "claude-sonnet-4-5"  # Default model
    llm_temperature: float = 0.0  # Default temperature
    tavily_api_key: Optional[str] = None  # For web discovery component
    langsmith_api_key: Optional[str] = None  # For code analysis component
    
    # API Keys - more generic approach
    llm_api_key: Optional[str] = None
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Global settings instance
settings = Settings()