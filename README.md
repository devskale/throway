<div align="center">

# 🗑️ throway

**A disposable file store. Upload a thing, get a short-lived URL. No auth. Nothing permanent.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Zero deps](https://img.shields.io/badge/dependencies-zero-4caf50)](store.py)
[![Status](https://img.shields.io/badge/status-live-00c853)](#-live-instance)

Share a file, pass data between agents, host a throwaway website, or keep a
text scratchpad — without accounts, without setup, without leftovers.
Everything you upload gets a URL that **expires in 4 hours** and disappears.

</div>

---

## ✨ Why throway?

- **Disposable by design** — nothing lives longer than 4 hours. No cleanup, no clutter.
- **Dead simple** — one `curl` to upload, one URL to share.
- **Zero dependencies** — a single Python stdlib file. Runs anywhere.
- **Agent-friendly** — self-describing API with a machine-readable contract.
- **No auth, no accounts** — upload, share, forget.

## 🚀 Quick start

```bash
# upload a file → get a URL back
curl -X POST --data-binary @photo.png \
  "https://lubu.skale.dev/throway/?name=photo.png"

# → {"id":"4f2a…","url":"https://lubu.skale.dev/throway/4f2a…","size":148,…}
```

```bash
# upload a bundle (a mini website) → one URL, files at /<id>/<file>
curl -F "f=@index.html;type=text/html" \
     -F "f=@style.css;type=text/css" \
     "https://lubu.skale.dev/throway/"
# → {"id":"…","bundle":true,"files":[{name,url,size,content_type},…]}
```

```bash
# download / view
curl "https://lubu.skale.dev/throway/<id>"

# edit text (replace / append)
curl -X PUT   --data-binary "new text"     "https://lubu.skale.dev/throway/<id>"
curl -X PATCH --data-binary "append this"  "https://lubu.skale.dev/throway/<id>"

# delete
curl -X DELETE "https://lubu.skale.dev/throway/<id>"
```

> **Live instance:** `https://lubu.skale.dev/throway/`

## 🧭 Endpoints

| Method | Path | Action |
|--------|------|--------|
| `POST` | `/throway/?name=<file>` | upload a file (raw body or multipart) |
| `POST` | `/throway/` | upload a bundle (multipart, 2+ files) |
| `GET` | `/throway/<id>` | download / view a file, or a bundle root |
| `GET` | `/throway/<id>/<file>` | fetch one file from a bundle |
| `GET` | `/throway/<id>?download=1` | force download |
| `PUT` | `/throway/<id>` | replace text (text only) |
| `PATCH` | `/throway/<id>` | append text (text only) |
| `DELETE` | `/throway/<id>` | delete |
| `GET` | `/throway/api` | machine-readable contract (JSON) |
| `GET` | `/throway/write_for_agents` | agent description (plain text) |
| `GET` | `/throway/copy_for_agents` | copy-pasteable agent description (HTML) |

## 📊 Limits

| Limit | Value |
|-------|-------|
| URL lifetime | **4 hours** |
| Max file size | **5 MB** |
| Pool size | **100 MB** (oldest evicted first) |
| Rate limit | **100 req/min** per IP |

## 🖼️ Behavior

- **Images and text-like types** (text, html, json, pdf, svg) render inline in
  the browser (a viewer). Everything else downloads. Append `?download=1` to
  force a download of any file.
- **Bundles** (2+ files) are served under one URL: browsers get `index.html`
  rendered inline (a real throwaway website), agents get a zip, and each file
  is reachable at `/throway/<id>/<filename>`. The whole bundle shares one
  4-hour expiry and is evicted as one unit.
- **Text files** are editable — `PUT` rewrites the whole content, `PATCH` appends.
  Images are immutable.
- **File URLs are never listed** on the main page — you only get them from the
  upload response. The page shows live stats instead.

## 🤖 For agents

Throway is built to be consumed by other programs. When an agent hits the root
URL it's served the full "for agents" instructions directly. There's also a
machine-readable contract:

```bash
curl "https://lubu.skale.dev/throway/api"
```

See **[`AGENTS.md`](AGENTS.md)** for the complete agent guide.

## 🛠️ Deploy

| Piece | How |
|-------|-----|
| Server | single-file Python stdlib: [`store.py`](store.py) — no dependencies |
| Service | systemd `throway-store.service` (port `8111`, auto-start/restart) |
| Proxy | nginx `/throway/` location |
| Storage | `/srv/storage2/throway/` (USB HDD) |

```bash
# run it anywhere
python3 store.py
```

## 📚 Docs

- **[`AGENTS.md`](AGENTS.md)** — guide for agents & programs
- **[`API.md`](API.md)** — full API reference
- **[`PRD.md`](PRD.md)** — product requirements

## 📄 License

[MIT](LICENSE) © 2026 devskale
