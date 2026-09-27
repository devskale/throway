# Coding Rules — throway

Distilled from the 1.19→1.29 session. Each rule beat a real failure.
Read before changing code; the checklist at the end gates every release.

## Release (Bumppush)

1. `VERSION` in `store.py` up, `RELEASES.md` entry on top (date, what,
   why, test count). Minor = feature, patch = bugfix, major = breaking.
2. Same-commit doc sweep — drift is a release blocker, not a follow-up:
   the `/api` spec (in code), HELP topics (in code), `API.md`,
   `AGENTS.md`, `README.md`. API.md once silently lost whole sections in
   a rewrite; the validation only caught it weeks later in review.
3. Commit subject carries the version: `1.30.0: …`. Push before deploy.

Done = version bumped, note written, all five doc surfaces current, pushed.

## Deploy

```bash
rsync -a store.py lubu:/var/www/store/
rsync -a --delete throway lubu:/var/www/store/   # NO trailing slash on throway/
```

A trailing slash on the package dir flattens its contents into
`/var/www/store/` — happened, service failed its own smoke.

- Secrets live in the systemd drop-in
  (`/etc/systemd/system/throway-store.service.d/`), never in the repo.
- Verify, in order: `grep ^VERSION` on lubu, `systemctl is-active`,
  live version via `/api`, one smoke of the actual feature, then a short
  heartbeat (2–3 ticks, 60–90 s). Blocking `wait_output` on a pane is
  the anti-pattern; the heartbeat message names the checks and "good".

- Before interpreting any browser assertion after a deploy: hard-reload
  and verify the NEW marker in the loaded DOM (a function/ID this release
  introduced). Chrome served stale cache twice and cost two debugging
  rounds on already-correct code.

Done = active + live version correct + feature smoke green + heartbeat started.

## Tests

- Behavior-level, through HTTP, against the real `store.py` as a
  subprocess with a tmp `THROWAWAY_ROOT` (see `tests/conftest.py`).
  These survive refactors; unit tests only where pure (mdrender).
- After any piped pytest run, read `${PIPESTATUS[0]}` — `echo $?` after
  a pipe reports `tail`'s exit, and a hung suite behind `timeout` reads
  as green. This cost an hour: an infinite loop shipped as "passed".
- Hung run drill: `PYTHONFAULTHANDLER=1`, wait, `kill -ABRT <pid>` →
  full stack dump names the loop. `py-spy` needs root on macOS. Then
  `pkill -9 -f "store.py"` — zombies from killed runs poison the next.
- Every feature ships with its tests in the same commit. Suite green is
  a deploy precondition.
- **Browser features get a browser test.** Anything with JS behavior
  (handlers, fetch flows, paste/drop) is proven by a rodney flow that
  actually triggers it (click / paste event / dispatch) — markup-grep and
  curl (= agent UA) mask exactly the bugs users hit. Two shipped that way
  (create-HTML-to-JS-fetch, paste Illegal invocation); both user-found.

## Code structure

- New feature = new module in `throway/` (see `pics.py`, `mdrender.py`)
  with a small interface: route dispatch, `sweep`, `api_endpoints`,
  `HELP_TOPICS`. `store.py` gets minimal touchpoints; the module owns
  everything else. Monolith edits stay surgical.
- Optional deps (`PIL`, `pillow_heif`) import lazily inside functions —
  the server must start without them.
- The HELP dict is the single source for all agent copy; the root agent
  description assembles from it automatically. One meaning, one place.
- All limits env-tunable (`THROWAWAY_*`), defaults in code.

## Security invariants

Non-negotiable; each guards a real hole found this session:

- **Secrets**: constant-time compare (`hmac.compare_digest`), shown
  exactly once at creation, create-or-get never re-reveals, wrong
  secret answers 404 (no enumeration).
- **User content → HTML**: escape first, format second; link schemes
  restricted to http/https/mailto/relative/anchor; code spans protect
  their content from inline formatting.
- **Public storage leaks no metadata**: EXIF/GPS stripped on every
  path, including keep-original ones (lossless JPEG segment surgery).
- **Pools**: persistent pools (pics) reject at capacity with 507; LRU
  eviction is exclusive to the throwaway pool. `total_size()`,
  `_units()`, `evict()` must never see pics entries.
- Admin URLs carry their secret as a path segment; write tokens as
  header or documented query. Assume query strings reach logs.

## Editing discipline

- Edits in store.py / pics.py (rich in —, ä, ⌘, ⌘): use the python-script
  route (`assert old in src` + `replace` + `py_compile`) BY DEFAULT; the
  edit tool only for pure-ASCII anchors. Rule-of-record: this rule was
  written after violating its weaker form five times — anchor failures
  cost more round-trips than scripts ever will.
- Edit calls are atomic — one bad anchor applies nothing. Verify with a
  grep after any multi-edit.
- Validate generated JS by extracting the rendered page's scripts and
  `node --check`-ing them; HTML markup via grep on the live response.

## Process

- **Fail early**: prove the smallest step first (one API call before a
  batch, one rendered page before a suite).
- **rodney runs on a shared Chrome.** Before interpreting any eval chain,
  `rodney url` once — other agents navigate the same instance, and evals
  on a foreign page read as mysterious failures (three misread rounds
  this session).
- **Functional assertions over screenshots** on iframe-heavy pages:
  `rodney screenshot` waits for network-idle, which external embeds never
  reach. Measure the DOM (`getBoundingClientRect`, counts, flags) —
  numbers, not pixels.
- **Prove features with real data once** — the ~/Pictures upload
  surfaced the `%40`-encoding bug that every synthetic test missed.
- Long tasks run in a Herdr pane with `tee` into a log; the heartbeat
  message names logfile + pane + what "good" looks like.

## Release checklist (the gate)

```
[ ] tests green — confirmed via ${PIPESTATUS[0]}, not a piped echo
[ ] browser feature? -> rodney flow that triggers it (not markup-grep)
[ ] scripts/release-check.sh green (version + docs drift; CI runs it too)
[ ] VERSION + RELEASES.md + docs (api/help/API.md/AGENTS/README) current
[ ] committed with (x.y.z), pushed
[ ] rsync (no trailing slash) + restart + is-active
[ ] live /api version + one smoke of the new behavior
[ ] heartbeat started (2–3 ticks), then done
[ ] issue (if any) → review or DONE
```
