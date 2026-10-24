<!-- STATUS: REFERENCE - memory unrelated to CC | source: claude.ai memory store, account-level files unrelated to CC (NBA, payer-policy benchmark, React library), as stored on 2026-10-24 -->
## /areas/next-best-action-model.md  (account-level; updated 2026-10-13T21:59:59Z)

---
name: next-best-action-model
description: User's team's Next Best Action (NBA) model for claim lines and their plan to evolve it into an agentic workflow (Memory, Strategy, Policy agents)
sources: [chat]
aliases: [NBA, NBA model, Next Best Action, next best action engine, agentic NBA]
---

- [stated] wants to evolve the team's current Next Best Action (NBA) model (recommends the next action on claim lines; today built on historical outcome-based predictions) into an agentic AI workflow, reusing existing agentic capabilities (Memory, Strategy and Policy agents) rather than building from scratch (Oct 2026)

## /areas/payer-policy-agent-benchmark.md  (account-level; updated 2026-10-21T21:37:26Z)

---
name: payer-policy-agent-benchmark
description: User's benchmark/task where agents take a CPT code + payer website (UHC, Humana) and find the applicable coverage policy
sources: [chat]
aliases: [CPT policy benchmark, payer policy lookup, policy URL benchmark]
---

- [stated] has a task/benchmark where agents are given a CPT code and a payer website and must navigate to the applicable coverage policy and return its URL (payers so far: UnitedHealthcare commercial, Humana Medicare)
- [stated] problem: some payers (e.g. Humana) serve the policy as a PDF download with no reachable URL; wants to adjust the benchmark to accept a non-URL success signal that is text/bytes and not LLM-generated

## /areas/shared-react-component-library.md  (account-level; updated 2026-10-12T20:18:54Z)

---
name: shared-react-component-library
description: User's upcoming work task building a shared React component library based on their company's themes/styles
sources: [chat]
aliases: [component library, React component library, design system]
---

- [stated] will have to build a shared React component library based on company themes/styles at work; has not done this before
