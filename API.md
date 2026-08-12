# Throwaway Store — Agent API

A disposable file store. Upload a file — or a **bundle** of files (e.g. a
website) — and get back a URL valid for **4 hours**. Files auto-expire and
are deleted. No auth required.

**Base URL:** `https://lubu.skale.dev/throway`

> The machine-readable contract is always available at `GET {base}/api`.
> An agent should read that endpoint first to discover current limits.

## Limits
| Limit | Value |
|---|---|
| URL lifetime | 4 hours (14400s) |
| Max file size | 5 MB |
| Pool size | 100 MB (oldest files evicted first) |
| Rate limit | 100 req/min per IP |

## Upload a file

### Option A — raw body (simplest)
```bash
curl -X POST --data-binary @photo.png \
  "https://lubu.skale.dev/throway/?name=photo.png"
```

### Option B — multipart form (single file)
```bash
curl -F "file=@photo.png" "https://lubu.skale.dev/throway/"
```

### Success response (JSON)
```json
{
  "id": "96c31bf491abdf91",
  "url": "https://lubu.skale.dev/throway/96c31bf491abdf91",
  "size": 148,
  "name": "photo.png",
  "content_type": "image/png",
  "expires_in": 14400,
  "expires_at": "2026-08-10T14:57:09Z"
}
```

The `url` field is what you share. It is valid until `expires_at`.

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
     "https://lubu.skale.dev/throway/"
```

### Success response (JSON)
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

## Download / view a file
`GET {base}/<id>`
- **Images and text-like types** (text, html, json, pdf, svg) render inline
  in the browser (viewer).
- **Everything else** downloads.
- Append `?download=1` to force a download of any file.

```bash
curl -O "https://lubu.skale.dev/throway/<id>"
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
curl -O "https://lubu.skale.dev/throway/<id>/style.css"
```

## Create / use a dir (mutable)

A **dir** is a mutable bundle: create it once, keep adding files. It's
deleted **4h after the latest upload** (capped at 24h total).

### Create
`POST {base}/?dir=1` — create an empty dir.
```bash
curl -X POST "https://lubu.skale.dev/throway/?dir=1"
# -> {"id":"…","url":"…/<dirid>","dir":true,"files":[],"expires_at":"…"}
```

### Add files
`POST {base}/<dirid>` — multipart file parts; **resets the 4h TTL**.
```bash
curl -F "f=@note.txt" "https://lubu.skale.dev/throway/<dirid>"
# -> {"id":"…","dir":true,"files":[{name,url,size,content_type},…],"expires_at":"…"}
```

### List
`GET {base}/<dirid>` — **JSON** listing for agents (`dir:true`, `files`, `expires_at`), HTML page for browsers.

### Fetch one file
`GET {base}/<dirid>/<filename>`

### Download whole dir
`GET {base}/<dirid>?zip=1` (or `?download=1`) — zip of all files.

### Delete
- `DELETE {base}/<dirid>/<filename>` — remove one file.
- `DELETE {base}/<dirid>` — remove the whole dir.

**TTL:** deleted 4h after the latest upload; never more than 24h total from
creation. `GET /<dirid>` shows the current `expires_at`.

## Edit / append text

For **text** files only (images are immutable). Both return the updated JSON metadata.

### Replace (edit) — `PUT /<id>`
```bash
curl -X PUT --data-binary "new full text" "https://lubu.skale.dev/throway/<id>"
```

### Append — `PATCH /<id>`
```bash
curl -X PATCH --data-binary "text to add" "https://lubu.skale.dev/throway/<id>"
```

> `PUT`/`PATCH` on a non-text file (e.g. an image) returns `400`.

## Delete a file
```bash
curl -X DELETE "https://lubu.skale.dev/throway/<id>"
```

## Contract endpoint
```bash
curl "https://lubu.skale.dev/throway/api"
```
Returns current limits + endpoint descriptions as JSON.
