---
name: preferences
description: How the user wants Claude to work in this project — keep the ClaimClarity current-state memory live
sources: [chat]
---

- [stated] treat the "Current state" section of [[implementations-neo4j-chatbot]] (ClaimClarity) as living: whenever a conversation changes the architecture, status, ADRs, data loads or open items, update, revert or edit that section to match (and bump its "as of" date) rather than appending history
