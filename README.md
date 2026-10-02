<div align="center">

# 🗑️ throway

**A disposable file store. Upload a thing, get a short-lived URL. No auth. Nothing permanent.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Zero deps](https://img.shields.io/badge/dependencies-zero-4caf50)](store.py)
[![Status](https://img.shields.io/badge/status-live-00c853)](#-live-instance)

Share a file, pass data between agents, host a throwaway website, or keep a
text scratchpad — without accounts, without setup, without leftovers.
Everything you upload is **short-lived and auto-expires** (files/bundles in
4 hours, dirs in up to 14 days) and disappears.

</div>

---

## ✨ Why throway?

- **Disposable by design** — nothing is permanent. Files/bundles live 4
  hours; dirs up to 14 days. No cleanup, no clutter.
- **Dead simple** — one `curl` to upload, one URL to share.
- **Zero dependencies** — a single Python stdlib file. Runs anywhere.
- **Agent-friendly** — self-describing API with a machine-readable contract.
- **No auth, no accounts** — upload, share, forget.

## 🚀 Quick start

```bash
# upload a file → get a URL back
curl -X POST --data-binary @photo.png \
  "https://skale.dev/throway/?name=photo.png"

# → {"id":"4f2a…","url":"https://skale.dev/throway/4f2a…","size":148,…}

# longer lifetime (max 14 days, default 4h)
curl -X POST --data-binary @note.txt \
  "https://skale.dev/throway/?name=note.txt&ttl=24h"

# chosen, memorable share name instead of a random id -> /d/my-note
curl -X POST --data-binary @note.txt \
  "https://skale.dev/throway/?share=my-note"

# burn-after-reading: auto-deletes after the first download
curl -X POST --data-binary @secret.txt \
  "https://skale.dev/throway/?name=secret.txt&once=1"
```

```bash
# upload a bundle (a mini website) → one URL, files at /<id>/<file>
curl -F "f=@index.html;type=text/html" \
     -F "f=@style.css;type=text/css" \
     "https://skale.dev/throway/"
# → {"id":"…","bundle":true,"files":[{name,url,size,content_type},…]}
```

```bash
# download / view
curl "https://skale.dev/throway/<id>"

# edit text (replace / append)
curl -X PUT   --data-binary "new text"     "https://skale.dev/throway/<id>"
curl -X PATCH --data-binary "append this"  "https://skale.dev/throway/<id>"

# delete
curl -X DELETE "https://skale.dev/throway/<id>"
```

> **Live instance:** `https://skale.dev/throway/`

## 🧭 Endpoints

| Method | Path | Action |
|--------|------|--------|
| `POST` | `/throway/?name=<file>` | upload a file (raw body or multipart) |
| `POST` | `/throway/` | upload a bundle (multipart, 2+ files) |
| `POST` | `/throway/?dir=1[&name=<name>]` | create a dir (unnamed or named; `&listed=1`, `&tag=`, `&ttl=`) |
| `GET` | `/throway/d` | list dirs (only `listed=1`; filter/sort via query) |
| `GET` | `/throway/d/<key>` | view a dir (listing / zip / files) |
| `POST` | `/throway/d/<key>` | add files to a dir |
| `GET` | `/throway/d/<key>/history` | edit history (JSON for agents, HTML for browsers) |
| `PUT`/`PATCH` | `/throway/d/<key>/<file>` | edit/append text in a dir |
| `DELETE` | `/throway/d/<key>` | delete a dir |
| `DELETE` | `/throway/d/<key>/<file>` | remove one file from a dir |
| `GET` | `/throway/<id>` | download / view a file, bundle root, or dir listing |
| `GET` | `/throway/<id>/<file>` | fetch one file from a bundle/dir |
| `GET` | `/throway/d/<key>?zip=1` | download a whole dir as zip |
| `GET` | `/throway/<id>?download=1` | force download |
| `PUT` | `/throway/<id>` | replace text (text only) |
| `PATCH` | `/throway/<id>` | append text (text only) |
| `DELETE` | `/throway/<id>` | delete file / bundle / dir |
| `POST` | `/throway/pics?create=1[&name=][&listed=1]` | **create a gallery** → per-gallery admin token (shown once) |
| `GET` | `/throway/pics` | gallery index (listed galleries; HTML + create form) |
| `GET` | `/throway/pics/g/<gid>` | one gallery (HTML grid / JSON) |
| `POST` | `/throway/pics/g/<gid>?name=<file>` | upload an image (raw or multipart batch) |
| `GET` | `/throway/pics/i/<id>` | serve one gallery image (`?thumb=1` for preview) |
| `POST` | `/throway/pics/i/<id>?like=1` | **toggle an image like** → `{id, likes, liked}` |
| `POST` | `/throway/pics/g/<gid>?comment=1` | **add a comment** (form/JSON: `name`, `text`) |
| `POST` | `/throway/pics/g/<gid>?clike=<cid>` | toggle a comment like |
| `GET` | `/throway/pics/g/<gid>?likes=1` | cheap like/comment counters (polling) |
| `GET` | `/throway/pics/g/<gid>?sort=likes` | gallery ranked by likes |
| `GET` | `/throway/pics/g/<gid>?stars=<p1>,<p2>` | shared star-set link: starred pids first, rest after (private selection, since 1.43.0) |
| `GET`/`POST` | `/throway/pics/g/<gid>/<secret>` | gallery admin: page / `/json` / actions (`hide`,`unhide`,`delete`,`up`,`down`) |
| `GET` | `/throway/api` | machine-readable contract (JSON) |
| `GET` | `/throway/help` | modular help index (JSON for agents, HTML for browsers) |
| `GET` | `/throway/help/<topic>` | one help topic (plain text for agents) |
| `GET` | `/throway/write_for_agents` | agent description (plain text) |
| `GET` | `/throway/copy_for_agents` | copy-pasteable agent description (HTML) |

## 📊 Limits

| Limit | Value |
|-------|-------|
| URL lifetime | **4 hours** default; single files can be extended via `ttl=` (max 14 days) |
| Dir lifetime | **fixed** (default 7 days, `ttl=` override clamped to [4h, 14d]) |
| Dir history | **last 50 edits** per dir |
| Max file size | **5 MB** |
| Pool size | **100 MB** (oldest evicted first) |
| Rate limit | **100 req/min** per IP |
| **pics galleries** | shared pool **20 GB** (full = uploads rejected with 507, never evicted), images fixed **90 days** (gallery slides on upload), max **30 MB** per image, recompressed to ≤ 2048 px WebP (GIFs pass through, HEIC supported) |

## 🖼️ Behavior

- **Images and text-like types** (text, html, json, pdf, svg) render inline in
  the browser (a viewer). Everything else downloads. Append `?download=1` to
  force a download of any file.
- **Bundles** (2+ files) are served under one URL: browsers get `index.html`
  rendered inline (a real throwaway website), agents get a zip, and each file
  is reachable at `/throway/<id>/<filename>`. The whole bundle shares one
  4-hour expiry and is evicted as one unit.
- **Dirs** are long-lived, nameable collections under `/d/<key>`: create one,
  keep adding files and editing them over days, with a **sliding lifetime**
  (default 7 days) and a lightweight **edit history**. `GET /d/<key>` returns
  a JSON listing to agents / an HTML page to browsers.
- **Text files** are editable — `PUT` rewrites the whole content, `PATCH` appends.
  Images are immutable.
- **File URLs are never listed** on the main page — you only get them from the
  upload response. The page shows live stats instead.

## 🤖 For agents

Throway is built to be consumed by other programs. **The core principle
(PRIO 1): an agent needs nothing pre-loaded — it pulls everything it needs
from the HTML pages, then confirms exact details via `/api` and `/help`.**

```bash
curl "https://skale.dev/throway/"              # HTML homepage (embedded Agent info)
curl "https://skale.dev/throway/api"           # machine-readable contract (JSON)
curl -A "curl" "https://skale.dev/throway/"    # --help summary + pointers
curl "https://skale.dev/throway/write_for_agents"  # full usage guide
```

Help is **modular** — fetch an index, then pull only the topics you need:

```bash
curl -A "curl" "https://skale.dev/throway/help"       # JSON topic index
curl -A "curl" "https://skale.dev/throway/help/dirs"   # one topic
```

See **[`AGENTS.md`](AGENTS.md)** for the complete agent guide.

## 🛠️ Deploy

| Piece | How |
|-------|-----|
| Server | Python stdlib: [`store.py`](store.py) (entry) + [`throway/`](throway/) package — only external dep: Pillow (+ `pillow-heif` for HEIC) |
| Service | systemd `throway-store.service` (port `8111`, auto-start/restart) |
| Proxy | nginx `/throway/` location |
| Storage | `/srv/storage2/throway/` (USB HDD) — `pics/` subdir = gallery pool |
| Tests | `pytest tests/` (34 behavior tests, subprocess-based) |

```bash
# run it anywhere
python3 store.py

# run the tests
.venv/bin/python -m pytest tests/ -q
```

## 📚 Docs

- **[`AGENTS.md`](AGENTS.md)** — guide for agents & programs
- **[`API.md`](API.md)** — full API reference
- **[`PRD.md`](PRD.md)** — product requirements

## 📄 License

[MIT](LICENSE) © 2026 devskale
