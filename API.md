# Throwaway Store — Agent API

A disposable file store. Upload a file — or a **bundle** of files (e.g. a
website) — and get back a URL valid for **4 hours**. Files auto-expire and
are deleted. No auth required.

**Base URL:** `https://skale.dev/throway`

> The machine-readable contract is always available at `GET {base}/api`.
> An agent should read that endpoint first to discover current limits.

## Limits
| Limit | Value |
|---|---|
| URL lifetime | 4 hours (14400s) — single files & bundles, fixed |
| Dir lifetime | fixed, default 7 days (`ttl=` clamp [4h, 7d]) |
| Dir history | last 50 edits per dir |
| Max file size | 5 MB |
| Pool size | 100 MB (oldest files evicted first) |
| Rate limit | 100 req/min per IP |

## Upload a file

### Option A — raw body (simplest)
```bash
curl -X POST --data-binary @photo.png \
  "https://skale.dev/throway/?name=photo.png"
```

### Option B — multipart form (single file)
```bash
curl -F "file=@photo.png" "https://skale.dev/throway/"
```

### Success response (JSON)
```json
{
  "id": "96c31bf491abdf91",
  "url": "https://skale.dev/throway/96c31bf491abdf91",
  "size": 148,
  "name": "photo.png",
  "content_type": "image/png",
  "editable": false,
  "persistence": {
    "type": "single",
    "expires_at": "2026-08-10T14:57:09Z",
    "extendable_by": "activity",
    "max_age": null
  },
  "expires_in": 14400,
  "expires_at": "2026-08-10T14:57:09Z"
}
```

The `url` field is what you share. It is valid until `expires_at`.

**`editable`** — `true` for `text/*` and `application/json` (PUT/PATCH work);
`false` for images and binaries.

**`persistence`** — how long the resource lives and how to keep it alive:
- `type` — `single` | `dir` | `bundle`.
- `expires_at` — when it dies.
- `extendable_by` — `none` (fixed lifetime) | `activity` (sliding lifetime).
- `max_age` — max total lifetime in seconds (`null` for fixed).

### Errors
| Code | Meaning |
|---|---|
| 411 | missing `Content-Length` |
| 413 | file too large (> 5 MB) |
| 429 | rate limit exceeded |

## Upload a bundle (multiple files)

`POST` 2+ file parts in a single multipart body creates a **bundle** — one
URL holding several files (e.g. an `index.html` + `style.css` website).

```bash
curl -F "f=@index.html;type=text/html" \
     -F "f=@style.css;type=text/css" \
     "https://skale.dev/throway/"
```

### Success response (JSON)
```json
{
  "id": "9c0f2b8a1d4e6f03",
  "url": "https://skale.dev/throway/9c0f2b8a1d4e6f03",
  "bundle": true,
  "editable": false,
  "persistence": {
    "type": "bundle",
    "expires_at": "2026-08-12T12:19:14Z",
    "extendable_by": "activity",
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

A **bundle itself is immutable** (`editable:false`); individual `text/*` or
`application/json` files within it are editable (`editable:true` in `files[]`).

## Download / view a file
`GET {base}/<id>`
- **Images and text-like types** (text, html, json, pdf, svg) render inline
  in the browser (viewer).
- **Everything else** downloads.
- Append `?download=1` to force a download of any file.

```bash
curl -O "https://skale.dev/throway/<id>"
```

## View / download a bundle
`GET {base}/<id>` — the bundle root:
- **Browsers** get `index.html` rendered inline (a real mini-website);
  relative links to other bundle files just work.
- **Agents / curl** get the whole bundle as a **zip**.
- `?download=1` forces the zip download for anyone.

`GET {base}/<id>/<filename>` — fetch one file from the bundle (inline for
text/images, download otherwise).

If a bundle has no `index.html`, browsers get a simple file listing instead.
The whole bundle shares one 4-hour expiry and is evicted as one unit.

```bash
curl -O "https://skale.dev/throway/<id>/style.css"
```

## Dirs (one unified concept, under /d/<key>)

A **dir** is a collection of files you keep adding to and editing over time —
a disposable workspace for an agent. One concept, addressable by an opaque
**id** (unnamed) or a memorable **name** (named), always under `/d/<key>`.
Fixed lifetime (default 7 days) and a lightweight edit history.

### Create
`POST {base}/?dir=1[&name=<name>][&listed=1][&tag=<tag>][&ttl=<h|d>]`

```bash
curl -X POST "https://skale.dev/throway/?dir=1"          # unnamed (hex id)
curl -X POST "https://skale.dev/throway/?dir=1&name=team7" # named (create-or-get)
# -> {"id":"team7","name":"team7","url":"…/d/team7","dir":true,"editable":false,"persistence":{"type":"dir","extendable_by":"activity",...},"listed":false,"tags":[],"files":[],"expires_at":"…","max_age":604800}
```
**Create-or-get** (idempotent): any agent calling the same create converges
on the shared dir. Create flags are honored **only on first creation**;
re-calling create on an existing name silently returns it.

### Naming rules
Rejected if: length <5 or >32; not `[a-z0-9-]`; all digits (no letter); or a
reserved word (`api`, `index`, `d`, `releases`, `llms`, `store`, …).

### Create flags (immutable at create)
- `&listed=1` — appears in the public `GET /d` listing.
- `&tag=<t>` — up to 5 discoverability tags (lowercase `[a-z0-9-]`, 1-24 chars).
- `&ttl=<h|d>` — **sliding lifetime**, clamped to `[4h, 7d]`, default **7 days**.

### Fixed lifetime
`expires_at` slides forward by `ttl` on each add/edit/delete (capped at 30 days
total from creation). An active dir keeps living; an idle one dies `ttl` after
its last activity.

### Using a dir
```bash
BASE=https://skale.dev/throway
curl -F "f=@note.txt" "$BASE/d/team7"          # add files (bumps updated_at)
curl -A "curl" "$BASE/d/team7"                 # list (JSON for agents, HTML for browsers)
curl "$BASE/d/team7/note.txt"                  # fetch one file
curl "$BASE/d/team7?zip=1"                     # whole dir as zip
curl -X PUT --data-binary "new" "$BASE/d/team7/note.txt"   # edit text
curl -X PATCH --data-binary " more" "$BASE/d/team7/note.txt" # append text
curl -A "curl" "$BASE/d/team7/history"         # edit history (JSON)
curl -X DELETE "$BASE/d/team7/note.txt"        # delete one file
curl -X DELETE "$BASE/d/team7"                 # delete whole dir
```
- **`updated_at`** = last add/edit/delete. Tracks activity; does **not** affect lifetime.
- **Privacy:** unlisted by default; only `listed=1` dirs appear in `GET /d`.

### Edit history
`GET /d/<key>/history` — lightweight history of the last **50** edits,
newest first. JSON for agents, HTML for browsers.
```json
{"dir":"team7","history":[
  {"ts":1787051638,"file":"note.txt","action":"put","old_bytes":5,"new_bytes":16},
  {"ts":1787051638,"file":"note.txt","action":"add"}
],"total":2}
```
Actions: `add` | `put` | `append` | `delete`, each with a timestamp and a
byte delta where relevant. No full-text versions, no revert.

### Listing `GET /d` (only listed dirs)
JSON for agents, HTML for browsers. Entries: `{name, url, tags, files, size,
created_at, updated_at, expires_at, max_age, persistence}` + `total`. Each
`files[]` carries a per-file `editable` boolean; the dir's `persistence` has
`type:"dir"`, `extendable_by:"activity"` (sliding lifetime) and `max_age`.
```bash
curl -A "curl" "$BASE/d"                                    # all listed
curl -A "curl" "$BASE/d?q=team"                            # name/tag substring
curl -A "curl" "$BASE/d?created_after=1750000000"          # by creation time
curl -A "curl" "$BASE/d?updated_before=1750000000"         # by update time
curl -A "curl" "$BASE/d?sort=updated&order=asc"            # sort created|updated|name, asc|desc
```

## Edit / append text

For **text** and **JSON** files only (images are immutable). Both return the
updated JSON metadata (with `editable` and `persistence`).

### Replace (edit) — `PUT /<id>`
```bash
curl -X PUT --data-binary "new full text" "https://skale.dev/throway/<id>"
```

### Append — `PATCH /<id>`
```bash
curl -X PATCH --data-binary "text to add" "https://skale.dev/throway/<id>"
```

> `PUT`/`PATCH` on a non-text file (e.g. an image) returns `400`.

Every response's `editable` field tells you whether PUT/PATCH will work on a
given file — `true` for `text/*` and `application/json`, `false` otherwise.
Trust it instead of guessing from the filename.

## Delete a file
```bash
curl -X DELETE "https://skale.dev/throway/<id>"
```

## Contract endpoint
```bash
curl "https://skale.dev/throway/api"
```
Returns current limits + endpoint descriptions as JSON.

## Help (modular, API-gatherable)
```bash
# JSON index of help topics (agents)
curl -A "curl" "https://skale.dev/throway/help"

# one topic as plain text
curl -A "curl" "https://skale.dev/throway/help/named_dirs"
```
Topics: `overview`, `files`, `bundles`, `dirs`, `named_dirs`, `view`,
`edit`, `delete`, `limits`, `contract`. Browsers get an HTML index / page;
unknown topics return `404`. Pull only the topics you need.
