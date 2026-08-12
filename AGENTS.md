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
