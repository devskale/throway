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

- **Daten-Root ≠ Deploy-Root auf lubu** (Retro 2026-10-02, hard-validate):
  Code liegt in `/var/www/store`, Daten in `/srv/storage2/throway`
  (Code-Default von `THROWAWAY_ROOT`). ssh-Aufräumen am falschen Ort
  läuft *still ins Leere* (rm erfolgreich, Objekt lebt weiter) — erst
  `systemctl cat`/`find` nach dem echten ROOT, dann rm.
- **Retain-Token-Verlust** (Retro hard-validate): das Token steht
  shuttle-frei im Drop-in — `ssh lubu 'sudo cat
  /etc/systemd/system/throway-store.service.d/retain.conf'`. Die /tmp-Kopie
  stirrt beim Reboot; ohne Token sind retained Objekte nur per ssh administrierbar.
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

## Browser-Tests: Fallen, die jede Runde Zeit kosten

- **Browser-Assertionen mit funktionalen Messwerten statt Screenshots** —
  `rodney screenshot` wartet auf network-idle, das externe Embeds nie
  erreichen. Bei iframe-Seiten: `scripts/browser-probe.py`
  (`emulate`/`open`/`eval`/`marker`/`net`) statt eigener CDP-Skripte.
- **Harte Reloads & Marker im GELADENEN DOM prüfen, bevor du eine
  Browser-Aussage interpretierst.** Chrome serviert gecachte Seiten
  (zwei Runden Debugging gekostet). Der Marker muss dabei exakt der
  sein, der auch im ausgelieferten HTML steht — `offset:o` statt
  `offset: o` findet nichts und führt zu falschen Diagnosen.
  `browser-probe.py marker <frame> <expr>` macht genau das (Exitcode).
- **`src` + `loading=lazy` schlägt `srcset`:** Chrome lädt beim
  Erst-Load das `src`-Attribut und wertet `srcset` erst bei Re-Layout
  neu — ohne `src` lädt lazy gar nichts. Für scharfe Thumbnails ist
  `src` deshalb die *größte* sinnvolle Kandidatin (1.39.2), nicht die
  kleinste.
- **Auto-Height-iframes haben keinen sichtbaren Viewport:**
  `innerHeight`/`vh`/`dvh`/IntersectionObserver referenzieren die
  *gesamte* Inhaltshöhe. Wer dort Layout-Entscheidungen trifft,
  braucht den Host-Handshake (`throway:pics:viewport`).

## Code structure

- New feature = new module in `throway/` (see `pics.py`, `mdrender.py`)
  with a small interface: route dispatch, `sweep`, `api_endpoints`,
  `HELP_TOPICS`. `store.py` gets minimal touchpoints; the module owns
  everything else. Monolith edits stay surgical.
- **Cross-Cutting vs. Namespace-Module** (Retro review 1.45.4): die
  Modul-Regel gilt voll fuer Namespace-Features (pics: eigene Route,
  eigener Pool). Querschnitts-Themen (retain) dürfen store.py-Touchpoints
  behalten, solange die Konzept-Logik (Token-Validierung, Gates, HELP,
  Limits) im Modul lebt — Flip-Routen und Sweep-Checks sind Dispatcher-
  Nähe, keine Konzept-Logik.
- Optional deps (`PIL`, `pillow_heif`) import lazily inside functions —
  the server must start without them.
- **Kein Create-Pfad ohne Delete-Pfad** (Retro 2026-10-02, hard-validate):
  jede Erzeugungsroute braucht eine *live geprüfte* Löschroute. Bundles
  waren seit 1.0 per API unlöschbar (DELETE prüfte nur `isfile`), die
  Doku behauptete das Gegenteil — mit Retention wurden sie immortal
  orphans. Eine neue Objektart oder Lifetime einzubauen heißt: den
  Gegenpfad im selben Commit testen.
- **Nach Guard-Mutation Meta neu laden** (Retro 2026-10-02, hard-validate):
  wenn ein Guard neben der Prüfung Meta auf Disk ändert (write implies
  retention), müssen Caller ihr In-Memory-Meta verwerfen — sonst
  überschreibt der nächste `json.dump` den Flip still (`_share_store`).
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

- **Patch-Scripts validieren vor dem Schreiben** (Retro 2026-10-01, P1):
  Anker prüfen → ersetzen → **im Speicher kompilieren** → erst dann
  atomisch schreiben. `scripts/patch.py` macht genau das (Import
  `from patch import apply`); ein Script, das erst schreibt und dann
  kompiliert, hinterlässt bei einem Syntaxfehler ein kaputtes Modul
  (passiert: 1.39.0 Grace-Period).

- Edits in store.py / pics.py (rich in —, ä, ⌘, ⌘): use the python-script
  route (`assert old in src` + `replace` + `py_compile`) BY DEFAULT; the
  edit tool only for pure-ASCII anchors. Rule-of-record: this rule was
  written after violating its weaker form five times — anchor failures
  cost more round-trips than scripts ever will.
- Edit calls are atomic — one bad anchor applies nothing. Verify with a
  grep after any multi-edit.
- Validate generated JS by extracting the rendered page's scripts and
  `node --check`-ing them; HTML markup via grep on the live response.
  **Automated since 1.43.3** — `tests/test_pics.py`
  (`test_js_constants_are_str` + `test_rendered_scripts_node_check`)
  assert the JS-carrying constants stay `str` and every rendered `<script>`
  parses. CI runs them; the manual step is now a safety net, not the gate.
- **CSS-Klammer-Balance** (Retro 2026-10-02): Verwaiste/doppelte `}` legen
  die FOLGENDE Regel still weg (2× passiert). **Automated** —
  `scripts/release-check.sh` prüft die `_*_CSS`-String-Inhalte in
  `pics.py` auf `{`/`}`-Balance (Kommentare + Strings ignoriert). Läuft bei
  jedem Release; der manuelle Balance-Check ist damit überflüssig.

## Process

- **Fail early**: prove the smallest step first (one API call before a
  batch, one rendered page before a suite).
- **rodney runs on a shared Chrome.** Before interpreting any eval chain,
  `rodney url` once — other agents navigate the same instance, and evals
  on a foreign page read as mysterious failures (three misread rounds
  this session).
- **Start each browser flow from a clean page.** localStorage persists
  across evals on the same page — a click that "doesn't take" is often a
  prior eval that already mutated state (or pre-populated the key).
  Reset the relevant localStorage keys and re-open the page before
  asserting a flow (Retro 1.43.2, P4).
- **Functional assertions over screenshots** on iframe-heavy pages:
  `rodney screenshot` waits for network-idle, which external embeds never
  reach. Measure the DOM (`getBoundingClientRect`, counts, flags) —
  numbers, not pixels.
- **Harte Live-Validierung vor Review** (Retro 2026-10-02, hard-validate):
  nach Feature-Deploys einen adversarialen Pass fahren — nicht nur happy
  path: „kann man das erzeugte Objekt wieder loswerden?", leere/falsche
  Credentials, Flag-Kombinationen, HTML-Injection-Namen, Write-Gates an
  ALLEN Routen (auch share/import). Der Pass fand 2 echte Bugs, die 127
  grüne Tests nicht sahen.
- **Live-Validierer-Hygiene**: eindeutige Objektnamen pro Run (create-or-get
  revealt write_token nie wieder → Run 2 crasht) und Cleanup via
  atexit/finally — Crash vor Cleanup hinterlässt *retained* Orphans, die
  nur per ssh sterben.
- **HTTP/2-Proxies lowercases Response-Header** (`X-Expires` → `x-expires`):
  Header-Assertionen case-insensitiv lesen, sonst False-Negative-FAILs.
- **Unmatched POST-Pfade enden im Raw-Upload** (Retro 2026-10-02, hard-validate R2):
  jeder POST, den keine Route reklamiert, wird zum Datei-Upload — mit
  Token sogar zu einer *retained* Muell-Datei mit 200 (`POST /d?retain=1`).
  Neue Routen/Flags muessen reservierte Namen explizit abweisen und duerfen
  sich nicht darauf verlassen, dass "die naechste Route es ablehnt".
- **UA-gesplittete Seiten bei Marker-Checks** (Retro R2): die Homepage
  liefert curl Agent-Text, Browsern das JS-UI — Marker gegen das
  Browser-HTML nur mit Browser-User-Agent curlen (sonst False-Negative).
- **Rodney am geteilten Chrome: Flows als EINE async-Expression**
  (Retro R2): input→click→wait→DOM-Lesen in einem Aufruf kombiniert
  (kein Diebstahlsfenster zwischen Kommandos); row()-Label und -Wert
  landen auf getrennten innerText-Zeilen (Regex ueber \n). Bleibt ein
  Flow trotz 3 Versuchen unbestimmt: ehrlich als offen dokumentieren —
  Marker + node-check + Pfade-Analyse decken bis dahin, kein Fake-Pass.
- **Prove features with real data once** — the ~/Pictures upload
  surfaced the `%40`-encoding bug that every synthetic test missed.
- Long tasks run in a Herdr pane with `tee` into a log; the heartbeat
  message names logfile + pane + what "good" looks like.

## Release checklist (the gate)

```
[ ] tests green — confirmed via ${PIPESTATUS[0]}, not a piped echo
[ ] scripts/validate-live.py green (Spawner-Modus; live nach Deploy nochmal mit --base)
[ ] browser feature? -> rodney flow that triggers it (not markup-grep)
[ ] scripts/release-check.sh green (version + docs drift; CI runs it too)
[ ] VERSION + RELEASES.md + docs (api/help/API.md/AGENTS/README) current
[ ] committed with (x.y.z), pushed
[ ] rsync (no trailing slash) + restart + is-active
[ ] live /api version + one smoke of the new behavior
[ ] heartbeat started (2–3 ticks), then done
[ ] issue (if any) → review or DONE
```
