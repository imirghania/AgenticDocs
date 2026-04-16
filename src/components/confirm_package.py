import json
from typing import Optional

from langgraph.types import interrupt

from src.core.llm import llm
from src.graph.resumption import skippable
from src.graph.scratchpad import write_scratchpad
from src.prompts.confirmation import SELECTION_SYSTEM_PROMPT
from src.schemas.discovery import PackageSelectionResult
from src.state import AgenticDocsState


_interpreter = llm.with_structured_output(PackageSelectionResult)


def _format_results(results: list[dict]) -> str:
    lines = []
    for i, r in enumerate(results, start=1):
        lines.append(f"{i}. {r['title']} — {r['url']}")
    return "\n".join(lines)


def _extract_github_url(result: dict) -> Optional[str]:
    url = result.get("url", "")
    return url if "github.com" in url else None


def _extract_docs_url(result: dict) -> Optional[str]:
    url = result.get("url", "")
    doc_hints = ("readthedocs.io", "docs.rs", "jsr.io", "/docs")
    return url if any(h in url for h in doc_hints) else None


@skippable("confirm_package")
def confirm_package_node(state: AgenticDocsState) -> dict:
    results = state["search_results"][:5]
    formatted = _format_results(results)

    # First interrupt: present results and ask for a natural-language selection
    user_response = interrupt({
        "type": "package_confirmation",
        "results": results,
        "message": "I found these results. Which package did you mean?",
    })

    # Interpretation loop — repeats until the user's intent is resolved
    while True:
        text = user_response.get("text", "")

        parsed: PackageSelectionResult = _interpreter.invoke([
            ("system", SELECTION_SYSTEM_PROMPT),
            ("user", f"RESULTS:\n{formatted}\n\nUSER_RESPONSE:\n{text}"),
        ])

        if parsed.action == "select":
            idx = parsed.selected_index
            if not (0 <= idx < len(results)):
                user_response = interrupt({
                    "type": "package_clarification",
                    "message": (
                        f"I couldn't find result #{idx + 1}. "
                        f"Please choose a number between 1 and {len(results)}."
                    ),
                })
                continue
            confirmed = results[idx]
            payload = {
                "confirmed_package": confirmed,
                "github_url": _extract_github_url(confirmed),
                "docs_url": _extract_docs_url(confirmed),
            }
            write_scratchpad(state["thread_id"], "confirm_package", json.dumps(payload, indent=2))
            return payload

        elif parsed.action == "none":
            payload = {
                "confirmed_package": None,
                "package_name": parsed.new_package_name,
                "github_url": None,
                "docs_url": None,
            }
            write_scratchpad(state["thread_id"], "confirm_package", json.dumps(payload, indent=2))
            return payload

        elif parsed.action == "clarify":
            user_response = interrupt({
                "type": "package_clarification",
                "message": parsed.clarification_question,
            })
