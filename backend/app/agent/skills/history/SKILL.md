---
name: history
description: Retrieve an item's recorded decisions and notes across review cycles.
---

# Review history

Read `get_item_history` and `get_item_notes` for the specified item. These tools
deliberately span workspaces; distinguish every record's batch and stockroom
when returned. Do not present another cycle's decision as the current state.

Summarize recorded decisions, dates, quantities, and justifications. Separate
engineer notes from recorded decisions. Describe changes only when the records
support them; absence of a comment does not reveal the reviewer's motivation.
If history is empty, say no history was found. Do not create notes or decisions.
