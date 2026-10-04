# 🤖 AGENTS.md — Guide for Agents & Programs

This file is written for **agents, bots, and scripts** that want to use
throway. It carries the **semantics** (rules that are not obvious from
any single response) plus **pointers** — the full reference lives on the
live server, always current (PRIO 1). No API examples are cached here on
purpose: docs-drift incidents have proven the copy wrong twice.

---

## TL;DR

```
Base URL:  https://skale.dev/throway
Lifetime:  4 hours (files auto-delete); dirs 7d sliding; retained = never
Auth:      none (public). Optional retain token for indefinite objects.
```

> **CODING_RULES.md lesen vor jeder Code-Änderung** — Release-Checkliste,
> Deploy-Ritual (rsync-Falle), Test-Disziplin (`${PIPESTATUS[0]}`!),
> Security-Invarianten, Edit-Disziplin.

> **CONTEXT.md lesen vor Modul-Arbeit** — Architektur-Begriffe + Modulkarte:
> wo gehört was hin (Namespaces, Request-Kit, storage-Mechanik, Homepage).

> **Bumppush-Pflicht:** Jede Änderung = eigenes Release (Bugfix → Patch,
> Feature → Minor, Breaking → Major; unsicher → fragen): `VERSION` hoch,
> Release-Note in `RELEASES.md`, Commit `x.y.z: …`, push, Deploy-Ritual
> (`store.py` + `throway/` — **kein trailing slash auf throway/**), dann
> `sudo systemctl restart throway-store`.

> **Koordination (Pflicht vor jedem Bumppush):** Claim in
> `.handoff/issues/active/` anlegen; `release-check.sh` verweigert bei
> fremdem Claim.

1. `POST` a file → get back JSON with `id` and `url`. Share the `url`.
2. `GET` download · `PUT`/`PATCH` edit text · `DELETE` remove.
3. 2+ multipart parts in one POST → a **bundle** under one URL.
4. `POST /?dir=1[&name=]` → a **dir** (workspace: add/edit/delete over
   time); multipart parts on create = initial files, on an existing
   named dir = **add** (retry-safe).

---

## ⭐ PRIO 1 — Self-service: discover, don't assume

The service is designed so an agent needs **nothing pre-loaded**. Never
hardcode the contract — pull it from the live server:

```
curl the HTML pages          # homepage + dir listing + help carry the guide
curl /throway/api            # THE spec: limits + every endpoint (JSON)
curl /throway/help           # topic index
curl /throway/help/<topic>   # overview files bundles dirs markdown view
                             # edit delete limits contract errors retention pics
```

Every JSON response and file download carries
`Link: <…/api>; rel="help"`. Browser pages carry a collapsed **“agent
hint”** block with ready-to-run curl lines.

**Feature discovery from responses:** every upload/listing tells you per
file `editable` (`text/*` + `application/json` → PUT/PATCH work; binaries
→ 400) and `persistence` (`type` single|dir|bundle, `expires_at`,
`extendable_by` none|activity, `max_age`). Trust those over any doc.

**Errors:** every error JSON has a stable `code` (400 `bad_request`,
401 `write_denied`, 404 `not_found`, 413 `too_large`, 429 `rate_limited`,
507 `pool_full`, …). Only **429/507** mean „later again" (they carry
`Retry-After`); every other code means the request itself is wrong.
Full table: `GET /help/errors`.

---

## Semantics (the non-obvious rules)

- **The store is ephemeral and shared.** Anyone with a URL can read,
  edit or delete that object. Never put secrets in it.
- **IDs are opaque.** Never parse meaning into an id.
- **Use `?name=`** on uploads so content type + download filename are
  right (especially images).
- **Lifetime:** files/bundles default 4h, `ttl=` clamped [4h, 14d].
  Dirs: sliding 7d default (every add/edit/delete slides `expires_at`
  forward, capped 14d per slide / max age overall). `once=1` =
  burn-after-reading (single files only — not with `&share=`, not with
  retention).
- **Dirs are create-or-get** (named: 5–32 chars `[a-z0-9-]`, ≥1 letter,
  not reserved). Create flags (`listed=`, `tag=`, `ttl=`, `write=`,
  `show=`) are honored **only on first creation**; re-calling create on
  an existing name returns it — multipart parts are **added** (since
  1.47.0, write gates apply).
- **Optional write protection** (`&write=1`): token shown exactly once;
  writes then need `X-Throway-Write` header or `?write=`; reads stay
  open. Whole-dir **delete always** needs the dir's token (or retain
  token) — even on open dirs.
- **`?json=1` / `?html=1`** force the listing representation on
  `/d/<key>`, `/d`, history, browse — against the UA heuristic (since
  1.47.0).
- **Unlisted by default.** Only dirs created with `listed=1` appear in
  `GET /d`; names are never enumerated otherwise.
- **Listing `GET /d`:** filters `?q=`, `?created_after/before=`,
  `?updated_after/before=`, sort `?sort=created|updated|name&order=`.

## Retention — indefinite objects (token-gated, seit 1.44.0)

Server-side token (`THROWAWAY_RETAIN_TOKEN`, comma-list for several).
Token-authenticated requests create/flip objects that **never expire**
and are **exempt from pool eviction** — public read, token-gated write
(`Authorization: Bearer <token>` or `?token=`). Details, Beispiele und
Show-Dirs (indefinite + public write, seit 1.45.0):
**`GET /help/retention`**.

Kernregeln: `?retain=1` ohne Token → 401 · Token-Upload und Token-Write
implizieren Retention („write implies retention") · `once=1` +
Retention → 400 · Flip-Routen `POST /<id>?retain=1` /
`POST /d/<key>?retain=1` (idempotent) · reservierte Namespaces
(`d`, `pics`) flippen nicht.

## Pics — event galleries (seit 1.20.0)

Beliebig viele Bildgalerien unter `/pics`: create-or-get, Admin per
per-Galerie-Token (genau einmal gezeigt), eigenes 20-GB-Budget, 90 Tage
pro Bild, eigene Limits (30 MB/Bild). Bilder ≤ 2048px byte-identisch
(Metadaten/GPS lossless entfernt), größere → 2048px WebP q90. Remote-
Import via `?url=`. Pool voll → 507, nie Eviction. Details:
**`GET /help/pics`**.

## Error codes

Siehe **`GET /help/errors`** (Code-Tabelle + Retry-Strategie, seit
1.47.0). Kurz: nur 429 (`rate_limited`, `Retry-After`) und 507
(`pool_full`) sind „später nochmal" — alles andere heißt Request falsch.

---

## Self-check before acting

1. `GET /api` — limits + endpoints, jetzt und hier.
2. `GET /help/<topic>` — das Detail, das du gerade brauchst.
3. Response lesen: `editable` + `persistence` entscheiden, nicht raten.
4. Fehler: `code` lesen — nur 429/507 sind ein „später".
