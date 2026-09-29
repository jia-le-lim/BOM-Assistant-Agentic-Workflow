---
name: triage
description: Rank items still awaiting review by recorded USD exposure and explain their flags.
---

# Triage the queue

Use `top_exposure` for the selected workspace and requested count (default 10,
maximum 25). Its filter runs before its limit and excludes reviewed,
auto-cleared, and awaiting-senior rows.

Return a ranked table with item ID, stockroom, recorded exposure, risk, and
reason code. Explain why these records deserve attention without calculating
replacement stocking levels. State the limit so the shortlist is not mistaken
for the entire queue. Report an empty queue plainly. Do not approve anything.
