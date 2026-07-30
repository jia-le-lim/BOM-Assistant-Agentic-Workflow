"""Canonical engineer justification templates and their UI definitions."""

JUSTIFICATION_TEMPLATES = (
    {
        "justification": "Follow SFM",
        "definition": "Select if you are adopting the SFM/BRR recommendation.",
    },
    {
        "justification": "Ad-hoc consumption/Bulk withdraw",
        "definition": (
            "Select if the algorithm decision is due to non-linear historical "
            "consumption."
        ),
    },
    {
        "justification": "Parts for PG Lab/SIMS/DSDP",
        "definition": "Select if the algorithm decision is due to sharing parts with ATM.",
    },
    {
        "justification": "EOL/obsoleted",
        "definition": (
            "Select if the algorithm decision is due to an EOL part that is not yet "
            "tagged in WINGS, for deactivation and/or scrap."
        ),
    },
    {
        "justification": "Budget/DOI control",
        "definition": "Select if the algorithm decision is due to a budget constraint.",
    },
    {
        "justification": "Quality issue",
        "definition": "Select if part consumption increased due to a quality issue.",
    },
    {
        "justification": "Constraint tool",
        "definition": (
            "Select if the algorithm decision is due to a constrained tool or a single "
            "tool at the site."
        ),
    },
    {
        "justification": "Volume/tool decrease",
        "definition": "Select if the algorithm decision is due to a future volume/tool decrease.",
    },
    {
        "justification": "Volume/tool increase",
        "definition": "Select if the algorithm decision is due to a future volume/tool increase.",
    },
    {
        "justification": "Flat Future Consumption",
        "definition": (
            "Select if the algorithm decision is consistent with historical consumption."
        ),
    },
)
