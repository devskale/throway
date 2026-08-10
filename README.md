# throway

A disposable file store. Upload a thing, get back a short-lived URL. No auth, nothing permanent.

## What it is
- Share a file (image, text, binary) by giving someone a URL
- Text scratchpad: create a note, append to it, rewrite it
- Pass data between agents / machines without setting up accounts

## Quick start
```bash
# upload
curl -X POST --data-binary @photo.png "https://lubu.skale.dev:8001/throway/?name=photo.png"

# download / view
curl "https://lubu.skale.dev:8001/throway/<id>"

# edit text (replace / append)
curl -X PUT   --data-binary "new text" "https://lubu.skale.dev:8001/throway/<id>"
curl -X PATCH --data-binary "append this" "https://lubu.skale.dev:8001/throway/<id>"

# delete
curl -X DELETE "https://lubu.skale.dev:8001/throway/<id>"
```

## Limits
| Limit | Value |
|---|---|
| URL lifetime | 4 hours |
| Max file size | 5 MB |
| Pool size | 100 MB (oldest evicted first) |
| Rate limit | 100 req/min per IP |

## Endpoints
| Method | Path | Action |
|---|---|---|
| POST | `/throway/?name=<file>` | upload (raw body or multipart) |
| GET | `/throway/<id>` | download / view |
| GET | `/throway/<id>?download=1` | force download |
| PUT | `/throway/<id>` | replace text (text only) |
| PATCH | `/throway/<id>` | append text (text only) |
| DELETE | `/throway/<id>` | delete |
| GET | `/throway/api` | JSON contract |
| GET | `/throway/write_for_agents` | agent description (plain text) |
| GET | `/throway/copy_for_agents` | copyable description (HTML) |

## Deploy
- Single-file Python stdlib server: `store.py` (no dependencies)
- systemd: `throway-store.service` (port 8111)
- nginx: `/throway/` proxy
- Storage: `/srv/storage2/throway/` (USB HDD)

See `PRD.md` and `API.md` for details.
