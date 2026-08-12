# PRD — Throway

A disposable file store. Upload a thing, get back a short-lived URL. No auth, nothing permanent.

## Purpose
Share files (images, text, binaries) via a URL without accounts or setup. Doubles as a text scratchpad. Built for agents and humans alike.

## URL
- Site: `https://lubu.skale.dev/throway/`
- Base API: `https://lubu.skale.dev/throway`

## Requirements

### Must
- Upload a file → returns a URL
- URL valid **4 hours** by default, then auto-expired & deleted
- Images render inline (viewer); other files download; `?download=1` forces download
- Text files editable: `PUT` = replace, `PATCH` = append
- Rolling **100 MB** pool (oldest evicted first)
- Max file **5 MB**
- No auth
- **100 req/min** per IP
- Machine-readable contract at `/api`
- Description for agents at `/write_for_agents` + copyable at `/copy_for_agents`

### Should
- Simple raw-body `POST` + standard multipart `POST`
- Clean JSON responses with `id`, `url`, `size`, `name`, `content_type`, `expires_in`, `expires_at`
- Safe filenames (sanitized, no path traversal)

### Won't (v1)
- No persistence beyond 4h
- No per-file auth / private files
- No search, no user accounts, no quota per user

## API

| Method | Path | Action |
|---|---|---|
| POST | `/throway/?name=<file>` | upload (raw body or multipart) |
| GET | `/throway/<id>` | download / view |
| GET | `/throway/<id>?download=1` | force download |
| PUT | `/throway/<id>` | replace text content (text only) |
| PATCH | `/throway/<id>` | append text (text only) |
| DELETE | `/throway/<id>` | delete |
| GET | `/throway/api` | contract (JSON) |
| GET | `/throway/write_for_agents` | description (plain text) |
| GET | `/throway/copy_for_agents` | copyable description (HTML) |

## Limits
- TTL: 4h (14400s)
- Max file: 5 MB
- Pool: 100 MB
- Rate: 100 req/min/IP

## Storage
- Location: `/srv/storage2/throway/` (USB HDD)
- Files stored by random hex id; original name kept in `.meta`
- Sweep deletes expired files

## Errors
| Code | Meaning |
|---|---|
| 400 | invalid filename / not editable (image) |
| 404 | not found / expired |
| 411 | missing Content-Length |
| 413 | too large |
| 429 | rate limit |

## Deployment
- Single-file Python stdlib server: `/var/www/store/store.py`
- systemd: `throway-store.service` (port 8111, auto-start/restart)
- nginx: `/throway/` + `/store/` proxy on `lubu.skale.dev` (443)
