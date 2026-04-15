from pydantic import BaseModel, Field

from src.core.llm import llm
from src.state import DocSmithState


class PackageIntent(BaseModel):
    package_name: str = Field(description="The name of the software package or library")
    language: str = Field(description="The programming language the package belongs to (e.g., Python, JS, Rust)")
    ecosystem: str = Field(description="The package manager or ecosystem (e.g., pypi, npm, crates.io)")
    hints: list[str] = Field(description="Any extra context the user gave")


extractor = llm.with_structured_output(PackageIntent)


def intent_parser_node(state: DocSmithState) -> dict:
    # user_msg = state["messages"][-1].content
    user_msg = state["messages"][0].content
    print("[Intent parser] Parsing user message: ", user_msg)
    result = extractor.invoke(
        f"Extract the package details from this request: {user_msg}"
    )
    print("[Intent parser] Parsing extractor result: ", result)
    
    print("[Parsed intent result] ", {
        "package_name": result.package_name,
        "language": result.language,
        "ecosystem": result.ecosystem,
    })
    # scratchpad_dir is already set by resumption_inspector to sessions/{thread_id}/
    return {
        "package_name": result.package_name,
        "language": result.language,
        "ecosystem": result.ecosystem,
    }
