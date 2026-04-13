from src.state import DocSmithState
from src.core.llm import llm

WRITER_SYSTEM_PROMPT = """You are a world-class technical documentation writer.
Your goal is to write documentation that is dramatically better than the source material.

STRUCTURE (always follow this order):
1. **Overview** — What problem does this library solve? Why would someone choose it?
2. **Installation** — Exact commands for all supported package managers.
3. **Quick start** — A working "Hello World" in under 10 lines. No prior knowledge assumed.
4. **Core concepts** — Explain the mental model before the API. Use analogies.
5. **API reference** — Every public class and function, with typed signatures and descriptions.
6. **Use cases** — A dedicated section for each major use case with a complete, runnable example.
7. **Common patterns** — Recipes for the most frequent real-world tasks.
8. **Troubleshooting** — The top 5 errors users encounter and how to fix them.

QUALITY RULES:
- Every code example must be complete and runnable on its own.
- Never say "see the docs" — explain it inline.
- Use progressive disclosure: simple version first, advanced options later.
- Include type annotations in all code examples.
- Every section should stand alone — assume the reader may jump directly to it.
"""

def writer_node(state: DocSmithState) -> dict:
    context = "\n\n---\n\n".join(
        state.get("context7_docs", []) +
        state.get("scraped_docs", []) +
        state.get("github_content", [])
    )[:120_000]

    result = llm.invoke([
        ("system", WRITER_SYSTEM_PROMPT),
        ("user", f"""Write comprehensive documentation for: {state['package_name']} ({state['language']})

Use this source material as your knowledge base:
{context}

Quality report (areas to focus on):
{state.get('quality_report', {})}""")
    ])

    return {
        "final_documentation": result.content,
        "messages": [result]
    }