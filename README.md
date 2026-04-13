# DocSmith

An AI agent that generates comprehensive, high-quality documentation for any open-source package. Built with LangGraph and Chainlit.

Given a package name, DocSmith finds the GitHub repository, confirms it with you, discovers the official documentation URL, ingests the source code and docs in parallel, evaluates quality, and writes structured documentation — all in a single conversational session.

## How it works

```
User prompt
    └─ Intent parser        extract package name, language, ecosystem
    └─ Web discovery        search GitHub for matching repositories
    └─ Confirm package      you confirm the right repo in plain language
    └─ Docs discovery       read GitHub API for homepage, fall back to web search
    └─ Parallel ingestion
           ├─ Context7      fetch structured library docs via MCP
           ├─ Docs scraper  scrape the official documentation site
           └─ GitHub agent  extract source code, README, and examples
    └─ Quality judge        score documentation across 4 dimensions
    └─ Enrichment agent     targeted gap-filling searches (if score < 0.7)
    └─ Writer agent         produce the final documentation
```

---

## Installation

### Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager

### Steps

```bash
# 1. Clone the repository
git clone <repo-url>
cd documentation-enhancer-agent

# 2. Install dependencies
uv sync

# 3. Configure environment variables
cp .env.example .env
```

Edit `.env` with your API keys:

```env
# LLM provider settings
LLM_PROVIDER=anthropic          # or: openai
LLM_MODEL=claude-sonnet-4-5    # or any model supported by your provider
LLM_TEMPERATURE=0.0
LLM_API_KEY=your_api_key_here

# Tavily (used for web discovery)
TAVILY_API_KEY=your_tavily_api_key_here
```

---

## Usage

Start the Chainlit app:

```bash
uv run chainlit run src/chainlit_app.py
```

Then open [http://localhost:8000](http://localhost:8000) in your browser.

### Example session

**You:** Generate documentation for the `httpx` Python library.

**DocSmith:** I found these results. Which package did you mean?

```
1. encode/httpx — A next-generation HTTP client for Python
   https://github.com/encode/httpx
2. ...
```

**You:** The first one.

DocSmith confirms the repository, discovers the docs URL from the GitHub metadata, ingests everything in parallel, and streams back a fully structured documentation page.

### Confirmation is natural language

When DocSmith presents search results, you can respond however feels natural:

| What you type | What happens |
|---|---|
| `1` or `the first one` | Selects result 1 |
| `httpx` | Matches by name |
| `none of these, I meant requests` | Restarts with `requests` |
| `the Python async one` | DocSmith asks a follow-up if ambiguous |

---

## Adding a new LLM provider

DocSmith uses a decorator-based registry. Adding a provider takes three steps.

### 1. Create the provider class

Create a new file in `src/core/llm/providers/`:

```python
# src/core/llm/providers/my_provider.py
from langchain_myprovider import ChatMyProvider
from pydantic_settings import BaseSettings
from src.core.llm.registry import BaseLLM, register_llm


@register_llm
class MyProviderLLM(BaseLLM):
    name = "myprovider"  # matched against LLM_PROVIDER in .env
    env_vars = ["llm_model", "llm_temperature", "llm_api_key"]

    def create_instance(self, settings: BaseSettings):
        config = self.get_required_settings(settings)
        return ChatMyProvider(**config)

    @classmethod
    def get_required_settings(cls, settings: BaseSettings) -> dict:
        return {
            "model":       getattr(settings, "llm_model"),
            "temperature": getattr(settings, "llm_temperature"),
            "api_key":     getattr(settings, "llm_api_key"),
        }
```

### 2. Register it

Import the provider in `src/core/llm/__init__.py` so the `@register_llm` decorator runs at startup:

```python
# src/core/llm/__init__.py
from src.core.llm.providers import anthropic, openai, my_provider  # add your module
```

### 3. Set your environment variables

```env
LLM_PROVIDER=myprovider
LLM_MODEL=my-model-name
LLM_API_KEY=your_key_here
```

The `get_llm()` factory reads `LLM_PROVIDER`, looks it up in the registry, and calls `create_instance()`. No other code changes are needed.
