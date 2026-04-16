from src.core.llm import llm
from src.schemas.discovery import PackageIntent
from src.state import AgenticDocsState


extractor = llm.with_structured_output(PackageIntent)


def intent_parser_node(state: AgenticDocsState) -> dict:
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
