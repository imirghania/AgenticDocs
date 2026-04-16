from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents import create_agent

from src.core.llm import llm
from src.graph.resumption import skippable
from src.graph.scratchpad import write_scratchpad
from src.prompts.context7 import CONTEXT7_SYSTEM_PROMPT
from src.state import AgenticDocsState


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
        system_prompt=CONTEXT7_SYSTEM_PROMPT
    )


@skippable("context7_agent")
async def context7_node(state: AgenticDocsState) -> dict:
    try:
        agent = await build_context7_agent(llm)
        result = await agent.ainvoke({
            "messages": [("user", f"Get ALL documentation for {state['package_name']} including API reference, examples, and core concepts.")]
        })
        content = result["messages"][-1].content
    except Exception as e:
        content = f"# Context7 documentation fetch failed\n\nError: {e}\n"

    written_path = write_scratchpad(state["thread_id"], "context7_agent", content)
    return {"scratchpad_files": [str(written_path)]}
