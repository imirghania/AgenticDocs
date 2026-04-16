import json
from typing import Optional

from pydantic import BaseModel, model_validator
from langgraph.types import interrupt

from src.core.llm import llm
from src.graph.resumption import skippable
from src.graph.scratchpad import write_scratchpad
from src.state import AgenticDocsState


class PackageSelectionResult(BaseModel):
    action: str  # "select" | "none" | "clarify"
    selected_index: Optional[int] = None        # 0-based; only when action == "select"
    new_package_name: Optional[str] = None      # only when action == "none"
    clarification_question: Optional[str] = None  # only when action == "clarify"

    @model_validator(mode="after")
    def check_consistency(self) -> "PackageSelectionResult":
        if self.action == "select":
            assert self.selected_index is not None, \
                "selected_index required when action is 'select'"
        elif self.action == "none":
            assert self.new_package_name is not None, \
                "new_package_name required when action is 'none'"
        elif self.action == "clarify":
            assert self.clarification_question is not None, \
                "clarification_question required when action is 'clarify'"
        else:
            raise ValueError(f"Unknown action: {self.action}")
        return self


_SELECTION_SYSTEM_PROMPT = """\
You interpret a user's natural-language response to a package search result list.
Your job is to map their response to one of three actions.

You will be given:
- RESULTS: a numbered list of search results (1-based numbering for the user)
- USER_RESPONSE: the user's free-text answer

Mapping rules:
- action = "select":
    Use when the user clearly identifies one result.
    Ordinals ("first", "second", "1st", "2nd", "#2", "the top one", "result 1", "option 1") → map
    to that 1-based position → output as 0-based selected_index.
    Name match: if the user types a package name (e.g. "numpy", "requests") and exactly one
    result's title contains it (case-insensitive) → select that result.
    If multiple titles match the typed name, fall back to "clarify".
- action = "none":
    Use when the user says "none", "neither", "none of these", "I meant X", "actually X",
    or any variant indicating all results are wrong and they want something different.
    Extract the intended package name into new_package_name.
    If they say "none" without naming an alternative, use action "clarify" instead.
- action = "clarify":
    Use when the response is ambiguous, matches multiple results, or you cannot determine a clear
    selection. Write a short, direct clarification_question. Reference the results by number so
    the user can answer "1", "2", etc. Never ask more than one question at a time.

Important:
- selected_index is 0-based (subtract 1 from the user's 1-based position).
- Do not hallucinate package names. Only extract names explicitly stated by the user.
- Be liberal in recognising ordinals: "the first one", "first", "1", "#1", "result 1" all mean
    index 0.
"""


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
            ("system", _SELECTION_SYSTEM_PROMPT),
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
