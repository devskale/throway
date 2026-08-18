# 🤖 AGENTS.md — Guide for Agents & Programs

This file is written for **agents, bots, and scripts** that want to use throway.
It tells you exactly how to upload, share, and manage files — and how to make
your agent discover the API by itself.

---

## TL;DR

```
Base URL:  https://skale.dev/throway
Lifetime:  4 hours (files auto-delete)
Auth:      none
```

1. `POST` a file → get back JSON with an `id` and `url`.
2. Share that `url`. It's valid for 4 hours.
3. `GET` to download, `PUT`/`PATCH` to edit text, `DELETE` to remove.
4. `POST` 2+ files in one multipart body → a **bundle** (e.g. a website)
   under one URL, served at `/throway/<id>/<filename>`.

---

## ⭐ PRIO 1 — Self-service: pull everything from the HTML pages, then `/api` + `/help`

**This is the core principle of throway.** The service is designed so an
agent needs **nothing pre-loaded** to use it — it can discover everything
itself, at runtime, from the live server. Never assume you know the API;
never hardcode endpoints. Instead:

1. **`curl` the HTML pages** — the human-facing pages (homepage, dir
   listing, help) are the primary source of what throway can do. They carry
   the full usage guide, so pulling them gives you everything needed to use
   the service.
2. **Then confirm / go deeper via the machine-readable contract:**
   - `GET /api` — the authoritative JSON spec: current limits + every
     endpoint (method, URL, body, response shape).
   - `GET /help` + `GET /help/<topic>` — modular plain-text topics
     (`overview`, `files`, `bundles`, `dirs`, `view`, `edit`, `delete`,
     `limits`, `contract`) so you pull only what you need.

So the recommended flow for any agent:

```
curl the HTML pages  →  learn the surface  →  GET /api for exact endpoints
                     →  GET /help/<topic> for details  →  act
```

If you can reach the site, you can use the site — no setup, no prior
knowledge, no hardcoded contract.

---

## Self-discovery — how an agent learns the API

**Don't hardcode the contract. Discover it from the live server** — the
HTML pages first, then the machine-readable spec to confirm exact details.

**Start by curling the HTML pages** — they carry the full usage guide and are
the primary way an agent learns what throway can do (see PRIO 1 above):

```
GET /throway/                  # homepage: upload UI + embedded Agent info
GET /throway/d/<key>           # a dir listing (HTML for browsers, JSON for agents)
GET /throway/help              # modular help index (JSON for agents, HTML for browsers)
GET /throway/help/<topic>      # one help topic (plain text for agents)
```

**Then confirm exact endpoints via the machine-readable contract:**

```
GET /throway/api               # THE API SPEC — limits + all endpoints (JSON)
GET /throway/write_for_agents  # full plain-text usage guide
```

`/api` tells you everything an agent needs to know about what this service
can do, right now: the current limits (`ttl_seconds`, `max_file_bytes`,
`pool_bytes`, `rate_limit_per_min`) and **every endpoint** (`upload`,
`upload_bundle`, `create_dir`, `edit_text`,
`append_text`, `delete`, …) with its method, URL, body, and response shape.
Because it's generated from the running server, it never drifts from what's
actually implemented. **If it's not in `/api`, it doesn't exist.**

**Curling the homepage** (`GET /throway/` as a non-browser client) returns a
compact, structured `--help` style summary: the essential usage commands plus
pointers to the full guide and the API index.

### Feature discovery — what can this API do?

Every response and the `/api` spec carry two fields that tell an agent what
it can do with a resource, so it never has to guess:

- **`editable`** (boolean, per file) — can you `PUT`/`PATCH` it? `true` for
  `text/*` and `application/json`; `false` for images/binaries.
- **`persistence`** (object) — how long it lives and how to keep it alive:
  `type` (`single`|`dir`|`bundle`), `expires_at`, `extendable_by`
  (`none`), `max_age`.

So the flow for any agent is simply: **`GET /api` → see what endpoints exist
→ upload → read `editable`/`persistence` from the response → act accordingly.**

### Modular help — gather only what you need

Help is split into **topics** you can fetch individually:

```
GET https://skale.dev/throway/help          # JSON index of topics (agents)
GET https://skale.dev/throway/help/<topic>  # one topic as plain text
```

Topics: `overview`, `files`, `bundles`, `dirs`, `view`,
`edit`, `delete`, `limits`, `contract`. Fetch the index, pick the topics you
need, and pull only those — no need to load the whole description.

---

## Upload a file

### Raw body (simplest)
```bash
curl -X POST --data-binary @photo.png \
  "https://skale.dev/throway/?name=photo.png"
```

### Multipart form
```bash
curl -F "file=@photo.png" "https://skale.dev/throway/"
```

### Response (JSON)
```json
{
  "id": "4f2a1c9d0e3b8a77",
  "url": "https://skale.dev/throway/4f2a1c9d0e3b8a77",
  "size": 148,
  "name": "photo.png",
  "content_type": "image/png",
  "editable": false,
  "persistence": {
    "type": "single",
    "expires_at": "2026-08-10T14:57:09Z",
    "extendable_by": "none",
    "max_age": null
  },
  "expires_in": 14400,
  "expires_at": "2026-08-10T14:57:09Z"
}
```

- The **`id`** is the file's document ID — use it in the path of every other call.
- The **`url`** is what you share. Valid until `expires_at`.
- **`editable`** — `true` for `text/*` and `application/json` (PUT/PATCH work); `false` for images and binaries.
- **`persistence`** — how long it lives (`type`), and how to keep it alive (`extendable_by`).

---

## Upload a bundle (multiple files)

`POST` 2+ file parts in a single multipart body to create a **bundle** — one
URL that holds several files (e.g. an `index.html` + `style.css` website).

```bash
curl -F "f=@index.html;type=text/html" \
     -F "f=@style.css;type=text/css" \
     "https://skale.dev/throway/"
```

### Response (JSON)
```json
{
  "id": "9c0f2b8a1d4e6f03",
  "url": "https://skale.dev/throway/9c0f2b8a1d4e6f03",
  "bundle": true,
  "editable": false,
  "persistence": {
    "type": "bundle",
    "expires_at": "2026-08-12T12:19:14Z",
    "extendable_by": "none",
    "max_age": null
  },
  "files": [
    {"name": "index.html", "url": "https://skale.dev/throway/9c0f2b8a1d4e6f03/index.html", "size": 202, "content_type": "text/html", "editable": true},
    {"name": "style.css",  "url": "https://skale.dev/throway/9c0f2b8a1d4e6f03/style.css",  "size": 75,  "content_type": "text/css", "editable": true}
  ],
  "size": 277,
  "expires_in": 14400,
  "expires_at": "2026-08-12T12:19:14Z"
}
```

- **Bundle root** `GET /throway/<id>` serves `index.html` inline to browsers
  (a real mini-website), or the whole bundle as a **zip** to agents/curl.
- **Each file** is reachable at `GET /throway/<id>/<filename>` (inline for
  text/images, download otherwise). Relative links between files just work.
- **`?download=1`** forces the whole bundle as a zip.
- If a bundle has no `index.html`, browsers get a simple file listing instead.
- The whole bundle shares one 4-hour expiry and is evicted as one unit.
- A **bundle itself is immutable** (`editable:false`); individual `text/*` or
  `application/json` files within it are editable (`editable:true` in `files[]`).

---

## Dirs (one unified concept, under /d/<key>)

A **dir** is a collection of files you keep adding to and editing over time
— a disposable workspace for an agent. One concept, addressable by an
**opaque id** (unnamed) or a **memorable name** (named), always under
`/d/<key>`. It has a **fixed lifetime** (default 7 days) and a lightweight
**edit history**.

```bash
# create an unnamed dir (opaque hex id)
curl -X POST "https://skale.dev/throway/?dir=1"
# -> {"id":"…","url":"…/d/<id>","dir":true,"files":[],…}

# create a named dir (create-or-get, idempotent)
curl -X POST "https://skale.dev/throway/?dir=1&name=team7"
# -> {"id":"team7","name":"team7","url":"…/d/team7","dir":true,…}

BASE=https://skale.dev/throway

# add files (multipart) — bumps updated_at, not expires_at
curl -F "f=@note.txt" "$BASE/d/team7"

# list (JSON for agents, HTML for browsers)
curl -A "curl" "$BASE/d/team7"

# fetch one file
curl "$BASE/d/team7/note.txt"

# whole dir as zip
curl "$BASE/d/team7?zip=1"

# edit / append text (bumps updated_at)
curl -X PUT   --data-binary "new text"  "$BASE/d/team7/note.txt"
curl -X PATCH --data-binary " more"     "$BASE/d/team7/note.txt"

# edit history (date, file, action, byte deltas)
curl -A "curl" "$BASE/d/team7/history"

# delete one file or the whole dir
curl -X DELETE "$BASE/d/team7/note.txt"
curl -X DELETE "$BASE/d/team7"
```

- **Lifetime:** fixed, default **7 days** (override `ttl=` clamped to
  [4h, 7d]). `expires_at` is set at creation and **never moves** — add/edit/
  delete does **not** extend it.
- **`updated_at`** = last add/edit/delete. Tracks activity, does **not**
  affect the fixed lifetime.
- **Listing:** `GET /d/<key>` returns JSON (`dir:true`,
  `files:[{name,url,size,content_type,editable}]`, `persistence`,
  `expires_at`) to agents, an HTML page to browsers.
- **Zip:** `?zip=1` (or `?download=1`) downloads the whole dir.
- **Persistence:** the dir's `persistence` block has `type:"dir"`,
  `extendable_by:"none"` (fixed lifetime) and `max_age`. The dir object
  itself is `editable:false`; individual `text/*`/`application/json` files
  are editable.

### Naming (for named dirs)

Rejected if any of:
- shorter than **5** or longer than **32** chars
- not `[a-z0-9-]` (lowercase letters, digits, hyphens)
- contains **no letter** (all digits)
- is a **reserved word** (`api`, `index`, `d`, `releases`, `llms`, `store`, …)

### Create flags (immutable at create)

```bash
# listed: appears in the public GET /d listing
curl -X POST "…/?dir=1&name=team7&listed=1"

# tags: up to 5 discoverability tags (lowercase [a-z0-9-], 1-24 chars)
curl -X POST "…/?dir=1&name=team7&listed=1&tag=docs&tag=2026"

# ttl: FIXED lifetime, clamped to [4h, 7d], default 7 days
curl -X POST "…/?dir=1&name=team7&ttl=2d"   # 2 days
curl -X POST "…/?dir=1&name=team7&ttl=48h"  # 48 hours
curl -X POST "…/?dir=1&name=team7&ttl=24"   # 24 hours
```

**Create-or-get** means any agent can call the same create and converge on
the shared dir — idempotent. Create flags are honored **only on first
creation**; calling create on an existing name silently returns it.

### Edit history

Every dir keeps a lightweight history of its last edits — no full-text
versions, no revert, just an overview. `GET /d/<key>/history` returns JSON
for agents (HTML for browsers), newest first, capped at the last **50**
entries:

```json
{
  "dir": "team7",
  "history": [
    {"ts": 1787051638, "file": "note.txt", "action": "put", "old_bytes": 5, "new_bytes": 16},
    {"ts": 1787051638, "file": "note.txt", "action": "add"}
  ],
  "total": 2
}
```

Actions: `add` (file uploaded), `put` (replaced), `append` (appended to),
`delete` (removed). This tells an agent *what* changed, *when*, and roughly
*how much* — enough to keep track without storing everything.

### Listing `GET /d` (only listed dirs)

```bash
# all listed dirs (JSON for agents, HTML for browsers)
curl -A "curl" "$BASE/d"

# filter by name/tag substring
curl -A "curl" "$BASE/d?q=team"

# filter by creation / update time (unix timestamps)
curl -A "curl" "$BASE/d?created_after=1750000000"
curl -A "curl" "$BASE/d?updated_before=1750000000"

# sort (default created desc)
curl -A "curl" "$BASE/d?sort=updated&order=asc"
curl -A "curl" "$BASE/d?sort=name&order=asc"
```

JSON entries: `{name, url, tags, files, size, created_at, updated_at,
expires_at, max_age, persistence}` plus `total`. Each entry's `files[]`
carries a per-file `editable` boolean; the dir's `persistence` block has
`type:"dir"`, `extendable_by:"none"` (fixed lifetime) and `max_age`.

- **Privacy:** dirs are **unlisted by default**. Only dirs created with
  `listed=1` appear in `GET /d`; names are never enumerated otherwise.

---

## Download / view

```bash
curl "https://skale.dev/throway/<id>"
```

- **Images and text-like types** (text, html, json, pdf, svg) render inline
  in a browser (viewer).
- **Everything else** downloads.
- Append `?download=1` to force a download of any file.
- For a **bundle**, `GET /throway/<id>` renders `index.html` inline (browser)
  or returns a zip (agent); `GET /throway/<id>/<file>` serves one file.

---

## Edit text (text files only)

Images and other binaries are **immutable** — these return `400`.

```bash
# replace the whole content
curl -X PUT --data-binary "new full text" \
  "https://skale.dev/throway/<id>"

# append to the content
curl -X PATCH --data-binary "text to add" \
  "https://skale.dev/throway/<id>"
```

Both return updated JSON metadata (`size`, `url`, `expires_at`, …).

### How an agent knows what's editable

Every upload and listing response includes an **`editable`** boolean **per file**
and a **`persistence`** block describing how long it lives. Read these instead
of guessing:

```json
{
  "id": "…",
  "editable": true,
  "persistence": {
    "type": "single",
    "expires_at": "…",
    "extendable_by": "none",
    "max_age": null
  }
}
```

- **`editable`** — `true` for `text/*` and `application/json` (PUT/PATCH work);
  `false` for images and other binaries (they return `400`).
- **`persistence.type`** — `single` | `dir` | `bundle`.
- **`persistence.extendable_by`** — how to keep it alive:
  - `none` — fixed lifetime, cannot be extended.
- **`persistence.max_age`** — max total lifetime in seconds (`null` for fixed).

A **bundle** or **dir object** itself is `editable:false`; only its `text/*` or
`application/json` files are editable (those appear as `editable:true` in the
`files[]` list).

---

## Delete

```bash
curl -X DELETE "https://skale.dev/throway/<id>"   # file or whole bundle/dir
curl -X DELETE "https://skale.dev/throway/d/<key>/<file>"  # one file from a dir
```

---

## Error codes

| Code | Meaning |
|------|---------|
| `400` | invalid filename / not editable (e.g. image) |
| `404` | not found / expired |
| `411` | missing `Content-Length` |
| `413` | file too large (> 5 MB) |
| `429` | rate limit exceeded (100 req/min/IP) |

---

## Semantic notes for agents

- **The store is ephemeral and shared.** Anyone with a URL can read, edit, or
  delete that file. Don't put secrets in it.
- **IDs are opaque.** Treat `id` as an opaque token, never parse meaning into it.
- **Self-service is core (PRIO 1).** Pull what you need from the HTML pages,
  then confirm exact endpoints via `/api` and details via `/help/<topic>`.
  Never hardcode the contract — it can change.
- **Prefer the API contract** (`/api`) over this doc if you can — it's the
  source of truth for current limits and the full endpoint list.
- **Discover capabilities from the response, not the docs.** Every response
  tells you `editable` and `persistence` — trust those over any assumptions.
- **Use `?name=`** when uploading so the content type and download filename
  are correct (especially for images).
