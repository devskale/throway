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
| URL lifetime | 4 hours (14400s) default — single files & bundles |
| Single-file lifetime | default 4h; `ttl=` override clamped to [4h, 14d] (max 14 days) |
| Dir lifetime | fixed, default 7 days (**max 14 days**: `ttl=` clamp [4h, 14d]) |
| Dir history | last 50 edits per dir |
| Max file size | 5 MB |
| Pool size | 100 MB (oldest files evicted first) |
| Rate limit | 100 req/min per IP |
| pics gallery | own 20 GB pool (full → 507 reject, never evicts), fixed 90-day lifetime, 30 MB max/image, recompressed ≤ 2048 px WebP q80 |

## Pics — event galleries (`/pics`, since 1.20.0)

Anyone can create a gallery and becomes its admin via a per-gallery secret
token (dirs-style: create-or-get for dir-shaped names, unlisted by default,
`listed=1` for the public index). Own pool shared across galleries —
independent of the 4h throwaway pool.

```bash
BASE=https://skale.dev/throway

# create (token + admin_url shown exactly once; create-or-get for
# dir-style names like "hochzeit-2026" -> /pics/g/hochzeit-2026)
curl -X POST "$BASE/pics?create=1&name=hochzeit-2026"

# upload (raw bytes; multipart for batches) -> JSON with id, url, thumb
curl --data-binary @photo.jpg "$BASE/pics/g/hochzeit-2026?name=photo.jpg"

# gallery listing: JSON for agents, HTML grid for browsers (paginated ?p=N)
curl -A curl "$BASE/pics/g/hochzeit-2026"

# index (only listed=1 galleries)
curl -A curl "$BASE/pics"

# one image / its thumbnail
curl "$BASE/pics/i/<id>"
curl "$BASE/pics/i/<id>?thumb=1"
```

Behavior: uploads are recompressed server-side to max 2048 px WebP q80
(EXIF rotation respected; GIFs pass through untouched; HEIC/AVIF decoded
via pillow-heif). The original bytes are discarded. Images live a fixed
90 days; an upload slides the gallery's lifetime (90 days after last
upload the gallery and its images are swept). Hidden images (admin)
return 404 for everyone but the admin view. Full pool → 507, never
eviction.

Admin (per-gallery token from creation, or the server-wide superadmin
env token — always a **path segment**, never a query param; wrong token
→ 404):

```bash
# admin page (HTML) or JSON listing incl. hidden images
curl -A curl "$BASE/pics/g/<gid>/<secret>/json"

# actions: hide | unhide | delete | up | down  (form-encoded id + action)
curl -A "Mozilla" -d "id=<id>&action=hide&p=1" "$BASE/pics/g/<gid>/<secret>"
```

Re-creating an existing named gallery returns it WITHOUT the token
(`existed:true`) — guessing a name never grants admin.

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
