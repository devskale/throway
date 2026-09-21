# throway — Releases

**Current version:** `1.18.3`

A disposable file store. Upload a file — or a bundle of files (e.g. a
website) — and get a short-lived URL. No auth. Nothing permanent.

---

## 1.18.3 — 2026-09-21

### Share-Name wählbar (Upload & Create)
- **Share-Name-Feld** in der Web-UI — für Upload und Create. Optional einen
  einprägsamen Namen (z.B. `my-note`) wählen; der Inhalt landet dann unter
  `/d/<name>` (create-or-get, wie ein benannter Dir) statt unter einer
  zufälligen Hex-ID.
- **Backend**: `&share=<name>` für Einzeldatei-Uploads (roh-body und
  multipart). Nutzt die Dir-Mechanik: Sliding-Lifetime (default 7d, `&ttl=`
  clamped [4h,14d]), validiert wie benannte Dirs (5-32 Zeichen `[a-z0-9-]`,
  >=1 Buchstabe, nicht reserviert).
- Docs: `/api`-Spez, `/help/files`, `/help/limits`, `_home_help`, `API.md`,
  `AGENTS.md`, `README.md` um `&share=` ergänzt.

## 1.18.2 — 2026-09-21

### Create-Text hinter „+" neben Upload (statt Tabs)
- Die Tabs „Upload | Create" sind wieder raus — der bisherige Upload-Bereich
  bleibt unverändert. Stattdessen sitzt **neben dem Upload-Button ein kleines
  „+"**, das die Create-Text-Box ein-/ausklappt (Textarea + Name + Lebensdauer
  + Create-Button). Erneutes Klick auf „+" klappt sie wieder zu.

## 1.18.1 — 2026-09-21

### Create-Text + einstellbare Lebensdauer in der Web-UI
- **„Create“-Tab neben „Upload“**: In der Web-UI gibt es jetzt zwei Tabs —
  **Upload** (bisherige Dropzone) und **Create** (neuer). Im Create-Tab lässt
  sich Text direkt eintragen/pasten und per Klick als Datei hochladen
  (optional mit Name, z.B. `note.txt`). Der erzeugte Link ist sofort
  teilbar.
- **Lebensdauer setzbar**: Standard 4h, aber per Dropdown auf bis zu **14
  Tage (max)** verlängerbar — getrennt für Upload und Create. Der Wert wird
  als `&ttl=<h|d>` mitgeschickt.
- **Backend**: `&ttl=<h|d>` funktioniert jetzt auch für **Einzeldatei-**
  Uploads (roh-body und multipart), nicht nur für Dirs. Clamped auf
  `[4h, 14d]` (max 14 Tage), Default 4h. `expires_in`/`persistence`/`X-Expires`
  spiegeln die gewählte Lebensdauer.
- Docs: `/api`-Spez, `/help/files`, `/help/limits`, `_home_help`,
  `API.md` um `ttl=` für Einzeldateien ergänzt.

## 1.18.0 — 2026-09-19

### Agent-Hints überall (Konsistenz-Durchlauf)
- **Hint-Block auf allen Browser-Seiten** (bisher nur Dir-Listing):
  Bundle-Listing, `/d`-Browse, `/browse`-Files, Help-Index, Help-Topic und
  Dir-History bekommen denselben einklappbaren `<details class=agenthint>`-
  Block mit lauffähigen, absoluten curl-Zeilen. Neu als `_agent_hint()`-
  Helper (escaped selbst), CSS wanderte in `_BASE_CSS` (keine Duplikate).
- **`Link: <…/api>; rel="help"` Header** (RFC 8288) auf **allen JSON-
  Responses** (Listings, Upload-Result, Errors — via `_send`) und auf
  Datei-Downloads für Agent-UA (`_serve_file`): jede maschinenlesbare
  Antwort verrät nun, wo der Vertrag liegt.
- AGENTS.md: Selbst-Discovery-Absatz beschreibt die Hint-Blöcke jetzt
  seitenübergreifend inkl. Link-Header.

## 1.17.0 — 2026-09-19

- **Agent-Hint auf der Dir-HTML-Seite**: einklappbarer `<details>`-Block
  unter der Dateiliste mit lauffähigen curl-Zeilen (JSON-Listing via
  `-A curl`, Einzeldatei, `?zip=1`, PUT/PATCH-Edit, `/history`). Absolute
  URLs — jede Zeile überall copy-paste-bar. Für Menschen dezent
  eingeklappt, im Quelltext/Accessibility-Tree immer voll lesbar (kein
  Cloaking: auch für Menschen nützlich). Schließt die Lücke für Agenten,
  die mit Browser-UA auf der HTML-Seite landen; `-A curl`-Agenten bekommen
  weiterhin direkt das JSON-Listing.

## 1.16.0 — 2026-09-19

### Smartphone-UX (Mobile-First-Nachschlag für Browser-Seiten)
- **Viewport + theme-color überall**: alle HTML-Seiten (Dir-Listing, Bundle-Listing,
  Browse, Help, Help-Topic, History, Copy-for-Agents) hatten teils keinen
  Viewport-Meta → auf Smartphones zoome-out-Desktop-Layout. Jetzt durchgängig
  `<meta name=viewport>` + `theme-color` (einheitliche `_META_MOBILE`-Konstante).
- **Shared Base-CSS (`_BASE_CSS`)** für alle Sekundärseiten: Touch-Ziele ≥ 44px
  (Zeilen, Buttons, Back-Links), Dateinamen mit Overflow-Wrap/Ellipsis statt
  Überlauf, responsive Padding via `@media(max-width:560px)`.
- **Homepage mobil**: Upload-Button + Dir-Checkbox stapeln sich untereinander
  (voll breit, 46px), URL-Eingabefeld in der Result-Box mit 16px Font (verhindert
  iOS Auto-Zoom), kompaktere Dropzone, `touch-action:manipulation` auf Buttons.
- **Native Share-Sheet**: nach Upload erscheint auf Smartphones ein
  „⇗ share link"-Button (`navigator.share`) — Link direkt per WhatsApp/Mail teilen.

### Bild-Vorschauen (Thumbnails)
- **`?thumb=1`** auf Datei-URLs (Einzeldatei, Bundle-Datei, Dir-Datei): liefert eine
  kleine WebP-Vorschau (96px, quality 70, konfigurierbar via
  `THROWAWAY_THUMB_PX` / `THROWAWAY_THUMB_QUALITY`).
- **Hardware-schonend**: Thumbnail entsteht lazy beim ersten Request (Pillow,
  EXIF-Rotation beachtet — Handyfotos stehen richtig herum), wird als
  `<file>.thumb` next to the original gecacht (einmal CPU-Kosten, dann nur
  Lesezugriff), atomarer Replace gegen Races; Fallback auf Original-Bytes bei
  SVG/Nicht-Bild/Fehler. `loading=lazy` + `decoding=async`: Phones laden
  Thumbnails erst beim Scrollen und dann KBs statt MBs (1MB-JPEG → ~72B WebP).
- **Listings mit Vorschau**: Dir-, Bundle- und Browse-Listing zeigen für Bilder
  44px-Vorschaukästchen; menschenlesbare Größen (`1.0 MB` statt Bytes).
- **Aufräumen**: `.thumb`-Dateien zählen nie als Dateien in Listings/JSON/Zips,
  werden zusammen mit dem Original gelöscht (`_remove`, Sweep), Uploads mit
  reservierten Suffixen (`.meta/.history/.thumb/.thumbtmp`) neutralisiert
  (`_safe_name` hängt `_` an), direkter Zugriff auf Bookkeeping-Dateien → 404.

### Fixed
- **Zip sauber**: Dir-/Bundle-Zips enthielten bisher `<key>.history` (und hätten
  künftig auch `.thumb`-Caches mitgeliefert) — Zips enthalten jetzt nur die
  echten Nutzerdateien.
- **500er auf Legacy-Dir-Pfad behoben**: `GET /<dir-id>` (Bundle-Namespace, alte
  Dirs) crashte für Browser mit TypeError (`_dir_listing` bekam falsche Argumente).

### API-Notes
- `/api`: `download`, `download_bundle_file` und `get_dir_file` dokumentieren
  jetzt `?thumb=1`. Agents/JSON unverändert (keine Thumb-Einträge).

---

## 1.15.0 — 2026-09-17

- Repo-Versöhnung: die am Live-Dir entwickelten Features (Bundles/Zip/Minisites,
  Tags+browse, Agent-Endpoints, UA-Negotiation) zurück im main-Branch gemergt —
  Live-Stand == Git wieder.
- SEO: HTML-Head für Browser/Googlebot (Title, Description, Canonical, OpenGraph
  + og-image); Agenten erhalten weiter text/plain (UA-Negotiation).

## 1.14.0 — 2026-08-25

### Added
- **Tags on single files** (wie bisher schon bei Dirs): bei Upload und
  URL-Import per `&tag=<t>` (wiederholbar, max 5, `[a-z0-9-]`, 1–24
  Zeichen); stehen danach in der Upload-Response und in der File-Meta.
- **Tag-Update ohne Rewrite**: `POST /<id>?tag=a&tag=b&untag=c` ändert nur
  die Tags — Content und Expiry bleiben unangetastet.
- **`GET /browse`** — Filtern & Sortieren über alle lebenden Einzeldateien:
  `?tag=<t>[&tag=<t2>]` (UND-Filter), `&q=<substr>` (Name/Tag),
  `&sort=created|name|size|expires`, `&order=asc|desc`. JSON für Agents,
  HTML-Seite für Browser.
- Dokumentiert in `/api` (neue Endpoints `browse_files`, `tag_file`;
  Upload-Response mit `tags`) und `/help/files`.

---

## 1.13.0 — 2026-08-25

### Added
- **Import from URL**: `POST /?url=<encoded-url>[&name=<filename>]` — the
  server fetches the remote http(s) document itself and stores it like a
  normal upload (name from Content-Disposition / URL path, overridable via
  `&name=`; content type from response header, fallback via extension).
  Guard rails: max 5 MB, max 3 redirects (re-validated per hop), private/
  loopback/link-local/reserved hosts blocked (SSRF), 10s timeout.
- **URL as document format (`&link=1`)**: `POST
  /?url=<url>&link=1[&name=<name>]` stores the URL itself as a tiny,
  editable HTML redirect page — browsers are redirected via meta refresh,
  agents can `PUT`/`PATCH` it like any text file.
- Both documented in `/api` (new `import_url` endpoint) and `/help/files`.

---

## 1.12.3 — 2026-08-25

### Added
- **Build config**: alle wichtigen Betriebsparameter sind jetzt zentrale
  Konfigurationsvariablen am Dateianfang von `store.py` und per
  `THROWAWAY_*`-Env-Vars überschreibbar (z.B. im systemd-Unit):
  `THROWAWAY_ROOT`, `_POOL_BYTES`, `_MAX_FILE_BYTES`, `_RATE_LIMIT`,
  `_TTL_HOURS`, `_DIR_MIN_AGE`, `_DIR_MAX_AGE` (das 14d-Max),
  `_DIR_DEFAULT_AGE`, `_DIR_ABS_MAX`, `_HISTORY_LIMIT`, `_MAX_TAGS`.
  Defaults unverändert.
- **`/api`**: neues Feld `dir_ttl_seconds: {min, default, max}` — Agents
  können das 14d-Max nun direkt als Daten lesen statt aus Prosa zu raten;
  `create_dir.note` führt das MAX 14 days vorne an.

---

## 1.12.2 — 2026-08-25

### Changed
- **Docs: 14d dir-TTL-Maximum explizit gemacht** — AGENTS.md, API.md und die
  eingebetteten Agent-Texte (`/api`, `/help/limits`, write-for-agents)
  sagen jetzt ausdrücklich "**MAX 14 days**", damit Agents das Limit nicht
  nur aus der Clamp-Angabe erraten müssen.

---

## 1.12.1 — 2026-08-25

### Changed
- **AGENTS.md: Bumppush-Regel geschärft** — per Default ein **Patch-Release**
  (`x.y.z` → `x.y.z+1`); Minor für Features, Major für Breaking Changes.

---

## 1.12.0 — 2026-08-25

### Changed
- **Dirs: `ttl=` max raised from 7d to 14d.** `&ttl=` at dir creation is now
  clamped to `[4h, 14d]` (was `[4h, 7d]`). Default (no `ttl=`) stays 7 days;
  sliding behavior and the 30-day absolute cap are unchanged. Updated in the
  embedded help, `/api` spec, homepage copy, AGENTS.md, API.md, PRD.md,
  README.md.

## 1.11.0 — 2026-08-23

### Added
- **Paste-to-upload**: press **Ctrl+V** (or Cmd+V) anywhere on the homepage
  and any image on the clipboard is queued in the dropzone for upload — no
  need to save it to disk first. A page-wide `paste` listener grabs `file`
  items from the clipboard, wraps them in a named `File` (deriving a
  sensible name + extension from the MIME type, e.g. `pasted-…png`), and
  calls `dz.addFile()`. Text pastes are left untouched. Multiple pasted
  images queue together and upload in the same POST as any dropped files.

---

### Fixed
- **Homepage upload was completely broken** (click AND drop did nothing):
  the inline script had a JS syntax error (a ternary branch's `)` closed
  the outer paren, leaving `:d.bundle?` dangling), so the browser never
  ran any of it. Found via `node --check` on the extracted script.

### Changed
- **Homepage upload UI now uses [Dropzone.js](https://dropzone.dev)
  (5.9.3) via CDN.** Battle-tested click-to-browse + drag-and-drop with
  file previews, progress and per-file remove — replacing the hand-rolled
  dropzone. `maxFilesize` is injected from the server's `MAX_FILE`, so the
  client limit always matches the real one.
- **Homepage JS modularized**: small named functions (`esc`, `row`,
  `filesBlock`, `showResult`, …) instead of one nested string-concat
  expression; upload URL derives from `location.pathname`, so the page
  works under any mount point (root or `/throway` behind the proxy).
- Upload still sends **one multipart POST for the whole queue** → file,
  bundle, or dir (`?dir=1`) exactly as before. Result box unchanged.

### Note
- The homepage now loads Dropzone.js from jsDelivr; if the CDN is
  unreachable the upload card won't function (agents/curl unaffected).

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
