# Throwaway Store — Agent API

A disposable file store. Upload a file, get back a URL valid for **4 hours**.
Files auto-expire and are deleted. No auth required.

**Base URL:** `https://lubu.skale.dev:8001/throway`

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
  "https://lubu.skale.dev:8001/throway/?name=photo.png"
```

### Option B — multipart form
```bash
curl -F "file=@photo.png" "https://lubu.skale.dev:8001/throway/"
```

### Success response (JSON)
```json
{
  "id": "96c31bf491abdf91",
  "url": "https://lubu.skale.dev:8001/throway/96c31bf491abdf91",
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

## Download / view a file
`GET {base}/<id>`
- **Images** render inline in the browser (viewer).
- **Everything else** downloads.
- Append `?download=1` to force a download of any file.

```bash
curl -O "https://lubu.skale.dev:8001/throway/<id>"
```

## Edit / append text

For **text** files only (images are immutable). Both return the updated JSON metadata.

### Replace (edit) — `PUT /<id>`
```bash
curl -X PUT --data-binary "new full text" "https://lubu.skale.dev:8001/throway/<id>"
```

### Append — `PATCH /<id>`
```bash
curl -X PATCH --data-binary "text to add" "https://lubu.skale.dev:8001/throway/<id>"
```

> `PUT`/`PATCH` on a non-text file (e.g. an image) returns `400`.

## Delete a file
```bash
curl -X DELETE "https://lubu.skale.dev:8001/throway/<id>"
```

## Contract endpoint
```bash
curl "https://lubu.skale.dev:8001/throway/api"
```
Returns current limits + endpoint descriptions as JSON.
