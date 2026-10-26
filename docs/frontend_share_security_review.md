# Security review: conversation sharing

**Scope:**
- Contract 1.11.0 §17: `api/share.py`; the share routes in `api/app.py`; `web/src/shell/SharedView.tsx`; `web/src/contract/share.ts`; the share dialog.
- Reviewed by platform on 2026-10-25 against sota@773854a. Demo backend only. The real backend inherits the API behaviour and must meet the "Real backend" requirements below.
- Data assumption: a conversation can hold PHI in the user's questions, the answer prose, SQL literals and table rows.

## The feature in one paragraph

An owner can create one **org-scoped, read-only** link per conversation: `PUT /api/conversations/{id}/share`, returning `/shared/sh_<144 random bits>`.
- **Who can open it:** any signed-in user of the **same customer** (`GET /api/shared/{shareId}`). There are no public links.
- **Default (`includeData: false`):** table rows are emptied and marked `dataWithheld`; charts and data artifacts (table, chart, report, image) are dropped.
- **With `includeData: true`:** viewers get the stored preview rows (at most 50 per table), but never the CSV, file ids, feedback or the owner's user id.
- **Revoking:** the owner revokes with `DELETE …/share`; deleting the conversation also revokes it.

## Threats and controls

| # | Threat | Control | Evidence |
|---|---|---|---|
| T1 | Someone outside the org opens a link | The lookup is `(shareId, viewer.customer)` with `compare_digest`. Unknown, revoked, deleted and other-org links all return the same **404**, so a link reveals nothing outside the org. | `test_share.py::test_org_scope_revoke_and_delete` |
| T2 | Guessing or enumerating links | `sh_` + `token_urlsafe(18)` gives 144 bits, so there's no enumeration. `_SAFE_ID` rejects malformed ids before any lookup. | `share.py` `put`; `test_bad_body_and_unknown_ids` |
| T3 | Signed-out access | Every share route calls `authed()`, which returns 401. The web app sends the user to sign-in. | `app.py` `get_shared` |
| T4 | Rows leak with the "no data" default | `_part()` empties rows and drops charts and data artifacts. Even with data, the CSV id is stripped, and `/api/download/{id}` is owner-scoped (`downloads.get(ctx.principal, …)`). | `test_share_defaults_to_no_data…`, `test_include_data_shares_preview_rows_but_never_the_csv` |
| T5 | Personal metadata leaks | Feedback (rating and comment), the owner's user id and file ids are never in `SharedConversation`. | `shared_view()` |
| T6 | Writes via CSRF (create a share with data, revoke) | `CsrfMiddleware`: cookie-borne PUT and DELETE need `x-cc-csrf: 1`. | `test_cache_audit.py` ROUTES (403 cases) |
| T7 | The link leaks through logs | The audit log has no share id (`test_audit_has_no_share_id`). Access logs show route shapes only (`/api/shared/:id`, #231). | `test_access_log.py` |
| T8 | The link leaks through Referer or caches | The shared response sends `Referrer-Policy: no-referrer` and `no-store`, and the web CSP sets `no-referrer` app-wide. There is no service worker and no localStorage of shared content. | `test_cache_audit.py`, `noServiceWorker.test.ts` |
| T9 | Hostile content in a shared answer (XSS via prose, SQL or chart spec) | The viewer renders through the same pipeline as the owner: React escaping, no `unsafe-inline` or `unsafe-eval`, Vega on the AST interpreter with a remote-blocked loader, and Trusted Types (#239) so any HTML-sink write throws. Shared views are read-only (no composer, no actions). | `docs/frontend_security.md` |
| T10 | A viewer acts as the owner | `get_shared` reads the owner's conversation with the owner's principal, but returns only the reduced view. The viewer gets no write route, no regenerate, no feedback and no export for it. | `app.py` |
| T11 | Crypto-shredding | If the customer key is destroyed, the shared view returns 410 like the owner's history. | `shredded()` |
| T12 | Who opened what | `shared_conversation_viewed` (viewer, conversation, `kind`); `conversation_shared` / `conversation_share_revoked` (owner, `with_data`/`no_data`). | `app.py` audit calls |

## Findings

| id | severity | finding | recommendation | owner |
|---|---|---|---|---|
| S1 | **Medium** | **Escalating `includeData` keeps the same link.** Sharing again updates `include_data` in place (contract §17: "sharing again returns it"). If the owner first shares without data and later turns data on, everyone already holding the old link silently gains row access. | When `includeData` goes false → true, mint a **new** `shareId` and revoke the old one. Going true → false can keep the id. This needs a contract change (§17 semantics), so it's for the Contract team and Peter. Until then, the share dialog should say "Anyone with the existing link will also see the data." | Contract team / Peter |
| S2 | Medium | **Prose, questions and SQL are always shared.** `includeData: false` withholds rows, but the user's questions ("why was claim AL1234 for J. Doe denied?"), answer prose that quotes values, and SQL literals (`WHERE member_id = '…'`) still go to every org member who has the link. | Make the dialog copy explicit: "Questions, answers and SQL are shared; table rows only if you include data." A real backend could add a server-side PHI scan of the shared transcript (the same redactor as the logs, flag-only) and warn the owner before the link is created. | Shell (copy) / backend |
| S3 | Low | **The share id can end up at the IdP.** A signed-out viewer opening `/shared/sh_…` is sent to `VITE_SIGN_IN_URL?next=/shared/sh_…`, so the capability lands in the IdP's request logs. It's org-scoped and still needs sign-in to use, so the risk is low. | Keep `next` to the route shape (`/shared`) and put the full path in `sessionStorage` under a fixed key, restoring it after sign-in. Platform can do this in `cookieSession` when the real sign-in flow exists. | Platform (with the real IdP) |
| S4 | Low | **No expiry.** Links live until revoked or the conversation is deleted. | Real backend: a default expiry (e.g. 30 days), shown in the dialog, plus revoking shares when the owner is deprovisioned. | Backend / Peter (policy) |
| S5 | Low | **No role check inside the org.** Any signed-in user of the customer can open any link they hold. | Acceptable for "share within the org". If the real backend gets roles (billing vs. analyst), check the viewer's data scope again when `includeData` is true. | Backend |
| S6 | Info | **Viewers can copy or download the preview rows client-side** when `includeData: true` (the table copy and CSV buttons work on the ≤ 50 rows they can already see). The full CSV stays with the owner. | Accepted: the rows are on screen anyway. If policy wants view-only, hide the copy and download buttons in SharedView. | Shell / Peter |
| S7 | Info | **The registry is in memory (demo).** | The real backend must persist shares next to the transcript (same customer key) and revoke them on delete and key destruction, as `share.py` already documents. | Backend |

No critical or high issues. S1 is the one to decide before real data.

## Real backend requirements (inherited checklist)

1. Keep the 404-for-everything rule (unknown, revoked, deleted or other-org) and constant-time customer comparison.
2. Use a share id from a CSPRNG with ≥ 128 bits. Never log it: audit and access logs use route shapes (`install_access_log_redaction`).
3. Store shares under the customer's key. Revoke them on conversation delete, on owner deprovisioning and on key destruction (return 410).
4. `GET /api/shared/{id}` sends `no-store` and `no-referrer`. Keep it in `test_cache_audit.py` ROUTES.
5. Never include the CSV, file or blob ids, feedback or the owner's user id. With `includeData: false`, no rows and no data artifacts.
6. Audit share creation and revocation (owner) and every view (viewer), without the share id.
7. Resolve S1 (rotate on data escalation) before a customer turns sharing on.

## Verified for this review

- `pytest api/tests/test_share.py api/tests/test_access_log.py api/tests/test_cache_audit.py`: green on sota@773854a.
- **Viewer rendering is covered** by the web CSP and Trusted Types checks (`scripts/csp_check.mjs`) and by QA's `/shared` review script (#238).
