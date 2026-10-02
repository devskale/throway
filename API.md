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
| Retention | token-gated (server-side `THROWAWAY_RETAIN_TOKEN`): token uploads **never expire**, exempt from pool eviction; public read, token-gated write (see [Retention](#retention-indefinite-objects-token-gated-since-1440)) |
| pics gallery | own 20 GB pool (full → 507 reject, never evicts), fixed 90-day lifetime, 30 MB max/image; ≤ 2048 px stored byte-identical (JPEG metadata stripped losslessly), larger downscaled to 2048 px WebP q90 (≤ 1 MB) |

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

# embed a gallery anywhere (minimal view, transparent bg, no uploader):
#   <iframe src="$BASE/pics/g/hochzeit-2026?embed=1"
#           style="width:100%;height:640px;border:0" loading="lazy"></iframe>
#   full copy-paste snippet (auto-height + lightbox viewport handshake)
#   sits on each gallery page ("diese Galerie einbetten"): the host
#   posts {type:"throway:pics:viewport",height} to the iframe, which
#   acks with {type:"throway:pics:ready"} once its listener is live.

# index (only listed=1 galleries)
curl -A curl "$BASE/pics"

# one image / its thumbnail (?thumb=1 default, ?thumb=160|320|640 exact)
curl "$BASE/pics/i/<id>"
curl "$BASE/pics/i/<id>?thumb=1"
curl "$BASE/pics/i/<id>?thumb=320"
```

### Likes & comments (since 1.38.0)

No login: likes toggle per visitor (pseudonymous fingerprint from IP+UA,
keyed by the gallery's secret — never exposed). Comments are a guestbook:
name optional, nothing personal persisted.

```bash
# like / unlike an image (toggle) -> {id, likes, liked}
curl -X POST "$BASE/pics/i/<id>?like=1"

# add a comment (form or JSON: name <=40 optional, text <=500)
curl -X POST "$BASE/pics/g/hochzeit-2026?comment=1" \
     -d "name=Anna&text=War%20sch%C3%B6n!"

# like a comment (toggle)
curl -X POST "$BASE/pics/g/hochzeit-2026?clike=<cid>"

# cheap counters for polling / live ranking (no image payloads)
curl "$BASE/pics/g/hochzeit-2026?likes=1"

# rank images by likes (ties keep curated order; ?embed=1 keeps it too)
curl -A curl "$BASE/pics/g/hochzeit-2026?sort=likes"
```

Cooldown 20 s per visitor (RAM only — restarts clear it), max 500
comments per gallery (`THROWAWAY_PICS_MAX_COMMENTS`). Browsers get heart
buttons on every thumb + in the lightbox, an AJAX comment form, and on
`sort=likes` pages liked images FLIP-climb live (30 s poll). Admins
delete comments via the admin page (action `cdel`).

### Star & share-set (since 1.43.0)

Starring is a **private** per-browser selection (localStorage
`ty_stars_<gid>`), separate from public likes — guests pick favourite
photos and share them as a set. No server state, no counters, no rate
limits, no "who may delete the set" problem.

```bash
# the shared link — starred pids first (in link order), rest after
curl -A curl "$BASE/pics/g/hochzeit-2026?stars=<pid1>,<pid2>"
#   -> "selected": ["<pid1>", "<pid2>"], images sorted starred-first
```

Unknown/hidden/expired pids in the link are silently skipped — the link
never 404s. `?sort=likes` still ranks the non-starred rest. Browsers
mark starred cells with a filled star and show an "Als Set teilen"
button (visible once >= 1 star is picked) that builds the `?stars=` link
from localStorage and shares it via `navigator.share` (copy fallback);
stars persist across reloads.

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

# actions: hide | unhide | delete | up | down | cdel  (form-encoded id + action;
# cdel removes a comment, id carries the comment id)
curl -A "Mozilla" -d "id=<id>&action=hide&p=1" "$BASE/pics/g/<gid>/<secret>"
```

Re-creating an existing named gallery returns it WITHOUT the token
(`existed:true`) — guessing a name never grants admin.

## Retention — indefinite objects (token-gated, since 1.44.0)

throway is disposable by default. When the server has a **retain token**
configured (`/api` limits: `"retention_token": true`), requests that
authenticate with it create objects that **never expire** and are exempt
from pool eviction — stable URLs for skill installs, share slugs, etc.

```bash
BASE=https://skale.dev/throway
TOKEN=<retain-token>

# upload that never expires (Bearer header preferred — query reaches logs)
curl -X POST --data-binary @skill.tar.gz \
     -H "Authorization: Bearer $TOKEN" \
     "$BASE/?name=skill.tar.gz"
# -> {"expires_at": null, "persistence": {"retention": "indefinite", ...}}

# same via query param
curl -X POST --data-binary @note.txt "$BASE/?name=note.txt&token=$TOKEN"

# flip an EXISTING file/bundle to indefinite (idempotent)
curl -X POST -H "Authorization: Bearer $TOKEN" "$BASE/<id>?retain=1"

# flip a dir
curl -X POST -H "Authorization: Bearer $TOKEN" "$BASE/d/<key>?retain=1"
```

Rules:

- Works for single files, bundles, dirs, `?share=` names and `?url=`
  server-side imports alike.
- **Reads stay public** (anyone with the URL). **Writes and deletes on
  retained objects require the token** (401 otherwise) — a permanent URL
  must not be defaceable.
- Token-authenticated `PUT`/`PATCH` (or dir writes) make the target
  indefinite — *write implies retention*.
- `&once=1` (burn-after-reading) cannot be combined with retention (400).
- Retained units are never swept and never pool-evicted; when the pool
  runs tight, only disposable units are evicted.
- Wrong/missing token on a retention request: `401`. On servers without
  a retain token: `401 retention is not enabled`.
- Details: `GET /help/retention` (topic `retention`).

## Browser rendering (since 1.27.0 / 1.28.0)

- **`.md` files** render as self-contained HTML for browsers (own
  stdlib renderer: headings, lists, tables, fences, links, emphasis;
  link schemes restricted, everything HTML-escaped). Agents keep getting
  raw bytes; `?raw=1` is the explicit escape for browsers.
- **DIR roots** with an `index.html` serve it inline to browsers (like
  bundle URLs), with a small footer link to the file listing.
  `?listing=1` forces the classic listing; agents keep getting JSON.

## Dirs — write protection (optional, since 1.29.0)

Create a dir with `&write=1` (server generates a token) or
`&write=<own token>` (8-64 chars `[A-Za-z0-9._-]`). The create response
contains `write_token` **exactly once**.

Afterwards writes — `POST /d/<key>`, `PUT`/`PATCH`/`DELETE` on its
files, `DELETE /d/<key>`, and `POST /?share=<name>` into it — require
the token as the `X-Throway-Write` header or `?write=<token>`:

```bash
curl -X POST "$BASE/?dir=1&name=report&write=1"     # -> write_token
curl -H "X-Throway-Write: <token>" -F "f=@report.md" "$BASE/d/report"
curl -X PUT -H "X-Throway-Write: <token>" --data-binary v2 "$BASE/d/report/report.md"
# oder per Query: ?write=<token> — ohne/falsch: 401
```

Reads, listing, history and zip stay open without the token; the
listing shows `write_protected: true`. Re-creating an existing protected
dir never re-reveals the token. Dirs without the flag behave as ever.

## Endpoint-Index (Namen aus `/api`)

Agenten können `/api` nach diesen Schlüsseln fragen; hier der Link zur
Doku-Stelle. (CI prüft diese Liste gegen die Live-Spec — Docs-Drift
schlägt beim Push an.)

| Key | Doku |
|---|---|
| upload | [Upload a file](#upload-a-file) |
| upload_bundle | [Upload a bundle](#upload-a-bundle-multiple-files) |
| download | [Download / view a file](#download--view-a-file) |
| import_url | [Upload a file](#upload-a-file) (`?url=`) |
| browse_files | [Help](#help-modular-api-gatherable) (`/browse`) |
| tag_file | [Upload a file](#upload-a-file) (Tags) |
| download_bundle_file | [View / download a bundle](#view--download-a-bundle) |
| create_dir | [Dirs](#dirs--one-unified-concept-under-dkey) |
| add_to_dir | [Dirs](#dirs--one-unified-concept-under-dkey) |
| get_dir | [Dirs](#dirs--one-unified-concept-under-dkey) |
| get_dir_file | [Dirs](#dirs--one-unified-concept-under-dkey) |
| dir_zip | [Dirs](#dirs--one-unified-concept-under-dkey) |
| get_dir_history | [Dirs](#dirs--one-unified-concept-under-dkey) |
| edit_dir_file | [Dirs — write protection](#dirs--write-protection-optional-since-1290) |
| append_dir_file | [Dirs — write protection](#dirs--write-protection-optional-since-1290) |
| delete_dir_file | [Dirs — write protection](#dirs--write-protection-optional-since-1290) |
| delete_dir | [Dirs — write protection](#dirs--write-protection-optional-since-1290) |
| list_dirs | [Dirs](#dirs--one-unified-concept-under-dkey) |
| delete | [Delete a file](#delete-a-file) |
| retain | [Retention](#retention-indefinite-objects-token-gated-since-1440) |
| edit_text | [Dirs — write protection](#dirs--write-protection-optional-since-1290) |
| append_text | [Dirs — write protection](#dirs--write-protection-optional-since-1290) |
| contract | [Contract endpoint](#contract-endpoint) |
| write_for_agents | [Contract endpoint](#contract-endpoint) |
| copy_for_agents | [Contract endpoint](#contract-endpoint) |
| help | [Help](#help-modular-api-gatherable) |
| releases | [Contract endpoint](#contract-endpoint) |
| pics_create | [Pics](#pics--event-galleries-pics-since-1200) |
| pics_index | [Pics](#pics--event-galleries-pics-since-1200) |
| pics_gallery | [Pics](#pics--event-galleries-pics-since-1200) |
| pics_upload | [Pics](#pics--event-galleries-pics-since-1200) |
| pics_import_url | [Pics](#pics--event-galleries-pics-since-1200) |
| pics_image | [Pics](#pics--event-galleries-pics-since-1200) |
| pics_like | [Pics](#pics--event-galleries-pics-since-1200) |
| pics_likes_json | [Pics](#pics--event-galleries-pics-since-1200) |
| pics_comment | [Pics](#pics--event-galleries-pics-since-1200) |
| pics_comment_like | [Pics](#pics--event-galleries-pics-since-1200) |
| pics_admin | [Pics](#pics--event-galleries-pics-since-1200) |

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
curl -A "curl" "https://skale.dev/throway/help/markdown"
```
Topics: `overview`, `files`, `bundles`, `dirs`, `markdown`, `view`,
`edit`, `delete`, `limits`, `contract`, `retention`. Browsers get an HTML index / page;
unknown topics return `404`. Pull only the topics you need.

Markdown (`/help/markdown`): any `.md`/`.markdown` upload renders as a
self-contained HTML page for browsers; agents and `?raw=1` get the raw
text/markdown. Living docs: named dir + `PUT`/`PATCH` edits + history.
