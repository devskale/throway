# PRD — Throway

A disposable file store. Upload a thing — or a **bundle** of files (e.g. a
website) — and get back a short-lived URL. No auth, nothing permanent.

## Purpose
Share files (images, text, binaries) or a whole mini-website via a URL without
accounts or setup. Doubles as a text scratchpad. Built for agents and humans
alike.

## URL
- Site: `https://skale.dev/throway/`
- Base API: `https://skale.dev/throway`

## Requirements

### Must
- Upload a file → returns a URL
- Upload **2+ files (multipart) → a bundle** under one URL; files served at
  `/throway/<id>/<file>`; `index.html` renders inline for browsers (a real
  throwaway website); agents get a zip
- **Dirs** (one unified concept): create a dir (`?dir=1`, unnamed hex id or
  `&name=<name>` named), keep adding files, fixed lifetime (default 7 days,
  `ttl=` override clamped to [4h, 7d]), optional `listed=1` + tags, full CRUD
  under `/d/<key>`, and a **lightweight edit history** (`GET /d/<key>/history`
  — last 50 entries: date, file, action, byte deltas)
- URL valid **4 hours** by default, then auto-expired & deleted
- Images and text-like types render inline (viewer); other files download; `?download=1` forces download
- Text files editable: `PUT` = replace, `PATCH` = append
- **Self-describing responses**: every upload/listing returns `editable` (per
  file) and a `persistence` block (`type`, `expires_at`, `extendable_by`,
  `max_age`) so an agent can discover editability & lifetime from the response
  instead of guessing
- Rolling **100 MB** pool (oldest evicted first)
- Max file **5 MB**
- No auth
- **100 req/min** per IP
- Machine-readable contract at `/api`
- Description for agents at `/write_for_agents` + copyable at `/copy_for_agents`
- **Modular help** at `/help` (JSON index for agents) + `/help/<topic>` (one topic each)

### Should
- Simple raw-body `POST` + standard multipart `POST`
- Clean JSON responses with `id`, `url`, `size`, `name`, `content_type`, `editable`, `persistence`, `expires_in`, `expires_at`
- Safe filenames (sanitized, no path traversal); dedupe name collisions in a bundle
- Dir JSON listing (`dir:true`, `files:[{name,url,size,content_type,editable}]`, `persistence`, `expires_at`) for agents

### Won't (v1)
- No per-file auth / private files
- No search, no user accounts, no quota per user
- No adding files to an existing bundle (bundles are immutable snapshots)
- No full-text history / revert — dir history is an overview only (last 50 entries)

## API

| Method | Path | Action |
|---|---|---|
| POST | `/throway/?name=<file>` | upload a file (raw body or multipart) |
| POST | `/throway/` | upload a bundle (multipart, 2+ files) |
| POST | `/throway/?dir=1[&name=<name>]` | create a dir (unnamed or named; `&listed=1`, `&tag=`, `&ttl=`) |
| GET | `/throway/d` | list dirs (only `listed=1`; filter/sort) |
| POST | `/throway/d/<key>` | add files to a dir |
| GET | `/throway/d/<key>` | view a dir (listing / zip / files) |
| GET | `/throway/d/<key>/history` | edit history (JSON for agents / HTML for browsers) |
| PUT/PATCH | `/throway/d/<key>/<file>` | edit/append text in a dir |
| DELETE | `/throway/d/<key>` | delete a dir |
| GET | `/throway/<id>` | download / view a file, bundle root, or dir listing |
| GET | `/throway/<id>/<file>` | fetch one file from a bundle/dir |
| GET | `/throway/d/<key>?zip=1` | download a whole dir as zip |
| GET | `/throway/<id>?download=1` | force download |
| PUT | `/throway/<id>` | replace text content (text only) |
| PATCH | `/throway/<id>` | append text (text only) |
| DELETE | `/throway/<id>` | delete file / bundle / dir |
| DELETE | `/throway/d/<key>/<file>` | remove one file from a dir |
| GET | `/throway/api` | contract (JSON) |
| GET | `/throway/help` | modular help index (JSON for agents) |
| GET | `/throway/help/<topic>` | one help topic (plain text for agents) |
| GET | `/throway/write_for_agents` | description (plain text) |
| GET | `/throway/copy_for_agents` | copyable description (HTML) |

## Limits
- TTL: 4h (14400s) for single files & bundles
- Dirs: fixed lifetime, default 7d, `ttl=` override clamped to [4h, 7d]
- Dir history: last 50 edits per dir
- Max file: 5 MB
- Pool: 100 MB
- Rate: 100 req/min/IP

## Storage
- Location: `/srv/storage2/throway/` (USB HDD)
- Single files stored by random hex id; original name kept in `.meta`
- Bundles stored as a directory per id: `ROOT/<bundleid>/` with the files plus
  a `<bundleid>.meta` manifest (shared expiry, ctype map)
- Dirs stored at `ROOT/d/<key>/` (keys are hex ids or names; the `d/`
  namespace keeps names from colliding with hex ids) with a `<key>.meta`
  manifest (`type`, `created`, `updated`, `expires`, `max_age`, `listed`,
  `tags`, `files`) and a `<key>.history` log (last 50 edit entries)
- Sweep deletes expired files and whole bundles; eviction treats a bundle or
  dir as one unit (oldest first)

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
- nginx: `/throway/` proxy on lubu (port 8001)
- **Front door**: `skale.dev` + `www.skale.dev` -> amd2 (`158.180.42.218`), nginx
  proxies `/throway/` -> lubu; TLS via certbot (HTTP-01)
- Canonical URL: `https://skale.dev/throway` (also served at
  `https://lubu.skale.dev/throway`)
