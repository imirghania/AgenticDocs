from langgraph.types import Command
import chainlit as cl

from src.graph.orchestrator import graph as compiled_graph


@cl.on_chat_start
async def on_chat_start():
    cl.user_session.set("graph", compiled_graph)


@cl.on_message
async def on_message(msg: cl.Message):
    config = {"configurable": {"thread_id": cl.context.session.id}}
    graph = cl.user_session.get("graph")

    # First invocation uses the user message; subsequent resume iterations use Command.
    current_input = {"messages": [("user", msg.content)]}

    while True:
        interrupt_hit = False

        async for chunk, _ in graph.astream(
            current_input,
            config=config,
            stream_mode=["messages", "updates"]
        ):
            if isinstance(chunk, dict) and chunk.get("type") == "package_confirmation":
                # First interrupt: show numbered results list and ask for free-text input
                interrupt_hit = True
                results = chunk["results"]
                result_lines = "\n".join(
                    f"{i + 1}. **{r['title'][:60]}**\n   {r['url']}"
                    for i, r in enumerate(results)
                )
                res = await cl.AskUserMessage(
                    content=(
                        f"{chunk['message']}\n\n"
                        f"{result_lines}\n\n"
                        "Type the number, name, or describe which one you meant."
                    ),
                    timeout=120,
                ).send()
                current_input = Command(resume={"text": res.get("output", "")})
                break  # exit inner loop, re-enter outer while to resume graph

            elif isinstance(chunk, dict) and chunk.get("type") == "package_clarification":
                # Follow-up interrupt: show the LLM's clarification question
                interrupt_hit = True
                res = await cl.AskUserMessage(
                    content=chunk["message"],
                    timeout=120,
                ).send()
                current_input = Command(resume={"text": res.get("output", "")})
                break  # exit inner loop, re-enter outer while to resume graph

            elif hasattr(chunk, "content") and chunk.content:
                await cl.Message(content=chunk.content).send()

        # If no interrupt was hit, the graph ran to completion (or error)
        if not interrupt_hit:
            break
