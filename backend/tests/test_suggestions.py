from app.agent.suggestions import predict_next_steps
from app.llm.provider import Response, ToolCall, ToolSpec


class SuggestionProvider:
    name = "test"
    model = "suggestion-test"

    def __init__(self, response: Response):
        self.response = response
        self.messages = []
        self.tools = []

    def chat(self, messages, tools):
        self.messages = messages
        self.tools = tools
        return self.response


def capability(name: str) -> ToolSpec:
    return ToolSpec(name=name, description=f"Use {name}", parameters={})


def test_model_predictions_are_structured_and_safety_filtered():
    provider = SuggestionProvider(Response(tool_calls=[ToolCall(
        id="next-1",
        name="suggest_next_steps",
        arguments={
            "intent": "Understand why item 100007 needs review",
            "suggestions": [
                {"label": "Check history", "prompt": "Show history for item 100007"},
                {"label": "Check levels", "prompt": "Show current levels for item 100007"},
                {"label": "Change max", "prompt": "Set item 100007 max to 12"},
                {"label": "Unknown item", "prompt": "Show history for item 999999"},
            ],
        },
    )]))

    prediction = predict_next_steps(
        "Why was item 100007 flagged?",
        "Item 100007 has high exposure.",
        [capability("get_item_history"), capability("get_current_values")],
        provider=provider,
    )

    assert prediction == {
        "intent": "Understand why item 100007 needs review",
        "suggestions": [
            {"label": "Check history", "prompt": "Show history for item 100007"},
            {"label": "Check levels", "prompt": "Show current levels for item 100007"},
        ],
    }
    assert [tool.name for tool in provider.tools] == ["suggest_next_steps"]
    assert "get_item_history" in provider.messages[-1].content


def test_invalid_model_output_has_no_hardcoded_fallback():
    provider = SuggestionProvider(Response(content="not valid structured output"))
    prediction = predict_next_steps(
        "What should I do?", "No grounded records were found.", [], provider=provider)
    assert prediction == {"intent": "", "suggestions": []}
