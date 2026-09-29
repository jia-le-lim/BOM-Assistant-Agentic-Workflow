---
name: propose
description: Stage explicit engineer-supplied stocking values for an item, retaining human confirmation.
---

# Propose a stocking change

Accept an item and at least one explicitly named integer quantity: max, rop,
or min. Optional stockroom and reason belong to this request. Do not infer a
quantity from an item ID, workspace, historical message, or recommendation.

Use only `propose_change` with the parsed fields. Report the actual returned
pending proposal ID and only the quantities supplied. Say the proposal is
staged and still needs confirmation; it is not a recorded review or approval.
Do not call review confirmation, rule-writing, or export tools.
When validation fails, show the error and preserve the user's original request.
