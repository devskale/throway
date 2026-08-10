# 🤖 AGENTS.md — Guide for Agents & Programs

This file is written for **agents, bots, and scripts** that want to use throway.
It tells you exactly how to upload, share, and manage files — and how to make
your agent discover the API by itself.

---

## TL;DR

```
Base URL:  https://lubu.skale.dev:8001/throway
Lifetime:  4 hours (files auto-delete)
Auth:      none
```

1. `POST` a file → get back JSON with an `id` and `url`.
2. Share that `url`. It's valid for 4 hours.
3. `GET` to download, `PUT`/`PATCH` to edit text, `DELETE` to remove.

---

## Self-discovery

Don't hardcode the contract. Read it at runtime:

```
GET https://lubu.skale.dev:8001/throway/api
```

Returns current limits and endpoints as JSON. If you `GET` the root URL
(`/throway/`) as a non-browser client, you'll also receive the plain-text
agent description with the same instructions.

---

## Upload a file

### Raw body (simplest)
```bash
curl -X POST --data-binary @photo.png \
  "https://lubu.skale.dev:8001/throway/?name=photo.png"
```

### Multipart form
```bash
curl -F "file=@photo.png" "https://lubu.skale.dev:8001/throway/"
```

### Response (JSON)
```json
{
  "id": "4f2a1c9d0e3b8a77",
  "url": "https://lubu.skale.dev:8001/throway/4f2a1c9d0e3b8a77",
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

## Download / view

```bash
curl "https://lubu.skale.dev:8001/throway/<id>"
```

- **Images** render inline in a browser (viewer).
- **Everything else** downloads.
- Append `?download=1` to force a download of any file.

---

## Edit text (text files only)

Images and other binaries are **immutable** — these return `400`.

```bash
# replace the whole content
curl -X PUT --data-binary "new full text" \
  "https://lubu.skale.dev:8001/throway/<id>"

# append to the content
curl -X PATCH --data-binary "text to add" \
  "https://lubu.skale.dev:8001/throway/<id>"
```

Both return updated JSON metadata (`size`, `url`, `expires_at`, …).

---

## Delete

```bash
curl -X DELETE "https://lubu.skale.dev:8001/throway/<id>"
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
