from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from typing import Optional

load_dotenv()  # populate os.environ before any third-party library reads it


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # LLM configuration
    llm_provider: str = "anthropic" 
    llm_model: str = "claude-sonnet-4-5"  
    llm_api_key: Optional[str] = None
    llm_temperature: float = 0.0
    
    tavily_api_key: Optional[str] = None
    
    langsmith_api_key: Optional[str] = None
    
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Global settings instance
settings = Settings()