from langgraph.types import Command
import chainlit as cl

from src.graph.orchestrator import graph as compiled_graph


NODE_STATUS = {
    "intent_parser":    "Parsing your request...",
    "web_discovery":    "Searching GitHub for matching repositories...",
    "docs_discovery":   "Finding official documentation URL...",
    "context7_agent":   "Fetching structured docs via Context7...",
    "docs_scraper":     "Scraping official documentation site...",
    "github_agent":     "Extracting source code from GitHub...",
    "aggregator":       "Aggregating ingested content...",
    "quality_judge":    "Evaluating documentation quality...",
    "enrichment_agent":     "Filling documentation gaps...",
    "chapter_planner":      "Planning documentation chapters...",
    "write_review_chapter": "Writing and reviewing chapter...",
    "chapter_assembler":    "Assembling final documentation...",
}


@cl.on_chat_start
async def on_chat_start():
    cl.user_session.set("graph", compiled_graph)


@cl.on_message
async def on_message(msg: cl.Message):
    config = {"configurable": {"thread_id": cl.context.session.id}}
    graph = cl.user_session.get("graph")
    current_input = {"messages": [("user", msg.content)]}

    while True:
        interrupt_hit = False
        resume_input = None
        streaming_msgs: dict[str, cl.Message] = {}

        astream = graph.astream(
            current_input,
            config=config,
            stream_mode=["messages", "updates"],
        )
        try:
            async for mode, data in astream:
                if mode == "updates":
                    if "__interrupt__" in data:
                        payload = data["__interrupt__"][0].value

                        if isinstance(payload, dict) and payload.get("type") == "package_confirmation":
                            results = payload["results"]
                            result_lines = "\n".join(
                                f"{i + 1}. **{r['title'][:60]}**\n   {r['url']}"
                                for i, r in enumerate(results)
                            )
                            res = await cl.AskUserMessage(
                                content=(
                                    f"{payload['message']}\n\n"
                                    f"{result_lines}\n\n"
                                    "Type the number, name, or describe which one you meant."
                                ),
                                timeout=120,
                            ).send()
                            resume_input = Command(resume={"text": res.get("output", "")})
                            break

                        elif isinstance(payload, dict) and payload.get("type") == "package_clarification":
                            res = await cl.AskUserMessage(
                                content=payload["message"],
                                timeout=120,
                            ).send()
                            resume_input = Command(resume={"text": res.get("output", "")})
                            break

                    else:
                        # Node completed — show status message and output file if writer finished
                        for node_name, node_update in data.items():
                            status = NODE_STATUS.get(node_name)
                            if status:
                                await cl.Message(content=f"_{status}_").send()
                            if isinstance(node_update, dict) and node_update.get("output_file"):
                                await cl.Message(
                                    content=f"Saved to `{node_update['output_file']}`"
                                ).send()

                elif mode == "messages":
                    message, _ = data
                    if not (hasattr(message, "content") and message.content):
                        continue
                    content = message.content
                    if isinstance(content, list):
                        content = "".join(
                            part["text"] for part in content
                            if isinstance(part, dict) and part.get("type") == "text"
                        )
                    if not content:
                        continue
                    msg_id = getattr(message, "id", None)
                    if msg_id not in streaming_msgs:
                        streaming_msgs[msg_id] = cl.Message(content="")
                        await streaming_msgs[msg_id].send()
                    await streaming_msgs[msg_id].stream_token(content)
        finally:
            await astream.aclose()

        if resume_input is not None:
            interrupt_hit = True
            current_input = resume_input

        if not interrupt_hit:
            break
