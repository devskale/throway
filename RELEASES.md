# throway — Releases

**Current version:** `1.9.4`

A disposable file store. Upload a file — or a bundle of files (e.g. a
website) — and get a short-lived URL. No auth. Nothing permanent.

---

## 1.9.4 — 2026-08-20

### Fixed
- **Homepage upload dropzone: drag-and-drop uploads now work.** The drop
  handler assigned the read-only `input.files = ev.dataTransfer.files` file
  list directly, which silently becomes an empty `FileList` in some
  browsers, so dropping a file onto the card showed an empty list and the
  upload reported "Choose at least one file". The handler now copies the
  dropped files into a fresh `DataTransfer` and assigns `input.files =
  dt.files`, the standard cross-browser technique (Chrome + Firefox).
  The file-picker path and direct API uploads already worked.
- **Version is now single-sourced from `store.py` `VERSION`.** The
  "Current version" line served on the releases page is derived from the
  code constant at request time, so it can never drift from the running
  build.

---

## 1.9.3 — 2026-08-20

### Fixed
- **Dir files with spaces in their names now fetch correctly.**
  `GET /d/<key>/<file>` (and `PUT`/`PATCH`/`DELETE`) now URL-decode the
  filename segment before matching, so a `%20` in the request matches the
  literal space in the stored name. Previously these returned `404` even
  when the filename was correctly percent-encoded; only space-free names
  worked. Same fix applied to per-file fetches from bundles.
- **Dir/bundle listings now return URL-encoded `files[].url` values**
  (e.g. `%20` instead of a raw space), so the URLs the server hands back
  actually work when fetched by programmatic consumers.

---

## 1.9.1 — 2026-08-18

### Changed
- **Homepage stats: two cards** — each shows files + size. Card 1 is the
  current live state ("now"); card 2 is activity since this server started
  ("since start"), a RAM-only counter reset on each restart. Replaces the
  old four cards (files now / stored / files ever / uploaded ever).

---

## 1.9.0 — 2026-08-18

### Changed
- **Unified the two dir types into one concept.** Mutable dirs (`/<dirid>`,
  sliding 4h/24h) and named dirs (`n/<name>`) are now a single **dir** under
  `/d/<key>`, addressable by an opaque id or a memorable name. One storage
  layout, one TTL model (fixed lifetime, default 7 days, `ttl=` clamped to
  [4h, 7d]), no duplicated code.
- All dir endpoints moved under `/d/`: `POST /d/<key>` (add files),
  `GET /d/<key>` (listing), `GET /d/<key>/<file>`, `PUT`/`PATCH`
  `/d/<key>/<file>` (edit/append), `DELETE /d/<key>[/<file>]`,
  `GET /d` (list `listed=1` dirs).
- **Edit history per dir.** `GET /d/<key>/history` returns the last
  `HISTORY_LIMIT` (50) entries, newest first — date, file, action
  (`add`|`put`|`append`|`delete`) and byte deltas. Lightweight by design:
  no full-text versions, no revert. JSON for agents, HTML for browsers.
- Removed the old `n/` namespace and the short-lived mutable dirs; existing
  real dirs were migrated from `n/<name>` to `d/<name>`.
- Responses carry `editable` (per file) and a `persistence` block (`type`,
  `expires_at`, `extendable_by`, `max_age`) so agents can discover
  editability and lifetime from the response instead of guessing.

---

### Changed
- **Canonical URL is now `https://skale.dev/throway`.** The front door moved
  to amd2 (`158.180.42.218`), which proxies `/throway/` to lubu and
  terminates TLS. `skale.dev` and `www.skale.dev` both point there; the root
  `/` redirects to `/throway/`.
- `PUBLIC_BASE` default is `https://skale.dev/throway`; all generated URLs
  (upload responses, dir/named-dir listings) use it. The old
  `https://lubu.skale.dev/throway` still works via the env override on lubu.
- Docs (AGENTS/API/README/PRD) updated to the new canonical URL.

---

## 1.6.1 — 2026-08-12

### Changed
- **Homepage now returns a structured `--help` summary to agents.** Curling
  the root (`GET /throway/` as a non-browser client) returns a compact usage
  overview plus pointers to where to get more — the full guide
  (`/write_for_agents`), the API index (`/api`), and per-topic help
  (`/help`, `/help/<topic>`). ~1 KB instead of the full ~5 KB blob.

---

## 1.6.0 — 2026-08-12

### Added
- **Modular, API-gatherable help** (`/help`). Instead of one giant
  copy-paste blob, help is split into topics served individually at
  `/help/<topic>` (`overview`, `files`, `bundles`, `dirs`, `named_dirs`,
  `view`, `edit`, `delete`, `limits`, `contract`).
  - `GET /help` → JSON index of topics (for agents) or an HTML list (browsers).
  - `GET /help/<topic>` → that topic as plain text (agents) or a rendered
    page (browsers). Unknown topics → 404.
  - Agents fetch only the pieces they need instead of one big blob.
- **Single source of truth**: the `/write_for_agents` description and the
  `/help/*` topics are assembled from the same `HELP` dict — no duplication.

---

## 1.5.0 — 2026-08-12

### Added
- **Named dirs** (`/n/<name>`). A mutable dir addressed by a memorable name
  instead of an opaque hex id, so a team of agents can remember and reuse one
  shared dir.
  - **Create-or-get**: `POST /?dir=1&name=<name>` is idempotent — any agent
    calling the same create converges on the shared dir.
  - **Naming ruling**: 5-32 chars, `[a-z0-9-]`, must contain a letter, no
    reserved words (`api`, `index`, `n`, `releases`, `llms`, `store`, …).
  - **Create flags** (immutable at create): `&listed=1` (appears in `GET /n`),
    `&tag=<t>` (up to 5 discoverability tags), `&ttl=<h|d>` (fixed lifetime).
  - **Fixed lifetime**: `expires_at` set at creation (default 7 days, `ttl=`
    overrides, clamped to [4h, 7d]) and **never moves** — add/edit/delete do
    not extend it.
  - **`updated_at`** tracks last add/edit/delete (does not affect lifetime).
  - **Full CRUD** on `n/<name>`: add files, fetch, zip, PUT/PATCH text edit,
    delete file or whole dir.
  - **Listing `GET /n`** (only `listed=1` dirs): filter by `?q=` (name/tag
    substring), `?created_after/before`, `?updated_after/before`; sort by
    `?sort=created|updated|name&order=asc|desc`. JSON for agents, HTML for
    browsers.
  - **Privacy**: unlisted by default; names never enumerated outside `GET /n`.

---

## 1.4.2 — 2026-08-12

### Fixed
- **Dir/bundle listing 404s in a browser.** The HTML listing pages for dirs
  and bundles (served at `/throway/<id>` with no trailing slash) now inject a
  `<base href="/throway/<id>/">` tag, so relative links (`a.txt`, `b.txt`)
  resolve against the dir/bundle directory instead of the parent path.
  Previously clicking any item in a dir or bundle listing 404'd.

### Changed
- `PUBLIC_BASE` is now overridable via the `THROWAWAY_PUBLIC_BASE` env var
  (default `https://skale.dev/throway`), making it easy to switch the public
  URL without editing code.

---

## 1.4.1 — 2026-08-12

### Changed
- Polished the web UI across all pages: a clean white design with drag &
  drop upload, one-click URL copy, a "create a dir" toggle, feature cards,
  and a stats grid on the landing page. Releases and bundle/dir listing
  pages match the same minimal white look.

---

## 1.4.0 — 2026-08-12

### Added
- **Mutable dirs.** Create a dir (`POST /?dir=1`), keep adding files to it
  (`POST /<dirid>`), and it's deleted **4h after the latest upload** (capped
  at 24h total). `GET /<dirid>` returns a JSON listing to agents / an HTML
  page to browsers; `?zip=1` downloads the whole dir; files are fetched and
  deleted individually.

### Fixed
- **Bundle sub-resource 404s in a browser.** A bundle's `index.html` is now
  served with an injected `<base href="/throway/<id>/">` tag, so relative
  URLs (`style.css`, `app.js`, images) resolve against the bundle directory
  instead of the parent path. Previously every multi-file bundle rendered
  unstyled/broken in a browser.

---

## 1.3.0 — 2026-08-12

### Fixed
- **Per-IP rate limiting.** The rate limiter now keys on the real client IP
  (via `X-Forwarded-For` / `X-Real-IP` set by nginx) instead of the socket
  peer, which behind nginx was always `127.0.0.1`. Previously the whole
  service shared one 100 req/min bucket — a single busy agent could exhaust
  it for everyone. Each real IP now gets its own bucket.
- **Memory-safe bundle downloads.** The bundle zip is streamed to a temp file
  and out in chunks instead of being built entirely in RAM. A bundle near the
  pool limit no longer allocates ~100 MB of memory per request.
- **Total bundle size cap.** A bundle can no longer exceed the 100 MB pool —
  returns `413` during upload instead of briefly blowing past the limit.

## 1.2.0 — 2026-08-12

### Fixed
- **Bundle MIME sniffing.** Bundle parts now get their content type from the
  file extension (like single-file uploads), so `.css`, `.js`, `.svg`, etc.
  serve correct MIME types instead of `application/octet-stream`. Browsers no
  longer block stylesheets/scripts in styled bundles.
- **HEAD requests.** `HEAD /throway/<id>` (and other routes) now return `200`
  with correct headers and `Content-Length` and no body, instead of `501`.

## 1.1.0 — 2026-08-12

### Added
- **Bundles.** Upload 2+ files in one multipart `POST` to create a bundle under
  a single URL. Browsers get `index.html` rendered inline (a real throwaway
  website); agents get a zip; each file is served at `/throway/<id>/<file>`.
  The whole bundle shares one 4-hour expiry and is evicted as one unit.
- **Inline rendering for text-like types.** Images plus text, HTML, JSON, PDF,
  SVG, and JavaScript now render inline in the browser; `?download=1` forces a
  download. Text responses include `charset=utf-8`.

### Fixed
- **Base URL.** Dropped the dead `:8001` port; the service is now reached at
  `https://lubu.skale.dev/throway`.

## 1.0.0 — 2026-08-10

### Added
- Initial release. A disposable, open, short-lived file store.
- Upload a single file (raw body or multipart) → 4-hour URL.
- Images render inline; other files download; `?download=1` forces download.
- Text files editable via `PUT` (replace) and `PATCH` (append); images immutable.
- Rolling 100 MB pool (oldest evicted first), 5 MB max file, 100 req/min per IP.
- Machine-readable contract at `/api` and agent descriptions at
  `/write_for_agents` and `/copy_for_agents`.
