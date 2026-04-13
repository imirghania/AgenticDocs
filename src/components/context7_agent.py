from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents import create_agent

from src.core.llm import llm
from src.state import DocSmithState


async def build_context7_agent(llm):
    client = MultiServerMCPClient({
        "context7": {
            "transport": "http",
            "url": "https://mcp.context7.com/mcp",
        }
    })

    tools = await client.get_tools()   # resolve-library-id + query-docs

    return create_agent(
        llm,
        tools=tools,
        name="context7_agent",
        system_prompt="""You fetch library documentation from Context7.
        First resolve the library ID, then query for:
        1. Core concepts and architecture
        2. Full API reference
        3. Common usage patterns and examples"""
    )


async def context7_node(state: DocSmithState) -> dict:
    try:
        agent = await build_context7_agent(llm)
        result = await agent.ainvoke({
            "messages": [("user", f"Get ALL documentation for {state['package_name']} including API reference, examples, and core concepts.")]
        })
        content = result["messages"][-1].content
    except Exception as e:
        content = f"# Context7 documentation fetch failed\n\nError: {e}\n"

    path = Path(state["scratchpad_dir"]) / "context7.md"
    path.write_text(content, encoding="utf-8")

    return {"scratchpad_files": [str(path)]}
