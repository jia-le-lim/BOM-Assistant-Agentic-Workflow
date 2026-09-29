---
name: explain
description: Explain an item's stored recommendation using current levels, reason codes, and procurement evidence.
---

# Explain a recommendation

Use the selected workspace and specified item. When the item exists in several
stockrooms, request an explicit stockroom rather than choosing one.
Read `get_current_values`, `get_recommendation`, and `get_procurement_context`.

Lead with the main finding. Show current versus engine-proposed Min, ROP, and
Max in a table, then the stored reason codes and explanation. Include lead
time and criticality only when recorded. State any missing evidence.
Never calculate new stocking quantities or stage a change through this skill.
