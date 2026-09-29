"""Source-free social replies and help, separate from factual BOM answers."""

from __future__ import annotations

import re


HELP_REPLY = (
    "I can help you review your BOM: find items by description or category, "
    "explain recommendations, check current Max/ROP/Min, show review history, "
    "and find high-risk or high-exposure items. I can also help with settings "
    "and prepare changes for your approval. "
    "Try 'show high risk items', 'find cable items', or 'why item <id>'. "
    "What would you like to review?"
)

REPLIES = {
    "greeting": (
        "Hi! I'm NYRA, your BOM review assistant. I can help you find items, "
        "explain recommendations, or review high-risk items. "
        "What would you like to look at?"
    ),
    "thanks": "You're welcome! Let me know what you'd like to review next.",
    "acknowledgement": "Got it. Let me know what you'd like to review next.",
    "smalltalk": "I'm here and ready to help with your BOM review. What would you like to look at?",
    "goodbye": "See you next time! You can return here when you're ready to continue your review.",
    "help": HELP_REPLY,
}

# Match the whole message. A greeting before a request must not consume the
# request, and acknowledgements must never double as approval of a proposal.
PATTERNS = {
    "greeting": r"(?:hi|hello|hey|hiya|howdy|good (?:morning|afternoon|evening))(?: (?:there|nyra|assistant))?",
    "thanks": r"(?:thanks(?: a lot)?|thank you(?: very much)?|thx|cheers)(?: nyra)?",
    "acknowledgement": r"(?:ok|okay|got it|understood|sounds good|great|cool)",
    "smalltalk": r"(?:how are you(?: doing)?|how's it going|are you there|you there|are you online|are you working)",
    "goodbye": r"(?:bye|goodbye|see you|see you later)(?: nyra)?",
    "help": (
        r"(?:help(?: me)?|can you help(?: me)?|what can you do|what can you help(?: me)? with|"
        r"how can you help(?: me)?|who are you|what is nyra|what are your capabilities|"
        r"how do i (?:get started|use (?:this|nyra|this assistant)))"
    ),
}


def conversation_reply(question: str) -> str | None:
    """Recognize simple standalone messages without needing model inference."""
    text = " ".join(re.sub(r"[.!?,;:]+", " ", question.casefold().replace("’", "'")).split())
    for kind, pattern in PATTERNS.items():
        if re.fullmatch(pattern, text):
            return REPLIES[kind]
    return None
