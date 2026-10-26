# Frontend feature-parity checklist vs ChatGPT and Claude

QA, 2026-10-25 (PETER DIRECTIVE: "every edge case, every feature"). This covers behaviour as seen from the outside only. We did not copy any source code, CSS or icons.

**Status key.**

| Status | Meaning |
|---|---|
| ✅ | Shipped and tested |
| 🟡 | Partial |
| ❌ | Missing |
| ⛔ | Intentionally out of scope (PHI, demo mode or product fit). The reason is given. |

Each row names the owning team and a priority (P1–P3). A ❌ or 🟡 at P1–P3 is filed in `backlog/<team>.md` under "QA findings … (cycle C9 parity)".

## Conversation & history
| Feature | ChatGPT | Claude | Us | Owner / pri |
|---|---|---|---|---|
| New chat (button and shortcut) | ✓ | ✓ | ✅ Ctrl+Shift+O | — |
| Sidebar date groups (Today / Yesterday / Previous 7 days) | ✓ | ✓ | ✅ | — |
| Rename chat (inline) | ✓ | ✓ | ✅ | — |
| Delete with Undo toast | ✓ | ✓ | ✅ | — |
| Pin / star chats | ✓ (pin) | ✓ (star) | ✅ Pinned group | — |
| Archive chats | ✓ | — | ❌ | shell P3 (QA-S20) |
| Projects / folders | ✓ | ✓ | ❌ | ⛔ for this sprint (needs a backend model). Noted. |
| Full-text chat search | ✓ | ✓ | ✅ Ctrl+K | — |
| Multi-tab consistency (a chat created, renamed or deleted in tab A shows up in tab B) | ✓ (on focus/refresh) | ✓ | ✅ verified C9: chats created in tab A appear in tab B on focus; a chat deleted in A fails gracefully in B (e2e/chaos.spec.ts multi-tab) | — (QA-S21 not needed) |
| Draft survives a reload | ✓ | ✓ | 🟡 per-chat drafts are in memory only | ⛔ PHI: no localStorage of question text. Keep as is; document. |
| Temporary / incognito chat | ✓ | ✓ (incognito) | ❌ | shell P3 (QA-S22); could be "don't save" in demo |
| Share read-only link | ✓ | ✓ | ✅ /shared/:id (#229) | S18 open (SQL on data-off) |
| Export / print | — (export via settings) | — | ✅ .md export, print answer | — |

## Composer
| Feature | ChatGPT | Claude | Us | Owner / pri |
|---|---|---|---|---|
| Enter sends, Shift+Enter adds a newline, and IME composition is respected | ✓ | ✓ | ✅ (IME tested) | — |
| Auto-grow with a max height and internal scroll | ✓ | ✓ | ✅ | — |
| Stop generating | ✓ | ✓ | ✅ | — |
| Attach / paste / drag-drop files | ✓ | ✓ | ✅ (chips) | — |
| Paste a large text block as an attachment chip | — | ✓ | ✅ | — |
| Slash / tool menu | ✓ (/ tools) | ✓ | ✅ slash.ts | — |
| Model / mode picker | ✓ | ✓ | removed (1.24.0: one server configuration) | — |
| Voice dictation | ✓ | ✓ (mobile) | ❌ | ⛔ PHI: browser speech engines send audio to a vendor. Do not ship. |
| Focus is kept after send (every engine) | ✓ | ✓ | 🟡 **Firefox loses focus to BODY after the first send** | shell **P2 QA-S19** |

## Answer rendering
| Feature | ChatGPT | Claude | Us | Owner / pri |
|---|---|---|---|---|
| Markdown, code highlight, Copy code | ✓ | ✓ | ✅ | — |
| Math (KaTeX) | ✓ | ✓ | ✅ | — |
| RTL / mixed-direction text (dir=auto) | ✓ | ✓ | ✅ Markdown dir=auto; whole-app `dir=rtl` verified in C9 chaos (no overflow) | — |
| Interactive tables (sort, resize, CSV) | ✓ (data analysis) | artifacts | ✅ ahead | — |
| Charts with hover values and download | ✓ | artifacts | ✅ | — |
| Side panel for artifacts / reports | canvas | artifacts | 🟡 1.9 report panel landing | answer (review on merge) |
| Inline source citations | ✓ | ✓ | 🟡 1.9 citations landing | answer (review on merge) |
| Thinking / tool-step disclosure | ✓ | ✓ | ✅ (A22 name concatenation) | answer P3 A22 |
| Clarifying-question UI | prose | prose | ✅ ahead (card) | — |
| Mermaid / diagrams | ✓ | ✓ | ❌ | ⛔ no diagram output in the contract |

## Message actions
| Feature | ChatGPT | Claude | Us | Owner / pri |
|---|---|---|---|---|
| Copy response / Copy as Markdown | ✓ | ✓ | ✅ + Ctrl+Shift+C | — |
| Regenerate and "try again with style" | ✓ | ✓ (retry) | ✅ (#216) | — |
| Edit the user message and keep versions | ✓ | ✓ | ✅ (A21 focus) | answer **P2 A21** |
| Thumbs feedback with an optional reason | ✓ | ✓ | ✅ | — |
| Hide actions on error turns | ✓ | ✓ | 🟡 | answer P3 A22 |
| Read aloud | ✓ | — | ❌ | answer P3 (QA-A23). `speechSynthesis` is on-device on most OSes, but voices can be network-backed. Ship only as opt-in. |
| Message timestamp on hover | — | ✓ (tooltip) | ❌ | shell P3 (QA-S23) |

## Navigation, a11y, resilience
| Feature | ChatGPT | Claude | Us | Owner / pri |
|---|---|---|---|---|
| Scroll-to-latest button and no auto-scroll hijack | ✓ | ✓ | ✅ | — |
| Shortcut sheet (Ctrl+/) | ✓ | — | ✅ | — |
| Offline banner and resume | ✓ | ✓ | ✅ | — |
| Reconnect mid-stream / resume after reload | ✓ (resumes) | ✓ | ✅ transport tests + C9 chaos reload at 7 stages | — |
| Quiet screen-reader announcements | ✓ | ✓ | 🟡 | shell **P2 S16** |
| Zoom 200% / 320 CSS px reflow (WCAG 1.4.10) | ✓ | ✓ | ✅ verified C9 (200%/400%/320px, no horizontal scroll) | — |
| Settings radios as a single Tab stop | ✓ | ✓ | 🟡 | shell P3 S17 |
| Cross-browser (Firefox, WebKit, iOS Safari) | ✓ | ✓ | 🟡 xb pass in progress | per finding |
| Personalisation / custom instructions / memory | ✓ | ✓ | ❌ | ⛔ PHI and product fit: analyst tool, no per-user memory |
