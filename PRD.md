# PRD — Throway

A disposable file store. Upload a thing — or a **bundle** of files (e.g. a
website) — and get back a short-lived URL. No auth, nothing permanent.

## Purpose
Share files (images, text, binaries) or a whole mini-website via a URL without
accounts or setup. Doubles as a text scratchpad. Built for agents and humans
alike.

## URL
- Site: `https://lubu.skale.dev/throway/`
- Base API: `https://lubu.skale.dev/throway`

## Requirements

### Must
- Upload a file → returns a URL
- Upload **2+ files (multipart) → a bundle** under one URL; files served at
  `/throway/<id>/<file>`; `index.html` renders inline for browsers (a real
  throwaway website); agents get a zip
- **Mutable dirs**: create a dir (`?dir=1`), keep adding files, deleted **4h
  after the latest upload** (capped at 24h total)
- URL valid **4 hours** by default, then auto-expired & deleted
- Images and text-like types render inline (viewer); other files download; `?download=1` forces download
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
- Safe filenames (sanitized, no path traversal); dedupe name collisions in a bundle
- Dir JSON listing (`dir:true`, `files:[{name,url,size,content_type}]`, `expires_at`) for agents

### Won't (v1)
- No persistence beyond 4h (dirs: beyond 24h total)
- No per-file auth / private files
- No search, no user accounts, no quota per user
- No adding files to an existing bundle (bundles are immutable snapshots)

## API

| Method | Path | Action |
|---|---|---|
| POST | `/throway/?name=<file>` | upload a file (raw body or multipart) |
| POST | `/throway/` | upload a bundle (multipart, 2+ files) |
| POST | `/throway/?dir=1` | create a mutable dir |
| POST | `/throway/<dirid>` | add files to a dir (resets TTL) |
| GET | `/throway/<id>` | download / view a file, bundle root, or dir listing |
| GET | `/throway/<id>/<file>` | fetch one file from a bundle/dir |
| GET | `/throway/<dirid>?zip=1` | download a whole dir as zip |
| GET | `/throway/<id>?download=1` | force download |
| PUT | `/throway/<id>` | replace text content (text only) |
| PATCH | `/throway/<id>` | append text (text only) |
| DELETE | `/throway/<id>` | delete file / bundle / dir |
| DELETE | `/throway/<dirid>/<file>` | remove one file from a dir |
| GET | `/throway/api` | contract (JSON) |
| GET | `/throway/write_for_agents` | description (plain text) |
| GET | `/throway/copy_for_agents` | copyable description (HTML) |

## Limits
- TTL: 4h (14400s); dirs: 4h after latest upload, max 24h total
- Max file: 5 MB
- Pool: 100 MB
- Rate: 100 req/min/IP

## Storage
- Location: `/srv/storage2/throway/` (USB HDD)
- Single files stored by random hex id; original name kept in `.meta`
- Bundles stored as a directory per id: `ROOT/<bundleid>/` with the files plus
  a `<bundleid>.meta` manifest (shared expiry, ctype map)
- Sweep deletes expired files and whole bundles; eviction treats a bundle as
  one unit (oldest first)

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
