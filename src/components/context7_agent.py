from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents import create_agent
from src.state import DocSmithState
from src.core.llm import llm


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
        prompt="""You fetch library documentation from Context7.
        First resolve the library ID, then query for:
        1. Core concepts and architecture
        2. Full API reference
        3. Common usage patterns and examples"""
    )


async def context7_node(state: DocSmithState) -> dict:
    agent = await build_context7_agent(llm)
    result = await agent.ainvoke({
        "messages": [("user", f"Get documentation for {state['package_name']}")]
    })
    return {"context7_docs": [result["messages"][-1].content]}