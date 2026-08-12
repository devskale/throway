# throway — Releases

**Current version:** `1.3.0`

A disposable file store. Upload a file — or a bundle of files (e.g. a
website) — and get a short-lived URL. No auth. Nothing permanent.

---

## 1.3.0 — 2026-08-12

### Fixed
- **Per-IP rate limiting.** The rate limiter now keys on the real client IP
  (via `X-Forwarded-For` / `X-Real-IP` set by nginx) instead of the socket
  peer, which behind nginx was always `127.0.0.1`. Previously the whole
  service shared one 100 req/min bucket — a single busy agent could exhaust
  it for everyone. Each real IP now gets its own bucket.
- **Memory-safe bundle downloads.** The bundle zip is streamed to a temp file
  and out in chunks instead of being built entirely in RAM. A bundle near the
  pool limit no longer allocates ~100 MB of memory per request.
- **Total bundle size cap.** A bundle can no longer exceed the 100 MB pool —
  returns `413` during upload instead of briefly blowing past the limit.

## 1.2.0 — 2026-08-12

### Fixed
- **Bundle MIME sniffing.** Bundle parts now get their content type from the
  file extension (like single-file uploads), so `.css`, `.js`, `.svg`, etc.
  serve correct MIME types instead of `application/octet-stream`. Browsers no
  longer block stylesheets/scripts in styled bundles.
- **HEAD requests.** `HEAD /throway/<id>` (and other routes) now return `200`
  with correct headers and `Content-Length` and no body, instead of `501`.

## 1.1.0 — 2026-08-12

### Added
- **Bundles.** Upload 2+ files in one multipart `POST` to create a bundle under
  a single URL. Browsers get `index.html` rendered inline (a real throwaway
  website); agents get a zip; each file is served at `/throway/<id>/<file>`.
  The whole bundle shares one 4-hour expiry and is evicted as one unit.
- **Inline rendering for text-like types.** Images plus text, HTML, JSON, PDF,
  SVG, and JavaScript now render inline in the browser; `?download=1` forces a
  download. Text responses include `charset=utf-8`.

### Fixed
- **Base URL.** Dropped the dead `:8001` port; the service is now reached at
  `https://lubu.skale.dev/throway`.

## 1.0.0 — 2026-08-10

### Added
- Initial release. A disposable, open, short-lived file store.
- Upload a single file (raw body or multipart) → 4-hour URL.
- Images render inline; other files download; `?download=1` forces download.
- Text files editable via `PUT` (replace) and `PATCH` (append); images immutable.
- Rolling 100 MB pool (oldest evicted first), 5 MB max file, 100 req/min per IP.
- Machine-readable contract at `/api` and agent descriptions at
  `/write_for_agents` and `/copy_for_agents`.
