from pydantic import BaseModel

from src.core.llm import llm
from src.state import DocSmithState


class PackageIntent(BaseModel):
    package_name: str
    language: str
    ecosystem: str          # "pypi" | "npm" | "cargo" | "maven" | "gem" | "unknown"
    hints: list[str]        # any extra context the user gave


extractor = llm.with_structured_output(PackageIntent)

def intent_parser_node(state: DocSmithState) -> dict:
    user_msg = state["messages"][-1].content
    result = extractor.invoke(
        f"Extract the package details from this request: {user_msg}"
    )
    return {
        "package_name": result.package_name,
        "language": result.language,
        "ecosystem": result.ecosystem,
    }