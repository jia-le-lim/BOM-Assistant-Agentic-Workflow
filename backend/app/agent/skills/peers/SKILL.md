---
name: peers
description: Retrieve comparable historical parts and explain the limits of their advisory evidence.
---

# Compare peer evidence

Read `get_similar_parts` for the selected workspace, item, and optional
stockroom. Preserve the review-role permission requirement. These are other
parts' historical records, not the selected item's own history.

Summarize returned similarity reasons, peer decisions, neighbour count, and
stored confidence. Use the stored aggregate statistics rather than recomputing
them from the limited neighbour list. State evidence limitations explicitly.
An analogue median is not a replacement stocking recommendation. Do not
automatically run similarity computation or stage a change when data is absent.
