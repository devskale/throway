# 🤖 AGENTS.md — Guide for Agents & Programs

This file is written for **agents, bots, and scripts** that want to use throway.
It tells you exactly how to upload, share, and manage files — and how to make
your agent discover the API by itself.

---

## TL;DR

```
Base URL:  https://lubu.skale.dev/throway
Lifetime:  4 hours (files auto-delete)
Auth:      none
```

1. `POST` a file → get back JSON with an `id` and `url`.
2. Share that `url`. It's valid for 4 hours.
3. `GET` to download, `PUT`/`PATCH` to edit text, `DELETE` to remove.
4. `POST` 2+ files in one multipart body → a **bundle** (e.g. a website)
   under one URL, served at `/throway/<id>/<filename>`.

---

## Self-discovery

Don't hardcode the contract. Read it at runtime:

```
GET https://lubu.skale.dev/throway/api
```

Returns current limits and endpoints as JSON. If you `GET` the root URL
(`/throway/`) as a non-browser client, you'll also receive the plain-text
agent description with the same instructions.

---

## Upload a file

### Raw body (simplest)
```bash
curl -X POST --data-binary @photo.png \
  "https://lubu.skale.dev/throway/?name=photo.png"
```

### Multipart form
```bash
curl -F "file=@photo.png" "https://lubu.skale.dev/throway/"
```

### Response (JSON)
```json
{
  "id": "4f2a1c9d0e3b8a77",
  "url": "https://lubu.skale.dev/throway/4f2a1c9d0e3b8a77",
  "size": 148,
  "name": "photo.png",
  "content_type": "image/png",
  "expires_in": 14400,
  "expires_at": "2026-08-10T14:57:09Z"
}
```

- The **`id`** is the file's document ID — use it in the path of every other call.
- The **`url`** is what you share. Valid until `expires_at`.

---

## Upload a bundle (multiple files)

`POST` 2+ file parts in a single multipart body to create a **bundle** — one
URL that holds several files (e.g. an `index.html` + `style.css` website).

```bash
curl -F "f=@index.html;type=text/html" \
     -F "f=@style.css;type=text/css" \
     "https://lubu.skale.dev/throway/"
```

### Response (JSON)
```json
{
  "id": "9c0f2b8a1d4e6f03",
  "url": "https://lubu.skale.dev/throway/9c0f2b8a1d4e6f03",
  "bundle": true,
  "files": [
    {"name": "index.html", "url": "https://lubu.skale.dev/throway/9c0f2b8a1d4e6f03/index.html", "size": 202, "content_type": "text/html"},
    {"name": "style.css",  "url": "https://lubu.skale.dev/throway/9c0f2b8a1d4e6f03/style.css",  "size": 75,  "content_type": "text/css"}
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

---

## Upload a dir (mutable, keep adding files)

A **dir** is a mutable bundle: you create it once, then keep adding files to
it. It's deleted **4h after the latest upload** (capped at 24h total).

```bash
# create an empty dir
curl -X POST "https://lubu.skale.dev/throway/?dir=1"
# -> {"id":"…","url":"…/<dirid>","dir":true,"files":[],…}

# add files to it (multipart) — resets the 4h TTL
curl -F "f=@note.txt" "https://lubu.skale.dev/throway/<dirid>"

# list (JSON for agents, HTML page for browsers)
curl -A "curl" "https://lubu.skale.dev/throway/<dirid>"

# fetch one file
curl "https://lubu.skale.dev/throway/<dirid>/note.txt"

# download the whole dir as a zip
curl "https://lubu.skale.dev/throway/<dirid>?zip=1"

# remove one file, or the whole dir
curl -X DELETE "https://lubu.skale.dev/throway/<dirid>/note.txt"
curl -X DELETE "https://lubu.skale.dev/throway/<dirid>"
```

- **TTL:** deleted 4h after the **latest** upload; never lives more than 24h
  total from creation.
- **Listing:** `GET /<dirid>` returns JSON (`dir:true`, `files:[{name,url,size,content_type}]`, `expires_at`) to agents, an HTML page to browsers.
- **Zip:** `?zip=1` (or `?download=1`) downloads the whole dir.

---

## Named dirs (rememberable, team-reusable)

A **named dir** is a mutable dir you address by a **memorable name** instead
of an opaque hex id — so a team of agents can remember and reuse the same
shared dir. Everything lives under `n/<name>`.

### Create-or-get

```bash
# create (or get, if it already exists) a named dir
curl -X POST "https://lubu.skale.dev/throway/?dir=1&name=team7"
# -> {"id":"team7","name":"team7","url":"…/n/team7","dir":true,"named":true,…}
```

**Create-or-get** means any agent can call the same create and converge on
the shared dir — idempotent. Create flags are honored **only on first
creation**; calling create on an existing name silently returns it.

### Naming rules (the "ruling")

Rejected if any of:
- shorter than **5** or longer than **32** chars
- not `[a-z0-9-]` (lowercase letters, digits, hyphens)
- contains **no letter** (all digits)
- is a **reserved word** (`api`, `index`, `n`, `releases`, `llms`, `store`, …)

### Create flags (immutable at create)

```bash
# listed: appears in the public GET /n listing
curl -X POST "…/?dir=1&name=team7&listed=1"

# tags: up to 5 discoverability tags (lowercase [a-z0-9-], 1-24 chars)
curl -X POST "…/?dir=1&name=team7&listed=1&tag=docs&tag=2026"

# ttl: FIXED lifetime, clamped to [4h, 7d], default 7 days
curl -X POST "…/?dir=1&name=team7&ttl=2d"   # 2 days
curl -X POST "…/?dir=1&name=team7&ttl=48h"  # 48 hours
curl -X POST "…/?dir=1&name=team7&ttl=24"   # 24 hours
```

### TTL model (fixed lifetime)

A named dir lives a **fixed** lifetime set at creation (default 7 days,
override with `ttl=`). `expires_at` is fixed at creation and **never moves**
— adding, editing, or deleting files does **not** extend it. A named dir
truly dies at its `expires_at`.

### Using a named dir

```bash
BASE=https://lubu.skale.dev/throway

# add files (multipart) — bumps updated_at, not expires_at
curl -F "f=@note.txt" "$BASE/n/team7"

# list (JSON for agents, HTML for browsers)
curl -A "curl" "$BASE/n/team7"

# fetch one file
curl "$BASE/n/team7/note.txt"

# whole dir as zip
curl "$BASE/n/team7?zip=1"

# edit text (bumps updated_at)
curl -X PUT --data-binary "new text" "$BASE/n/team7/note.txt"
curl -X PATCH --data-binary " more" "$BASE/n/team7/note.txt"

# delete one file or the whole dir
curl -X DELETE "$BASE/n/team7/note.txt"
curl -X DELETE "$BASE/n/team7"
```

- **`updated_at`** = last add/edit/delete. It tracks activity but does **not**
  affect the fixed lifetime.
- **Privacy:** named dirs are **unlisted by default**. Only dirs created with
  `listed=1` appear in `GET /n`; names are never enumerated otherwise.

### Listing `GET /n` (only listed dirs)

```bash
# all listed dirs (JSON for agents, HTML for browsers)
curl -A "curl" "$BASE/n"

# filter by name/tag substring
curl -A "curl" "$BASE/n?q=team"

# filter by creation / update time (unix timestamps)
curl -A "curl" "$BASE/n?created_after=1750000000"
curl -A "curl" "$BASE/n?updated_before=1750000000"

# sort (default created desc)
curl -A "curl" "$BASE/n?sort=updated&order=asc"
curl -A "curl" "$BASE/n?sort=name&order=asc"
```

JSON entries: `{name, url, tags, files, size, created_at, updated_at, expires_at, max_age}` plus `total`.

---

## Download / view

```bash
curl "https://lubu.skale.dev/throway/<id>"
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
  "https://lubu.skale.dev/throway/<id>"

# append to the content
curl -X PATCH --data-binary "text to add" \
  "https://lubu.skale.dev/throway/<id>"
```

Both return updated JSON metadata (`size`, `url`, `expires_at`, …).

---

## Delete

```bash
curl -X DELETE "https://lubu.skale.dev/throway/<id>"   # file or whole bundle/dir
curl -X DELETE "https://lubu.skale.dev/throway/<dirid>/<file>"  # one file from a dir
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
- **Prefer the API contract** (`/api`) over this doc if you can — it's the
  source of truth for current limits.
- **Use `?name=`** when uploading so the content type and download filename
  are correct (especially for images).
