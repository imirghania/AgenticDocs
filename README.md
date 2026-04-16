# AgenticDocs

An AI agent that generates comprehensive, structured documentation for any open-source package. Built with LangGraph and Streamlit.

Given a package name, AgenticDocs searches GitHub, asks you to confirm the right repo, ingests source code and official docs in parallel, evaluates quality across four dimensions, fills gaps through targeted enrichment, then writes a multi-chapter documentation set — with full resumption if interrupted.

---

## Pipeline

```
User prompt
    └─ Resumption inspector   detect prior progress; restore state from scratchpad
    └─ Intent parser          extract package name, language, ecosystem
    └─ Web discovery          search GitHub for matching repositories
    └─ Confirm package        you pick the right repo (HITL)
    └─ Docs discovery         find official documentation URL
    └─ Parallel ingestion
           ├─ Context7        fetch structured library docs via MCP
           ├─ Docs scraper    scrape the official documentation site
           └─ GitHub agent    extract source code, README, and examples
    └─ Quality judge          score across 4 dimensions; surface gaps
    └─ Enrichment agent       targeted gap-filling (if any dimension ≤ 2/5)
    └─ Chapter planner        LLM-designed chapter structure (no static fallback)
    └─ Write & review         each chapter written + peer-reviewed in parallel
    └─ Cross-reference pass   add backward/forward refs between chapters
    └─ Chapter assembler      combine into final output under output/{package}/
```

---

## Features

- **Resumable sessions** — every node writes a numbered scratchpad file to `sessions/{thread_id}/`. On restart, completed nodes are skipped automatically; state is restored from disk, not from memory.
- **Per-dimension quality scoring** — four dimensions (beginner friendliness, API coverage, code example quality, progressive structure), each scored 1–5. Enrichment runs only when needed.
- **LLM-planned chapters** — the chapter structure is always determined by the LLM from the actual source material. No static fallback.
- **Cross-chapter references** — after all chapters pass review, a dedicated pass inserts backward refs (`covered in *Chapter N*`) and forward refs (`explained in *Chapter N*`).
- **Session sidebar** — Streamlit UI lists all past sessions with status indicators and Resume / View buttons.
- **HITL package confirmation** — the graph pauses at `confirm_package` and resumes after your selection.

---

## Installation

**Requirements:** Python 3.12+, [uv](https://docs.astral.sh/uv/)

```bash
git clone <repo-url>
cd documentation-enhancer-agent
uv sync
cp .env.example .env
```

Edit `.env`:

```env
# Required
LLM_PROVIDER=anthropic          # or: openai
LLM_MODEL=claude-sonnet-4-5
LLM_API_KEY=your_api_key_here
TAVILY_API_KEY=your_tavily_key_here

# Optional — defaults to SQLite / in-memory if unset
POSTGRES_URL=postgresql://user:password@localhost:5432/agenticdocs
REDIS_URL=redis://localhost:6379/0
```

---

## Usage

```bash
uv run streamlit run streamlit_app.py
```

Open [http://localhost:8501](http://localhost:8501).

1. Enter a package name (e.g. *"httpx Python library"*)
2. Pick the matching GitHub repo from the displayed results
3. Watch each pipeline stage complete in real time
4. Download the finished documentation from the sidebar

### Output

Each chapter is written to `output/{package-slug}/` as an individual `.md` file with cross-references already inserted.

---

## Resumption

Progress is persisted to `sessions/{thread_id}/` as numbered files:

| File | Node |
|---|---|
| `01_search_results.json` | web_discovery |
| `02_confirmed_pkg.json` | confirm_package |
| `03_context7_docs.md` | context7_agent |
| `04_scraped_docs.md` | docs_scraper |
| `05_github_content.md` | github_agent |
| `06_quality_report.json` | quality_judge |
| `07_enrichment.md` | enrichment_agent |
| `08_chapter_plan.json` | chapter_planner |
| `09_crossref_done.json` | chapter_crossref |
| `10_final_output.md` | writer_agent |

If the process is interrupted, restart and click **Resume** in the sidebar. Completed nodes are skipped; the pipeline picks up from the first incomplete step.

---

## Storage backends

| Backend | When used | Purpose |
| --- | --- | --- |
| SQLite (`sessions/checkpoints.db`) | default | LangGraph short-term checkpoint |
| PostgreSQL | `POSTGRES_URL` set | LangGraph short-term checkpoint |
| InMemoryStore | default | Session metadata, user preferences |
| Redis | `REDIS_URL` set | Session metadata, user preferences |

---

## Adding a new LLM provider

1. Create `src/core/llm/providers/my_provider.py`:

```python
from langchain_myprovider import ChatMyProvider
from src.core.llm.registry import BaseLLM, register_llm

@register_llm
class MyProviderLLM(BaseLLM):
    name = "myprovider"
    env_vars = ["llm_model", "llm_temperature", "llm_api_key"]

    def create_instance(self, settings):
        config = self.get_required_settings(settings)
        return ChatMyProvider(**config)

    @classmethod
    def get_required_settings(cls, settings):
        return {
            "model":       getattr(settings, "llm_model"),
            "temperature": getattr(settings, "llm_temperature"),
            "api_key":     getattr(settings, "llm_api_key"),
        }
```

1. Import it in `src/core/llm/__init__.py`:

```python
from src.core.llm.providers import anthropic, openai, my_provider
```

1. Set `LLM_PROVIDER=myprovider` in `.env`.

---

## Running tests

```bash
uv run pytest tests/ -v
```
