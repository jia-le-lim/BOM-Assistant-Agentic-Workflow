"""Code-owned operational replies; never trust source-free model assertions."""

import json

CLARIFICATIONS = {
    "workspace": "Choose a workspace with @ or state its batch ID.",
    "item": "Which item ID or description should I look up? You can also name a part category.",
    "stockroom": "Which stockroom do you mean? State the stockroom ID with the item ID.",
    "quantity": "State the item ID and the exact Max, ROP or Min quantity you want to propose. I cannot choose a stock level for you.",
    "unsupported": "I don't have a data tool for that request. I can search workspace items by description or category, explain stored recommendations, retrieve history, or check the review queue and dormant coverage.",
    "spending": "I cannot retrieve actual purchasing spend or monthly spending totals with the available chat tools. Inventory exposure is a different measure. Provide purchasing transaction data for a spending analysis.",
}


def clarify_request(ctx, kind: str) -> dict:
    from .tools import ToolError
    if kind not in CLARIFICATIONS:
        raise ToolError("Choose a supported clarification kind.")
    return {"response_status": "unsupported" if kind in {"unsupported", "spending"} else "clarification",
            "message": CLARIFICATIONS[kind]}


def empty_message(ctx, name: str, args: dict) -> str:
    """Describe a real empty read, including known workspace prerequisites."""
    bid = args.get("batch_id", ctx.batch_id)
    global_tools = {"get_item_history", "get_item_notes", "search_similar_reviews", "recall_context"}
    if name not in global_tools and name not in {"get_page_context", "get_settings"}:
        if bid is None:
            return CLARIFICATIONS["workspace"]
        batch = ctx.conn.execute("SELECT status FROM batches WHERE batch_id=?", (bid,)).fetchone()
        if batch is None:
            return f"Workspace #{bid} does not exist. Check the workspace ID."
        if batch["status"] != "scored" and name != "get_current_values":
            return f"Workspace #{bid} has not been scored. Run recommendations first; uploaded items can be searched by description or category."
    labels = {
        "get_assist_verdict": "No saved assist verdict matched this item and workspace. Assist may not have run yet.",
        "list_assist_queue": "No saved assist rows matched these filters. Assist may not have run yet.",
        "get_similar_parts": "No saved peer evidence matched this item and workspace. Peer analysis may not have run yet.",
        "get_similarity_outliers": "No saved peer outliers matched this workspace. Peer analysis may not have run yet.",
        "get_dormant_coverage": "No scored dormant rows were found in this workspace.",
        "get_item_history": "No recorded review history was found for this item.",
        "get_item_notes": "No saved notes were found for this item.",
        "get_page_context": "No browser context was supplied. Name the page or item you want help with.",
    }
    if name in labels:
        return labels[name]
    if args.get("item_id"):
        return f"No matching record was found for item {args['item_id']} in workspace #{bid}. Check the item ID and stockroom, or search by description."
    return "No matching records were found for these filters. Try a broader search."


def render_records(results: list[tuple[str, dict]]) -> str:
    """Keep retrieved evidence available when narration fails or exhausts its budget."""
    from ..llm.echo import EchoProvider
    blocks = []
    for name, data in results:
        if data.get("error") or data.get("_empty") or data.get("response_status"):
            continue
        if name in {"search_items", "list_review_queue"}:
            blocks.append(render_items(data))
            continue
        if name == "propose_change":
            quantities = ", ".join(f"{label} {data[key]}" for key, label in
                                   (("proposed_max", "Max"), ("proposed_rop", "ROP"), ("proposed_min", "Min"))
                                   if data.get(key) is not None)
            blocks.append(f"Staged proposal #{data['pending_id']} for item {data['item_id']}: {quantities}. "
                          "It has not been applied; an engineer must confirm it and any required senior approval still applies.")
            continue
        try:
            rendered = EchoProvider()._render(name, data)
        except (KeyError, TypeError, ValueError):
            rendered = ""
        blocks.append(rendered or "Retrieved records:\n```json\n" + json.dumps(data, ensure_ascii=False, indent=2).replace("```", "` ` `") + "\n```")
    return "\n\n".join(blocks)


def render_items(data: dict) -> str:
    total = data["total_count"]
    text = f"Workspace **#{data['batch_id']}**: **{total} matching rows**; showing {data['returned_count']} from offset {data['offset']}."
    if not data["items"]:
        return text + (" Try a broader search." if not total else " This page is past the last result; use a smaller offset.")
    def cell(value):
        return str(value if value is not None else "unknown").replace("|", "\\|").replace("\n", " ")
    fields = ("item_id", "stockroom_id", "item_desc", "part_category", "current_max", "current_rop", "current_min", "risk_level")
    text += "\n\n| Item | Stockroom | Description | Category | Current Max | Current ROP | Current Min | Risk |\n| --- | --- | --- | --- | ---: | ---: | ---: | --- |\n"
    text += "\n".join("| " + " | ".join(cell(row.get(key)) for key in fields) + " |" for row in data["items"])
    if data["has_more"]:
        text += f"\n\nMore rows are available at offset {data['offset'] + data['returned_count']}."
    return text
