# throway — Releases

**Current version:** `1.45.2`

A disposable file store. Upload a file — or a bundle of files (e.g. a
website) — and get a short-lived URL. No auth. Nothing permanent.

---

## 1.45.2 — 2026-10-02

### Bugfix: Data-Loss-Race in der Dir-Maschinerie (Retro hard-validate, Runde 2)

12 parallele Adds auf ein Dir: alle 200, aber **3 Files verschollen,
Meta kannte nur 2, History nur 1 Eintrag**. Präexistent (Dir-Maschinerie),
durch Show-Dirs zum Alltagsrisiko (offener Write = parallele Schreiber
by design). Drei gestapelte Ursachen:

1. **Nicht-atomare Meta-Schreiben**: `json.dump` direkt aufs Ziel —
   concurrent Reader sahen halbe Dateien → `_dir_meta` = None.
2. **`_dir_add`-None fiel durch do_POST** in den Raw-Upload-Pfad: aus
   einem Dir-Add wurde *still ein Einzel-Upload mit 200* (daher schienen
   alle Adds "erfolgreich").
3. **Read-modify-write-Races** auf Meta `files`-Map und History
   (last-writer-wins).

Fix: `_atomic_json()` (tmp + `os.replace`, wie schon idem-map) für alle
14 Persistenz-Schreiben; `_dirlock`-Decorator (`RLock`) auf alle
Dir-Mutatoren; `/d/<key>`-POST endet bei None mit 404 statt Fall-through.

Tests: +2 (`test_parallel_adds_no_loss` mit 12-Thread-Hammer und
Dir-Antwort-Assertion je Add, `test_dir_add_missing_dir_404`);
Suite 129/129 grün.

---

## 1.45.1 — 2026-10-02

### Bugfixes aus der harten Live-Validierung (Retro hard-validate)

1. **Bundles waren per API unloeschbar** (praekexistent, Doku behauptete
   das Gegenteil): `DELETE /<id>` pruefte nur `isfile` — Bundles sind
   Dirs → 404, seit 1.44.0 auch mit Token. Mit Retention akut: retained
   Bundles waren immortal orphans (nur per ssh entfernbar). Fix: Bundle-
   Delete mit Retain-Gate. Regel daraus: **kein Create-Pfad ohne
   Delete-Pfad**.
2. **„Write implies retention" versagte bei `?share=`**: `_share_store`
   rief den Write-Guard nur bei `write_token`-Dirs; Token-Adds auf
   normalen Share-Dirs flippeden nicht. Fix: Guard immer + Meta-Neuladen
   danach (der Flip landete auf Disk, aber `_dir_write_files` schrieb das
   stale In-Memory-Meta zurueck).

Benigne Kanten (dokumentiert, kein Fix noetig): leerer Bearer zaehlt als
kein Token (Wegwerf-Upload); falscher Query-Token ohne Retention-Wunsch
→ fail-closed 401; HTTP/2 lowercased Response-Header (Validierung
case-insensitiv lesen).

Tests: +2 (`test_bundle_deletable_and_retained_gate`,
`test_share_token_add_implies_retention`); Suite 127/127 grün.

---

## 1.45.0 — 2026-10-02

### Feature: Show-Dirs — permanent + öffentlich beschreibbar (`&show=1`)

Issue throway-show-dirs (User-Spec): eine per Retain-Token **einmal
generierte** Dir, danach **unbegrenzt** nutzbar, und **jeder mit der URL
darf read+editen** — beliebige Dateitypen. Das Kollaborations-Pendant zu
1.44.0: reteniert (stabil) aber mit offenem Write-Modell.

- `POST /?dir=1&show=1[&name=<slug>]` + Token → Meta `{retain: true,
  open: true}`: nie expiry/eviction, öffentlich beschreibbar.
- `POST /d/<key>?show=1` + Token flippt Bestands-Dirs (idempotent);
  `show=1` auf Nicht-Dirs → 400.
- File-Level-Ops (add/edit/delete) ohne Token; **Whole-Dir-Delete nur mit
  Token** (schützt das Retention-Versprechen — sonst tötet jeder Joker
  die Show).
- `write_token` (falls bei Create gesetzt) hat weiter Vorrang vor `open`.
- Dir-Responses carry `"open": true`; History läuft mit (Accountability
  ohne Auth). `/api`: `show_dir`-Endpunkt, `&show=1` im create_dir-Note;
  `/help/retention` um SHOW-DIRS-Sektion erweitert.

Tests: `tests/test_show.py` (10 neue); Suite 125/125 grün.

---

## 1.44.0 — 2026-10-02

### Feature: Token-gated indefinite Retention (`retain`)

Issue throway-indefinite-token (from mac@skale-skills): Skill-Installs und
stabile Share-URLs brauchen URLs, die nicht nach 4h/7d sterben — ohne
zweiten Dienst. Lösung: ein serverseitiges **Retain-Token**
(`THROWAWAY_RETAIN_TOKEN`, Env, komma-getrennte Liste möglich; leer =
Feature aus) spaltet Unbeschränktheit vom Wegwerf-Prinzip ab:

- Token-autorisierte Uploads (`Authorization: Bearer <t>` oder dokumentiertes
  `?token=`) erzeugen Objekte **ohne Ablauf**: `expires_at: null`,
  `persistence.retention: "indefinite"`. Gilt für Singles, Bundles, Dirs,
  `?share=` und `?url=`-Importe.
- `POST /<id>?retain=1` (bzw. `/d/<key>?retain=1`) mit Token flippt
  **Bestandsobjekte** auf unbeschränkt (idempotent).
- Token-autorisierte `PUT`/`PATCH`/Dir-Writes machen das Ziel unbeschränkt
  — *write implies retention*.
- **Public read, token-gated write**: Lesen bleibt URL-basiert offen;
  Schreiben/Löschen auf retained Objekten braucht das Token (401).
- `&once=1` + Retention → 400.
- Sweep **und** Eviction überspringen retained Units (`retain`-Flag im
  Meta/Manifest); bei Pool-Druck weichen nur Disposable-Units.

Implementierung: neues Modul `throway/retain.py` (Token-Config,
constant-time-Vergleich, `request_retention`/`write_denied`, HELP-Topic
`retention`); store.py bekommt minimale Touchpoints (Upload-Pfade, Write-
Gates, `_meta_expired`/`_meta_retained`/`_dir_retain`-Helper, Listings
null-sicher). `/api`: `retention_token`-Limit, `retain`-Endpunkt,
`expires_at: str|null`-Schema; `/help/retention`. Sicherheits-Invarianten
gehalten: `hmac.compare_digest`, 401 statt Enumeration, Query-Token als
dokumentierter (Log-Warnhinweis), Header bevorzugt.

### Bugfix mitgeritten: Idempotenz-Key war still tot (seit 1.42.x)

Beim Retention-Umbau fiel F821 auf: `hashlib` war nie importiert,
`_read_json` nie definiert, und `_idem_put` wurde **nie aufgerufen** —
die try/except-Blöcke fraßen die NameErrors, der Replay lieferte
stillschweigend immer neue IDs. Es gab keinen Test dafür. Gefixt:
Import + `json.load` + Persistenz-Hook in `_send` (erste 200-JSON-
Antwort unter dem Key); Regressionstest `test_idempotency_replay`.

Tests: `tests/test_retain.py` (19 neue, HTTP-Level + in-process
sweep/evict-Immunität) + `test_idempotency_replay`; Suite 115/115 grün.

---

## 1.43.2 — 2026-10-02

### Bugfix (P4): RECEIVED-Stern lässt sich abwählen

Randfall aus dem Review (pics-star-received-abwaehlen): Ein Empfänger,
der ein Foto selbst gestarrt hat UND das Foto im geteilten `RECEIVED`-Set
liegt, konnte den Stern nicht aus seiner Auswahl entfernen — er blieb
gefüllt (wegen `RECEIVED`), obwohl er aus dem localStorage entfernt war.
Rein visuell, kein falsches Teilen.

Behoben über ein **session-lokales `DESEL`-Set** (nie in localStorage):
- `paintStar` blendet `RECEIVED` für ein abgewähltes Bild aus:
  `on = (starmine()[pid] || RECEIVED[pid]) && !DESEL[pid]`.
- Klick auf einen Stern, der im `RECEIVED`-Set liegt, setzt `DESEL[pid]`
  → der Stern wird leer, obwohl das Bild im geteilten Set bleibt.
- Die Empfänger-Markierung wird weiterhin **nie** in localStorage
  geschrieben (P1/P3-Invariante) — `shareSet()` baut die Auswahl nur aus
  eigenen Sternen und nimmt ein abgewähltes Foto nicht in die URL auf.
- Gilt für Grid **und** Lightbox (`#lbsst` nutzt `paintStar`).

Browser-Flow (rodney/CDP) verifiziert: Empfänger mit eigenem Stern +
`RECEIVED` kann den Stern abwählen (Grid + Lightbox), localStorage wird
geleert, `shareSet()`-URL enthält das Foto nicht mehr; fremde
`RECEIVED`-Sterne werden nie übernommen.

Suite: 93 Tests grün, ruff grün.

---

## 1.43.1 — 2026-10-02

### Bugfix (Review P1): Empfänger-Markierung im ?stars=-Link

Review-Befund (pics-star-set-teilen): Der geteilte Link sortierte die
gestarrten Fotos zwar korrekt zuerst, aber ein Empfänger ohne eigene
Sterne sah sie **nicht markiert** — die Markierung hing nur am
localStorage des aktuellen Besuchers. Kernanforderung war „derselbe
View“. Behoben:

- **P1:** Der Server injiziert die URL-PIDs als `RECEIVED`-Set ins JS;
  `paintStar` markiert eine Zelle, wenn sie im eigenen `starmine()`
  **oder** im `RECEIVED`-Set liegt. Der Empfänger sieht die geteilten
  Fotos jetzt mit gefülltem Stern — auch ohne sie selbst gestarrt zu
  haben.
- **P1/P3:** Die Empfänger-Markierung wird **nie** in localStorage
  geschrieben — der „Als Set teilen“-Button baut seine Auswahl weiterhin
  nur aus den eigenen Sternen, übernimmt also keine fremden.
- **P2:** Toten `STARS`-Regex entfernt (war nie verwendet); das
  `RECEIVED`-Set kommt aus der bereits injizierten `__STARS__`.
- **CSS:** `.grid a[data-star]` als statische Markierung ergänzt
  (vorher nur `.starred`, das ebenfalls nur via localStorage gesetzt
  wurde).

Browser-Flow (rodney/CDP) verifiziert: frischer Empfänger sieht die
getrennten Fotos markiert (Grid + Lightbox), localStorage bleibt leer,
Share-URL enthält nur eigene Sterne.

Suite: 92 Tests grün, ruff grün.

---

## 1.43.0 — 2026-10-02

### Feature: Fotos starren & als Set teilen (pics-star-set-teilen)

Galerie-Gäste können Lieblingsfotos starren und sie als Set über Social
Media teilen — ohne Server-State. Star ist bewusst etwas anderes als
Like: Like ist öffentlich (gezählt, rankt die Galerie), Star ist eine
**private** Auswahl zum Teilen.

- **Star = rein clientseitig** (localStorage `ty_stars_<gid>`): kein
  Server-State, keine Rate-Limits, keine Cleanup-Pflicht, kein
  „wer darf das Set löschen“-Problem. Analog zu den bestehenden
  Browser-Likes `ty_likes_<gid>`.
- **Zustandsloser Link statt Set-Objekt:**
  `/pics/g/<gid>?stars=<pid1>,<pid2>,<pid3>` — starrer PIDs zuerst (in
  Link-Reihenfolge), dann der Rest in gewohnter Ordnung (`?sort=likes`
  rankt weiterhin den Rest). Unbekannte/versteckte/abgelaufene PIDs
  werden still übersprungen — der Link 404t nie.
- **Browser-UI:** Stern-Button an jedem Thumb + im Lightbox (toggelt
  localStorage), markierte Zellen mit gefülltem Stern, Hinweis über dem
  Grid („★ Sterne deine Lieblingsfotos und teile sie als Set.“) und ein
  „Als Set teilen“-Button mit Zähler (sichtbar ab ≥ 1 Stern) via
  `navigator.share`/Copy-Fallback. Sterne überleben Reloads.
- **Agent-JSON** spiegelt die Auswahl als `selected: [pid, …]`.

Browser-Flow (rodney/CDP) verifiziert: Stern-Klick → localStorage →
Zähler → Share-URL → Reload-Persistenz → Empfänger sieht starrer-zuerst
mit gefüllten Sternen; Lightbox-Stern synchronisiert mit dem Grid.

Suite: 91 Tests grün, ruff grün.

---

## 1.42.0 — 2026-10-02

### P1 (SOTA-Review): Strukturierte Fehler + Retry-After

Fehler waren Status + Prosa (`{"error":"not found"}`); ein Agent
musste raten, ob „gleich nochmal" (429) oder „falscher Aufruf" (400)
oder „Pool voll" (507) gilt — und wusste nie, wie lange er warten
soll.

- **`code`-Feld in jeder JSON-Fehlerantwort** (stabile Maschinen-Codes:
  `bad_request`, `write_denied`, `not_found`, `length_required`,
  `too_large`, `rate_limited`, `pool_full`, `server_error`). 11 text/plain-
  404-Antworten auf strukturiertes JSON umgestellt, damit *jede*
  Fehlerantwort maschinenlesbar ist.
- **`Retry-After`-Header + `retry_after`-Feld**: 429 → 60s (Rate-Fenster),
  507 pics-Pool → 300s. Ein Agent wartet jetzt, statt zu raten.

Suite: 87 Tests grün, ruff grün. Nächster Punkt: P2 (Idempotenz).

---

## 1.41.1 — 2026-10-01

### Koordinations-Guardrail: Bumppush gegen parallele Sessions absichern

Der Vorfall aus 1.40.3 war nicht die Ursache, sondern die Symptom-
huelle: Zwei Sessions im selben Working-Tree haben sich die Versions-
kette gegenseitig überschrieben, ohne es zu merken. Der Übeltäter war
fehlende Koordination — also mechanisch gelöst statt dokumentiert:

- **`scripts/bumppush-check.sh`:** Ein Bumppush ist nur erlaubt, wenn
  `<handoff>/issues/active/` leer ist (kein fremder Agent arbeitet) oder
  der eigene Claim-Slug im Commit-Betreff steht. Verweigert sonst mit
  klarer Meldung (inkl. Hinweis auf stale Claims).
- **In `release-check.sh` verdrahtet**, läuft also in jedem Release
  und in CI; ohne Handoff-Struktur (CI-Runner) wird er übersprungen.
- **Regel in `AGENTS.md`** beim Bumppush-Abschnitt: wer an throway
  arbeitet, legt einen Claim in `.handoff/issues/active/` an. Der
  Mechanismus ist das vorhandene issues-Kanban (`issues start <slug>`),
  keine neue Infrastruktur.

Suite: 86 Tests grün, ruff grün.

---

## 1.41.0 — 2026-10-01

### Retro-Nachzug: FD-Lecks geschlossen, Repro ohne Proxy, Browser-Fallen dokumentiert

Die restlichen Retro-Punkte (P5/P6) — diesmal mit **echtem Runtime-Fund**:

- **FD-Lecks im Server-Code (P6):** `json.load(open(...))` an 15 Stellen
  in `store.py`/`pics.py` ließ das Handle bis zum GC offen. Unter
  Thumb-Last (1.40.0 hat den Warmer eingeführt) konnte das
  Datei-Deskriptoren aufbrauen. Alle auf `with open(...)` umgestellt —
  **Semantik unverändert** (die umgebenden try/except bleiben, ein
  erster Versuch mit einem `_read_json`-Helper änderte das
  Exception-Verhalten und brach 31 Tests — zurückgenommen).
  Der Test-Harness schloss sein `server.log` ebenfalls nie (117
  ResourceWarnings → 0 aus dem Harness).
- **`THROWAWAY_PREFIX` (P6):** Der URL-Prefix der generierten Links ist
  jetzt Env. Lokal `THROWAWAY_PREFIX=` → alle Links zeigen auf denselben
  Origin, **Browser-Tests brauchen keinen Reverse-Proxy mehr** (vorher
 _proxy.py_ pro Session). Prod unverändert (`/throway`).
- **CODING_RULES (P5):** Abschnitt „Browser-Tests: Fallen" — harte
  Reloads, Marker im geladenen DOM **exakt** prüfen, Chromes
  `src`+`loading=lazy` schlägt `srcset`, Auto-Height-iframes haben
  keinen sichtbaren Viewport. Plus Verweis auf `scripts/browser-probe.py`.

Suite: 86 Tests grün (`-W error::ResourceWarning` ohne Fehlschlag),
ruff grün.

---

## 1.40.3 — 2026-10-01

### Retro-Umsetzung: Agent-Umgebung gehärtet (P1–P4, nachgezogen)

Versions-Hinweis: Diese Änderungen landeten zunächst als Commit mit
Betreff „1.39.3" — **Kollision mit einer parallelen Session** (die
zwischenzeitlich 1.39.3/1.40.0–1.40.2 gelandet hatte; der Thumb-Warmer
1.40.0 baut direkt auf dem srcset aus 1.39.2 auf). Nachgezogen als
1.40.3, damit Commit-Betreff und VERSION wieder zusammenpassen.

Inhalt (kein Runtime-Code):

- **`scripts/patch.py` (P1):** Anker prüfen → ersetzen → im Speicher
  kompilieren → erst dann atomisch schreiben (der 1.39.0-Patch hatte
  `pics.py` mit SyntaxError auf der Platte hinterlassen). CODING_RULES
  um die Reihenfolge ergänzt.
- **`scripts/browser-probe.py` (P2):** Ein CDP-Helfer statt fünf
  Wegwerf-Scripts pro Session — emulate/open/eval/marker/net gegen den
  rodney-Chrome, legt fehlende Page-Targets selbst an. Verifiziert:
  Emulation 390×844@3x, Frame-eval, Marker-Exitcodes, Thumb-Netzwerk-
  Mitschnitt.
- **AGENTS.md entwirrt (P3):** Bumppush-Absatz sagte zweimal
  Gegenteiliges (Minor- vs. Patch-Default), rsync-Zeile widersprach
  CODING_RULES. Jetzt eine Semantik-Policy + Verweis auf den
  CODING_RULES-Deploy-Befehl.
- **ruff in CI (P4):** eigener `lint`-Job (E9+F), Kalibrierung in
  `ruff.toml`; 9 Auto-Fixes + 2 tote Test-Locals bereinigt.

Suite: 86 Tests grün, ruff grün.

---

## 1.40.2 — 2026-10-01

### Accept-Backlog 128 + nginx Thumb-Cache

Zwei letzte Latenz-Hebel aus dem Galerie-Burst-Profiling (der H2-Burst
eines Browsers feuert Dutzende Thumb-Requests quasi gleichzeitig):

- **`request_queue_size = 128`** (war Pythons Default 5): Überschläge im
  Listen-Backlog kosteten betroffene Requests ~1 s SYN-Retransmit —
  sichtbar als die ~1,2-s-Stalls im Galerie-Burst (loopback reproduzier-
  bar). Accept-Verhalten selbst unverändert.
- **nginx `proxy_cache` für Thumbs** (Server-Konfig, nicht Repo-Code):
  Zone `throway_thumbs` (300 MB, inactive 24 h) in
  `conf.d/throway-cache.conf`, map cacht NUR
  `/throway/pics/i/<id>?…thumb=…` — volle Bilder, Uploads, API, HTML und
  Admin-Routen laufen BYPASS zur App (Moderation bleibt live).
  `X-Cache-Status` als Response-Header. Validity 1 h = Trade-off:
  gelöschte/versteckte Bilder können bis 1 h als Thumb nachlesbar sein.
  Vorher nachher (H2-parallel über WAN): Ø TTFB 165 ms = reine RTT,
  Stalls nur noch Netzwerk-Jitter.

---

## 1.40.1 — 2026-10-01

### Perf: Auto-Sweep gedrosselt — kein Full-Directory-Scan pro Request

Der eigentliche TTFB-Killer unter dem Warmer: `sweep()` lief bei JEDEM
Bild- und Galerie-Request (`_serve`, `all_pics`) — ein Full-Scan über
ROOT/pics (listdir + je Bild ein .meta-JSON-Read, aktuell ~800 Dateien
auf der USB-HDD), multipliziert mit parallelen Browser-Requests.
Gemessen: TTFB-Ausreißer bis 5,6 s bei 8 parallelen HEADs, obwohl alle
Thumbs auf Platte lagen.

- Auto-Sweep jetzt pro Prozess gedrosselt (`SWEEP_INTERVAL`, Default
  30 s, env `THROWAWAY_PICS_SWEEP_INTERVAL`): erster Request swept,
  weitere 30 s lang nicht. Bei 90 Tagen TTL ist das mehr als präzise.
- Aufrufe mit explizitem `now=` (Store-Periodikum, Tests) umgehen die
  Drossel — Verhalten für Ablauf/Expiry unverändert (Tests grün).
- Unit-Test: gedrosselter Sweep löscht nicht, expliziter schon.
- Gemessen nach Deploy: TTFB aller 306 Thumb-Kombos (102 Bilder × 3
  Breiten, 8 parallel) bei ~RTT, keine Ausreißer mehr.

---

## 1.40.0 — 2026-10-01

### Pics: Thumb-Warmer — alle srcset-Breiten vor dem ersten Request

Nach 1.39.3 (429-Fix) blieb der letzte Langsam-Faktor: die
Cold-Generation. Der erste Besucher zahlte ~0,3–0,5 s CPU (Pillow
decode/resize/encode) pro (Bild, Breite) — bei 102 Bildern füllten
sich die unteren Grid-Zellen sichtbar langsamer als die oberen.

- Neuer Daemon-Worker (`pics-thumb-warmer`, FIFO-Queue): erzeugt alle
  Whitelist-Breiten (160/320/640) im Hintergrund — nach jedem
  Upload/Import und beim Serverstart (`warm_existing()` nachrechnen
  für Bestände; idempotent, nur fehlende Paare, Neustarts danach
  No-Ops). `sleep(50 ms)` zwischen Generierungen, Pillow gibt die GIL
  her — Requests bleiben bedient.
- DI statt `import store` im Worker: store hat modul-level
  Side-Effects (ROOT-Bootstrap), ein zweiter Import wäre ein zweiter
  Store. `pics.set_thumb_maker()` wird von store.py main registriert,
  Tests injizieren ihre eigene Maker-Funktion.
- Tests: Warmer erzeugt nach Upload alle 3 Breiten ohne Request
  (echte WebPs, korrekte Kanten); Backfill reicht gezielt NUR die
  gelöschte Breite nach.
- Effekt (Gemessen am Soundspritzer-Bestand): Cold Ø 1,3 s/Thumb →
  warm Ø ~0,3 s/Thumb (Netz-RTT), Galerien laden komplett vorgecached.

---

## 1.39.3 — 2026-10-01

### Fix: Galerie-429s — Thumb-Exemption deckt jetzt alle srcset-Breiten

Der Soundspritzer-Galerie-Report („Laden der Thumbs extrem langsam,
~30 s“): 1.39.2 stellte die Grid-Thumbs auf `?thumb=160|320|640` um
(srcset) — die Rate-Limit-Exemption in `do_GET` prüfte aber weiter
literal `"thumb=1"`. `thumb=320`/`thumb=640` fielen dadurch unter den
Zähler (`100 req/min` pro IP): jede Galerie mit >100 Bildern 429te ab
Request #101 für den Rest des 60-s-Fensters — Chrome zeigte die
übrigen Thumbs erst nach Ablauf des Fensters.

- Exemption jetzt `"thumb=" not in _q` (alle Whitelist-Breiten;
  volle Bilder, Uploads und API zählen weiterhin).
- Test `test_thumbs_dont_consume_rate_limit` läuft jetzt über alle
  vier Breiten (1|160|320|640) — 40 Thumb-Requests bei Limit 5.
- Gemessen vorher/nachher (Chrome, 102 Bilder): vorher avg 2,2 s/Thumb
  inkl. 429-Wartefenstern, Requests #101+ garnicht → nachher: Cold
  (Erzeugung on-the-fly) avg 1,3 s/Thumb, warm (Disk-Cache) avg 0,29 s
  — Rest-Optimierungsidee: Thumbs beim Upload vorwärmen.

---

## 1.39.2 — 2026-10-01

### Pics: Responsive Thumbnails (srcset/sizes, `?thumb=N`)

Aus dem SOTA-Review: die Grid-Thumbs lieferten nur `?thumb=1`
(96px). Auf einem ~162px-Gridfeld — erst recht bei 3x-DPI (525px
Bedarf) — war jedes Thumb pixelig.

- `?thumb=N` (N ∈ 160|320|640) serviert genau diese longest edge als
  WebP, lazy erzeugt und pro Breite gecacht (`<file>.thumb320` …).
  Whitelist: unbekannte Werte fallen auf den Default zurück.
- Grid-Thumbs: `srcset` (160w/320w/640w) + kalibriertes `sizes`
  entsprechend der echten Spaltenzahl, `<noscript>`-Fallback.
- **`src` = 640w**, bewusst der größte Kandidat: bei `loading=lazy`
  nimmt Chrome den `src`-Fallback zuerst und wertet `srcset` erst bei
  Re-Layout neu — ohne `src` lädt Chrome gar nichts (gemessen). Der
  Schärfetest ist billiger als die Bytes: 640px-WebP ≈ <1 KB/Bild,
  24 Bilder ≈ 16 KB pro Galerie-Seite. Browser ohne den Chrome-lazy-Bug
  (Safari/Firefox) wählen aus dem srcset die passend kleinere Variante.

Gemessen (CDP-Netzwerk-Mitschnitt): Mobile 390@3x und Desktop
1440@1x laden die 640er-Variante scharf; Kandidatenbreiten exakt.

---

## 1.39.1 — 2026-10-01

### Pics-Lightbox: W3C-ARIA-Dialog-Muster (a11y)

Review gegen den W3C ARIA APG Dialog-Modal Pattern ergab sechs
Lücken, die jetzt geschlossen sind:

- **`aria-modal=true`** (Pflicht laut APG): Screenreader nehmen den
  Hintergrund hinter dem Overlay jetzt als inaktiv wahr.
- **Fokusfalle**: Tab/Shift-Tab bleiben im Dialog (zyklisch über die
  sichtbaren Buttons) — vorher konnte die Tastatur aus der Lightbox in
  die Galerie flüchten, die Modalität war nur optisch.
- **Fokus-Rückgabe**: beim Schließen (Close, Esc, Backdrop) landet der
  Fokus wieder auf dem Thumbnail, das den Dialog geöffnet hat
  (APG-Kernregel).
- **`alt` an `#lbimg`** = Bildname; ein leeres `alt` ist bei einem
  Foto-Viewer falsch (es markiert das Bild als dekorativ).
- **Caption als `aria-live=polite`**: „3 von 24" wird beim Blättern
  vorgelesen.
- **Thumb-Anchor mit Accessible Name** („<name> – groß öffnen"); das
  Thumb-Bild ist dekorativ (`alt=''`), der Link hatte sonst keinen
  Namen.

Details: `open()` setzt den Fokus auf `#lb` (tabindex=-1), Escape liegt
deshalb im `keydown` des Containers (der dokumentweite Handler
überspringt Ziele innerhalb von `lb`), der Fokusfalle zählen sichtbare
Buttons (das Like-Clear hat `display:inline-flex` und ist auch bei
`hidden` sichtbar — ein `:not([hidden])`-Filter wäre falsch gewesen).

Browser-Nachweis (rodney): Fokus im Dialog nach Öffnen, 5× Tab bleibt
drin, Escape schließt, Fokus zurück auf dem Thumbnail. Suite: 82 Tests.

---

## 1.39.0 — 2026-10-01

### Pics-Embed: Smartes Lazy-Load (sichtbarer Ausschnitt statt iframe-Höhe)

Der Infinite-Scroll im `?embed=1` lud bei einer auto-height-Galerie
alles auf einmal nach: das Nachlade-Limit war `innerHeight+500` — im
auto-Height-iframe ist `innerHeight` aber die GESAMTE Inhaltshöhe
(2717 px / 102 Bilder), der Sentinel lag also immer im "Fenster". Bei
102 Bildern luden alle 5 Seiten sofort.

SOTA-Lever, der in dieser Architektur zur Verfügung steht: der
1.38.4-Handshake kennt den sichtbaren Ausschnitt bereits
(`throway:pics:viewport` mit `height`+`offset`). Das Inf-Script hört
mit, pflegt `visTop`/`visH` und lädt erst, wenn der Sentinel dem
sichtbaren Fenster nahe kommt (900 px Vorlauf) — event-getrieben
(Viewport-Nachricht) mit Intervall als Sicherheitsnetz. Grace-Periode
von 1.5 s, damit der Handshake den Erst-Tick nicht verpasst; ohne
Host-Nachrichten bleibt das alte `innerHeight`-Verhalten.

Zusätzlich: `next` in der Lightbox an der Grenze der geladenen Liste
lädt die nächste Seite nach und springt automatisch weiter, statt zu
wrappen — Lazy-Load bricht das Durchblättern nicht ab.

Browser-Nachweis (rodney, 30 Bilder, 390-px-iframe): ohne Scrollen
24 Thumbs, Seite 2 lädt erst bei Tiefscroll; Lightbox "24 / 30" →
next → "25 / 30". Suite: 81 Tests grün.

---

## 1.38.4 — 2026-10-01

### Pics-Embed: Lightbox jenseits der ersten 24 Bilder + sichtbare Position

Feldbefund soundspritzer.at/share (102 Bilder): Klick auf einen
Thumb ab #25 navigierte das iframe zum rohen Bild (kein Click-Handler —
die Pro-Anker-Bindung kannte nur die ersten 24) und tötete damit
Grid, Lightbox und Auto-Height in einem Zug; die Caption zeigte
deshalb immer "N / 24"; und die Lightbox öffnete am iframe-Anfang —
bei tief gescrollter Host-Seite tausende Pixel über dem Sichtbereich.

- **Click-Delegation** auf `.grid a` mit Live-Index-Lookup: jeder
  nachgeladene Thumb öffnet die Lightbox, kein iframe-Navigieren mehr.
- **LB-Liste wächst mit**: jede Embed-Seite trägt ihre Items als
  `<script type=application/json id=lbdata>`; der Infinite-Scroll-
  fetch übergibt sie an `window.__tyLbAdd` → Caption "N / 102".
- **Position**: `throway:pics:viewport` um `offset` erweitert (Host-
  Scrollposition des iframes, Dedupe auf Höhe+Offset); `#lb.top`
  folgt dem sichtbaren Ausschnitt und rückt beim Scrollen nach —
  die Lightbox bleibt unter den Augen, nicht am iframe-Anfang.

Browser-Nachweis (rodney, 30-Bilder-Repro): Klick auf Thumb #30 →
Lightbox "30 / 30", iframe-URL unverändert, Bild korrekt skaliert,
Box bei Host-Scrollposition. Suite: 80 Tests grün.

---

## 1.38.3 — 2026-10-01

### Agent-Copy: Markdown-Publishing discoverable (Help-Topic `markdown`)

Session-Feedback (soundspritzer-Session): Ein Agent fand nicht heraus,
wie man ein lebendes Markdown-Dokument auf throway veröffentlicht, und
hielt ein hochgeladenes Kanban-HTML für ein Service-Feature. Ursache:
Das mdrender-Verhalten (seit 1.28: `.md` rendert als HTML-Seite für
Browser, roh für Agenten/`?raw=1`) war in KEINER der Help-Topics und
nicht in der Homepage-Agent-Beschreibung dokumentiert.

- **Neues Topic `/help/markdown`**: Rendering-Verhalten, One-off- vs.
  Living-Doc-Rezept (named dir + PUT/PATCH + history), Hinweis
  "kein CMS" und "Boards auf throway = hochgeladene HTML-Dateien".
- **overview**: Markdown als Zweck + "Not an app platform"-Guard.
- **view**: `.md` in der Inline-Render-Liste; **dirs**: Rendering-Hinweis
  bei `GET /d/<key>/<file>`; **files**: Pointer unter `?share=`.
- **Homepage-Agent-Summary**: `?name=doc.md`-Usage-Zeile.
- **Docs-Drift behoben**: API.md nannte Topic `named_dirs`, das nie
  existierte. release-check.sh prüft jetzt zusätzlich, dass jedes
  HELP_ORDER-Topic in API.md erwähnt ist (hätte den Drift gefangen).

Suite: 79 Tests grün; release-check inkl. neuem Topics-Drift-Guard.

---

## 1.38.2 — 2026-10-01

### Pics: Lightbox im Embed auf den sichtbaren Viewport fixiert

Die Lightbox in `?embed=1`-Galerien war auf Mobile unbenutzbar: Das
Auto-Height-iframe (`throway:pics:height` an den Host) macht den
iframe-Viewport so hoch wie den ganzen Inhalt (gemessen 2717 px,
live 8826 px). `position:fixed;inset:0` spannte die Lightbox über die
ganze Höhe, das Bild kollabierte auf 0×0 (`max-height:80vh` = 80 %
davon, aber `max-width:100%` deckelt), Vor/Zurück/Caption/Like lagen
~1300 px unterhalb des sichtbaren Screens — nur das Close-X war
erreichbar.

Zweistufiger Fix:

1. **Host-Handshake:** Das Embed-Snippet postet
   `{type:"throway:pics:viewport", height: window.innerHeight}` an das
   iframe — auf `resize`, `scroll` und nach dem Auto-Height-Update,
   dedupliziert. Das iframe meldet `throway:pics:ready`, sobald sein
   Listener lebt (verhindert das verlorene erste Post bei langsamem
   Laden). Lightbox-Höhe = exakt die sichtbare Viewport-Höhe.
2. **Self-contained Fallback** für alte, bereits kopierte Snippets:
   ohne Host-Nachricht kappingt die Lightbox auf
   `min(innerHeight, screen.height)` — nie wieder Tausende Pixel.
   Dazu `inset:0` → `top/left/right + height`, Bild `max-height:80vh`
   → `80%` (relativ zur Box), alle Controls hängen an der Box statt
   am iframe-Viewport.

Browser-Nachweis (rodney + CDP, 390×844 Mobile-Emulation gegen ein
2717-px-Auto-Height-iframe): Legacy-Snippet → `#lb` 844 px, Bild
300×200, alle Controls im Sichtbereich. Neues Snippet → `#lb` =
Host-`innerHeight` (Handshake), per postMessage exakt steuerbar
(500-px-Test). Rendered JS `node --check` grün; Suite: 78 Tests grün.

---

## 1.38.1 — 2026-09-28

### Embed: helle Scrollbar im Dunkel-Host versteckt

Das Embed passt seine Höhe per postMessage an — eine Scrollleiste ist
dort nie nötig, aber Chrome zeichnet sie trotzdem (weiß auf dunklem
Host, sah unpoliert aus). `scrollbar-width:none` + Webkit-Pendant im
Embed-CSS. Scrollen (No-JS-Fallback) geht weiter per Rad/Geste.

---

## 1.38.0 — 2026-09-28

### Likes & Kommentare für Event-Galerien (Gästebuch, ohne Login)

Bilder liken, die beliebtesten wandern hoch; Galerie-Kommentare mit
eigenen Likes. Alles serverseitig gezählt, ohne Konto — Pseudonym per
Fingerprint (IP+UA, keyed mit dem Gallery-Token, nie exponiert).

- **Likes (Bilder)**: `POST /pics/i/<id>?like=1` — Toggle pro Besucher,
  Antwort `{id, likes, liked}`. Zähler + Fingerprints im Sidecar
  `g/<gid>.likes.json` (neueste 500 fps, RAM-schonend).
- **Kommentare**: `POST /pics/g/<gid>?comment=1` (Form oder JSON:
  name ≤ 40 optional, text ≤ 500) — 20 s Cooldown/Besucher (nur RAM,
  es wird keine IP persistiert), max 500/Galerie (env-tunbar).
  Browser-Form → 303 zurück zu `#comments`.
- **Kommentar-Likes**: `POST /pics/g/<gid>?clike=<cid>` — gleiches Modell.
- **Ranking**: `GET /pics/g/<gid>?sort=likes` — Likes zuerst, kuratierte
  Ordnung bricht Gleichstand; `?embed=1` behält es bei. Browser:
  gelikte Bilder FLIP-climben live, 30-s-Poll hält Zähler frisch
  (`GET ?likes=1` — Zähler ohne Bild-Payloads).
- **UI**: Herz-Button auf jedem Thumb + im Lightbox (synchronisiert),
  Kommentarformular + Neueste-zuerst-Liste auf Galerie & Embed; No-JS =
  Form-POST + Links (progressive enhancement). XSS-fest escaped,
  user content nur via textContent im JS.
- **Moderation**: Admin-Seite listet Kommentare mit Löschen-Button
  (`action=cdel`); Superadmin inklusive.
- **Bugfix nebenbei**: `gallery_sweep`/`all_galleries` behandeln die
  neuen Sidecars (`*.likes.json`, `*.comments.json`) nicht mehr als
  Gallery-Metas (wären sonst als „abgelaufen" gelöscht worden).
- Docs-Sweep: /api (4 neue Endpunkte), help `pics`, API.md, README.
  Tests: 77 grün (11 neu: Toggle/Fingerprint, Sortierung, Cooldown,
  Caps/Trim, Escaping, Admin-Delete, UI-Marker) + Browser-Flow via
  rodney (Klick-Like mit FLIP, Lightbox, AJAX-Kommentar) verifiziert.


## 1.37.1 — 2026-09-27

### Bugfix: Google-Import holte jede Größenvariante + den User-Icon

Live-Analyse der Share-Seite: Sie enthält pro Foto **mehrere
Größenvarianten** (96px-Thumbs, OG-Cover, Vollversion) plus den Avatar
(`/ogw/default-user=s83`). Der Scraper nahm alle mit, `_lh3_hq` blies
alle auf 2048px auf → jedes Foto mehrfach + User-Icon als
„unnamed.jpg". (Die frühere „Byte-Varianz"-Deutung war falsch — es waren
Größenvarianten.)

- **Basis-Dedupe im Scraper**: eine Variante pro Foto-Basispfad — die
  größte (param-los = Vollversion). Avatare (`/ogw/`, `/a/`) und UI-
  Icons (≤128px) fliegen raus.
- Test erweitert (Varianten/Avatar/Icon gefiltert, größte gewählt) — 66
  grün. Live in einer frischen Galerie verifiziert: nur echte Fotos,
  kein unnamed.jpg.

---

## 1.37.0 — 2026-09-27

### Duplikat-Erkennung (Content-Hash, pro Galerie)

Event-Wänden und Re-Imports sei Dank: identische Bilder werden nicht
zweitgespeichert.

- **SHA-256 der gespeicherten Bytes** pro Bild in der Meta. Upload eines
  bereits vorhandenen Bildes (gleiche Galerie) liefert die **existierende
  ID zurück** mit `duplicate: true` — idempotente Upload-Semantik statt
  Fehler. Andere Galerien sind bewusst nicht dedupliziert.
- Multipart-Batches: `duplicates: N` im Response; Album-Import
  (Share-Link): `duplicates` gezählt, nicht neu geholt; Uploader-Status
  zeigt „N duplikate übersprungen" / Zeile „duplikat — übersprungen".
- 1 neuer Test (gleiche ID + Flag, Count bleibt, anderes Bild speichert,
  Batch-Duplikate) — 66 grün.

---

## 1.36.2 — 2026-09-27

### Bugfix: Embed-Infinite-Scroll feuert zuverlässig

IntersectionObserver meldete im Embed-Kontext `isIntersecting:false`
trotz sichtbarem Sentinel (y=412 im 800px-Viewport, live reproduziert) —
das Nachladen startete nie. Ersetzt durch schlichtes Intervall-Polling
(400ms, räumt sich nach letzter Seite selbst ab, Initial-Check nach
150ms). Lokal bewiesen: 24 initial → alle 38 geladen, Sentinel entfernt;
Host-Seite: iframe wächst auf exakte Inhaltshöhe (739px für 38 Bilder).

---

## 1.36.1 — 2026-09-27

### Bugfix: Auto-Höhe meldet echte Inhaltshöhe

`documentElement.scrollHeight` ist viewport-geklemmt — das Embed konnte
nie kleiner werden als die Starthöhe des iframes (642px statt ~450px
im Live-Test). Jetzt: `body.scrollHeight` mit 280px-Mindesthöhe.
Live gegen die Host-Seite verifiziert: iframe schrumpft auf
Inhaltshöhe, wächst beim Nachladen mit.

---

## 1.36.0 — 2026-09-27

### Smartes Embed: Infinite-Scroll + Auto-Höhe

- **Infinite Scroll im Embed**: IntersectionObserver am Sentinel lädt die
  nächste Embed-Seite per fetch und hängt den Grid an (initial 24 Bilder,
  rootMargin 500px — lädt VOR dem Erreichen des Randes). Paging-Links
  bleiben als No-JS-Fallback.
- **Auto-Höhe per postMessage**: Das Embed meldet seine Inhaltshöhe an
  die Host-Seite (`throway:pics:height`, ResizeObserver + nach jedem
  Nachladen). Das neue Embed-Snippet enthält die 2-Zeilen-Listener-
  Zeile — iframe wächst mit, keine doppelten Scrollbars, kein leerer
  Raum. Wer nur die iframe-Zeile nimmt, bekommt das Scroll-Verhalten
  wie zuvor.
- Snippet auf der Galerie-Seite aktualisiert (beide Zeilen, Klick
  selektiert alles).
- 1 Test erweitert (Sentinel/Observer/postMessage im Markup) — 65 grün;
  embed-JS per node --check, rodney-Live-Beweis mit Host-Seite folgt.

---

## 1.35.2 — 2026-09-27

### Bugfix: Erfolgsmeldung bei Album-Import (soundspritzer-Fall)

Der Galerie-Paste prüfte `x.d.id` — Album-Antworten haben aber
`{imported: N}` statt `id`: der Import lief erfolgreich durch, die Seite
meldete trotzdem „fehlgeschlagen: unbekannt" und lud nicht neu. Jetzt:
„importiert ✓ 38 bilder" + Auto-Reload. Live auf soundspritzer
verifiziert (38 Bilder drin, Meldung korrekt).

---

## 1.35.1 — 2026-09-27

### Bugfix: Ctrl+V auf der Galerie-Seite (Illegal invocation)

Der Paste-Handler rief `clipboardData.getData` abgekoppelt auf
(`(e.clipboardData.getData || …)(…)' — ohne Empfänger) → Chrome wirft
stillschweigend „Illegal invocation", der Import startete nie. Mein
übercleveres Defensivkonstrukt; jetzt schlicht gebunden. Mit echtem
Paste-Event gegen die soundspritzer-Galerie live verifiziert.

---

## 1.35.0 — 2026-09-27

### Google-Photos-Share-Links einfügen (Stufe 1.5)

„Diesen Link will ich reinkopieren" — jetzt geht das:

- **`?url=` versteht Share-Links** (`photos.app.goo.gl/…`): Kommt statt
  eines Bildes eine HTML-Seite zurück, wird sie nach den eingebetteten
  `lh3.googleusercontent.com`-Bild-URLs durchsucht (Server-gerendert,
  kein JS nötig), die Größenbegrenzung wird auf unser 2048-px-Budget
  angehoben (Auth-Tail bleibt) und **das ganze Album** wird sequenziell
  importiert — Pipeline wie immer (Pixel-Regel, GPS-Strip, Pool).
  Antwort: `{imported, failed, images, errors}`. Cap 100 Bilder/Link,
  Videos (andere Hosts) bleiben automatisch draußen.
- **Paste überall**: Link in die Galerie-Seite oder den Homepage-
  Gallery-Tab einfügen (⌘V) → Import läuft mit Statuszeile; ohne
  bestehende Galerie wird erst eine angelegt.
- 1 neuer Pure-Function-Test (Scrape+HQ-Rewrite) — 65 grün. Share-Link-
  Import live gegen einen echten Link verifiziert (siehe Release-Doku).
- Bekanntes Risiko (im Issue notiert): Google kann das Share-Seiten-
  Format ändern — der Scraper ist bewusst schmal (nur lh3-Muster).

---

## 1.34.0 — 2026-09-27

### Stufe 1 des Google-Photos-Plans: URL-Import in Galerien

- **`POST /pics/g/<gid>?url=<bild-url>`**: Server holt das Remote-Bild
  durch die normale Pics-Pipeline (Pixel-Regel, GPS-Strip, 30-MB-Cap,
  Pool-507). SSRF-Guard wie beim throway-Import (nur öffentliche Hosts,
  Redirect-geprüft), nur `image/*`. `_fetch_remote` ist jetzt größen-
  parametrisierbar; `THROWAWAY_ALLOW_PRIVATE_FETCH=1` als Test/Dev-
  Flucht (Prod-Default: an).
- **URL-Drops in beiden Dropzonen** (Galerie-Seite + Homepage-Gallery-
  Tab): Bild aus einem anderen Tab (z.B. Google Photos) auf die Dropzone
  ziehen → Server-Import mit Statuszeile. Homepage-Tab legt bei Bedarf
  erst die leere Galerie an.
- Stufe 0 (Copy-Paste aus Google Photos) funktioniert seit den Paste-
  Uploads ohnehin. Stufe 2 (Picker-API) bleibt im Issue-Backlog.
- 2 neue Tests (Import-Flow inkl. Nicht-Bild-400, SSRF-Default-Block) —
  64 grün. /api + /help/pics + AGENTS.md dokumentiert.

---

## 1.33.0 — 2026-09-27

### Galerien einbettbar (embed)

- **`?embed=1`** auf `GET /pics/g/<gid>`: minimale, chrome-lose Ansicht
  für `<iframe>` — nur Grid, kompakte Pagination und Lightbox; kein
  Header, kein Uploader, **transparenter Hintergrund** (Host-Seite
  scheint durch). Agents bekommen wie immer JSON (embed ist reines
  Browser-Rendering).
- **Copy-Paste-Snippet** auf jeder Galerie-Seite: „diese Galerie
  einbetten" klappt ein `<details>` auf mit fertiger iframe-Zeile
  (readonly-Input, Klick selektiert alles) — null zusätzliches JS.
- `/api` + `/help/pics` dokumentieren den Parameter. 1 neuer Test
  (Embed-Ansicht enthält/fehlt-X, Snippet, Agent-JSON) — 62 grün.

---

## 1.32.1 — 2026-09-27

### Bugfix: Gallery-Create aus dem Browser (gefunden durch echten Browser-Test)

Der `POST /pics?create=1` antwortete nur für Agent-UAs mit JSON — der
Browser-JS-Flow (Create-Button UND Upload-Create-on-first-file im
Homepage-Gallery-Tab) bekam HTML, `r.json()` scheiterte still. Meine
bisherigen Validierungen liefen per curl (= Agent-UA) und maskierten
das. Jetzt Content-Negotiation: JSON bei `Accept: application/json`
oder Agent-UA; beide JS-Fetches senden den Header. Rodney-Browser-Test
bestätigt: Button-Flow, Admin-Link-Anzeige, Space-Kuration vollständig
durchgeklickt. (Und der Checklisten-Schritt „VERSION bumpen" rächt
sich, wenn man ihn überspringt — nachgeholt.) 61 Tests grün.

---

## 1.32.0 — 2026-09-27

### Galerie-UX: leere Galerie anlegen + Admin-Tastatur-Kuration

- **„Create gallery"-Button** im Homepage-Gallery-Tab: Galerie ohne
  einziges Bild anlegen (Event morgen vorbereiten, Bilder später).
  Create-or-get wie immer — existiert der Name, erscheint der
  öffentliche Link mit Hinweis (Admin-Token nur beim Anlegen). Später
  hochgeladene Bilder landen automatisch in der angelegten Galerie.
- **Space-Kuration im Admin-Lightbox**: Admin blättert mit < > durch
  die Galerie und toggelt mit **Space** das aktuelle Bild zwischen
  sichtbar (+) und verborgen (−) — ohne den Viewer zu verlassen. Die
  Karte dahinter dimmt live, die Caption zeigt den Zustand
  („[+ visible] / [− hidden]"), Pfeil-Scroll wird unterbunden.
- Admin-Lightbox-JS nur auf Admin-Seiten ausgeliefert (public bleibt
  schlank); beide per node --check validiert. 61 Tests grün.

---

## 1.31.0 — 2026-09-27

### Homepage-UX-Pass (Vision-Review, improve-ux-Skill)

Screenshot-gestützter Review des `#text`-Zustands (User-Feedback:
„textfeld wirkt old style") — 5 Fixes, 3 Befunde ins Ledger verschoben:

- **Textarea war ungestylt** (Wurzel: `#createBox`-Selektor verwaist seit
  dem 1.22er-Tab-Umbau → Browser-Default). Jetzt: volle Panel-Breite,
  min-height 220px, Mono-Typo .95rem/1.6, 3px-Focus-Ring — statt
  180×110px-Stub in 790px Panel.
- **Feature-Grid**: 4. Karte (Pics) stand als Waisen-Karte in Reihe 2
  (minmax 220px) → `minmax(180px,1fr)`, vier Karten in einer Reihe.
- **Stats**: „1 FILES"-Plural-Bug → dynamisch; Pool-Meter unter 2%
  Belegung unsichtbar (0,06% real) → Mindestfüllung 2% + Title-Tooltip
  „pool: X of Y".
- **Agent-Info** (40 Zeilen Raw-Dump) begrub die Homepage → jetzt
  default zugeklappt.
- **„once"-Checkbox** kryptisch → „download once" + Tooltip; Share-Hint
  rutschte gequetscht neben das Input → eigene Zeile.

Verzögert (Ledger F6–F8): Casing-System, PUBLIC_BASE-Dokumentation,
Char-Counter im Text-Tab. Messtechnisch verifiziert via Headless-Chrome
(Textarea 761×225px, 4 Karten/Reihe, agents collapsed). 61 Tests grün.

---

## 1.30.0 — 2026-09-27

### pics ↔ throway-Integrationsreview: drei Nähte gefestigt

Systematischer Konsistenz-Check der pics-Integration. Befunde + Fixes:

- **Thumbs zählen nicht mehr aufs Rate-Limit** (der kritische Fund): Eine
  Galerieseite feuert 60 `?thumb=1`-Requests — zwei volle Seiten pro
  Minute liefen ab Request #101 in 429 (bewiesen im Lokaltest). Thumbs
  sind gecachte Mikro-WebPs und bleiben jetzt außerhalb des Zählers;
  Uploads, Vollbilder und API zählen weiter.
- **Cache-Control für Unveränderliches**: Thumbs `public, max-age=3600`,
  pics-Bilder `public, max-age=86400` (IDs sind bis zum Ablauf
  immutable) — Event-Gäste hinter NAT profitieren kollektiv. Throway-
  Einzeldateien bleiben bewusst ungecacht (PUT-editierbar). Nebenan
  bereinigt: doppelter Cache-Header im Thumb-Pfad, Header-Nach-`end_
  headers()`-Bug in `_serve_file`.
- **pics zählen in die Stats**: Galerie-Uploads bumpen jetzt
  stats.json + Since-Start-Zähler — die Homepage zeigt Galerie-Aktivität
  statt „0 files". Stats sind kosmetisch: Fehler dort killen nie einen
  Upload.
- Docs: AGENTS.md-Kompressionszeile auf die 1.26er-Pixel-Regel
  korrigiert (stand noch auf „recompress alles").
- 2 neue Tests (Thumb-Limit-Exemption + Cache-Header/Stats) — 61 grün.

Unverändert korrupt-frei bestätigt: Pool-Trennung (4 Stellen),
RESERVED_NAMES, Homepage-/Browse-Listing überspringen pics, Routing.

---

## 1.29.0 — 2026-09-27

### Opt-in Write-Token für DIRs (Issue: throway-dir-write-token)

Lesen bleibt offen, Schreiben auf Wunsch geschützt — rückwärtskompatibel:

- **Anlegen**: `POST /?dir=1&name=<name>&write=1` → Server generiert
  Token (48 hex); alternativ `&write=<eigenes>` (8–64 Zeichen
  [A-Za-z0-9._-]). **Token steht genau einmal im Create-Response**
  (`write_token` + Hinweis); create-or-get auf existierenden DIR
  verrät ihn nie.
- **Schreiben geschützt**: `POST /d/<key>`, `PUT/PATCH /d/<key>/<file>`,
  `DELETE /d/<key>[/<file>]` verlangen den Token — Header
  `X-Throway-Write` oder `?write=<token>`, constant-time verglichen.
  Ohne/falsch → 401. Auch das `?share=<name>`-Loch ist geschlossen.
- **Lesen offen**: Files, Listing, History, Zip bleiben ohne Token
  erreichbar. `write_protected: true` im Listing zeigt den Zustand an.
- **Bugfix nebenbei**: `do_PUT`/`do_PATCH`/`do_DELETE` strippen jetzt
  Query-Strings aus dem Pfad — `?write=…` (und jede andere Query) auf
  diesen Routen führte vorher zu 404. Nur GET konnte das je.
- 3 neue Tests (Token-Flow komplett, Backward-Compat + Leak-Guard,
  Custom-Token) — 59 grün. API-Spec + Help aktualisiert. Issue in
  Review.

---

## 1.28.0 — 2026-09-27

### DIR-Root serviert index.html inline (Issue: throway-dir-index-landing)

Parität mit Bundles: Enthält ein DIR eine `index.html`, bekommen Browser
an `GET /d/<key>` diese inline ausgeliefert statt der Listing-Seite —
der geteilte DIR-Link ist damit die Landing-Page (Kontext:
firmenindex-Agent teilt Report-DIRs mit gerendertem Report als
`index.html`).

- `<base>`-Injection wie bei Bundles, damit relative Links
  (`style.css`, `data.json`, …) gegen `/d/<key>/` auflösen.
- Kleiner Footer-Link „files & history" → `?listing=1` erzwingt die
  klassische Listing-Seite.
- Agents: unverändert JSON-Listing. DIRs ohne `index.html`: unverändert.
  `?zip=1`/`?download=1`: unverändert.
- 1 neuer Test (Browser-Root, ?listing=1, Agent-JSON, ohne-index) — 57
  grün. Issue in Review.

---

## 1.27.0 — 2026-09-27

### Markdown im Browser rendern (Issue: throway-md-render-browser)

`.md`-Dateien liefern Browsern jetzt **gerendertes, self-contained HTML**
statt Markdown-Quelltext (Kontext: firmenindex-Agent teilt Report-DIRs,
`report.md` ist das Primary-Artefakt):

- **Eigener Renderer** `throway/mdrender.py` — stdlib-only, kein JS, kein
  externes CSS: Überschriften (Titel aus `# `-Zeile), Absätze, Listen
  (verschachtelt, ordered/unordered), GFM-Tabellen, Code-Fences,
  Blockquotes, HR, Links (+bare Autolinks), **bold/italic/code**.
- **Sicherheit zuerst**: alles wird HTML-escaped, Link-Schemata auf
  http/https/mailto/relativ/#anchor beschränkt (kein `javascript:`),
  Code-Spans schützen Inline-Formatierung.
- **Agents bleiben raw** (UA-basiert wie immer); `?raw=1` ist der
  explizite Escape — auch für Browser. `?download=1` unangetastet.
- Wirkt auf **alle drei Serve-Pfade**: Einzeldatei `/<id>`, Bundle-Datei
  `/<fid>/<file>`, Dir-Datei `/d/<key>/<file>`. Footer-Link „raw:
  markdown" auf jeder gerenderten Seite.
- 10 Unit-Tests + Integrationstests über alle Pfade — 56 grün. Issue in
  Review (throway-md-render-browser).

---

## 1.26.0 — 2026-09-27

### Nur komprimieren, wenn die Pixel zu gross sind

Johanns Regel, jetzt wörtlich umgesetzt:

- **≤ 2048 px und web-freundliches Format (JPEG/PNG/WebP)** → Bild wird
  **byte-identisch** gespeichert. Kein Re-Encode, null Qualitätsverlust,
  null Generationen-Drift. Transparenz natürlich intakt.
- **JPEG-Metadaten lossless entfernt**: Auch im Keep-Original-Pfad werden
  EXIF (inkl. GPS!), XMP, Photoshop/IPTC und Kommentare aus dem Container
  geschnitten — reine Byte-Chirurgie an APP-Segmenten, **Pixel bleiben
  100% identisch** (verifiziert per Hash). JFIF + ICC-Farbprofil bleiben.
- **> 2048 px** → downscale auf 2048 px + WebP q90 mit Step-down bis
  ≤ 1 MB (wie 1.25.0; Floor q65, alpha-erhaltend).
- GIFs pass through weiter unangetastet (Animation), HEIC/AVIF müssen
  transcodieren (Browser können sie nicht anzeigen) — dabei fällt deren
  Metadaten ohnehin weg.
- Server-Pipeline und Browser-Pre-Resize (Homepage-Tab) sind jetzt
  konsistent: kleine Bilder gehen unberührt durch.
- 2 neue Tests (byte-identisch JPEG+PNG, lossless EXIF-Strip), 45 grün.
  RFQ FR-5 aktualisiert.

---

## 1.25.0 — 2026-09-27

### Komprimierung: HQ first, size second (1-MB-Cap)

Auf Johanns Rückmeldung: Fotos sollen hochwertig bleiben — Limit ~1 MB.

- **q90 statt q80** als Start-Qualität; nur wenn das Ergebnis **> 1 MB**
  ist, sinkt die Qualität in 5er-Schritten (90 → 85 → … → Floor 65) bis
  es passt. **Ergebnisse unter 1 MB bleiben auf q90** — keine unnötige
  Verschlechterung mehr bei bereits kleinen Fotos.
- **Alpha bleibt erhalten**: transparente PNGs (Logos, Grafiken) werden
  nicht mehr auf Weiß aufgefüllt, sondern als RGBA-WebP gespeichert.
- **EXIF/GPS wird konsequent entfernt** (vorher konnte der
  „keep-original"-Pfad bei winzigen Dateien EXIF inkl. GPS durchreichen —
  jetzt strippt jeder Pfad, Privacy by Default für eine öffentliche
  Galerie).
- Client-seitig (Homepage-Gallery-Tab) spiegelt der Browser-Shrink das
  Verfahren: 2048 px, q0.90, Step-down bis ≤ 1 MB (Floor 0.70) — spart
  Upload-Bandbreite bei gleichem Ergebnis.
- Env-tunbar: `THROWAWAY_PICS_QUALITY` (90), `THROWAWAY_PICS_QUALITY_FLOOR`
  (65), `THROWAWAY_PICS_TARGET_BYTES` (1 MB), `THROWAWAY_PICS_EDGE_PX`
  (2048).
- 3 neue Tests (Size-Cap, Alpha, EXIF-Stripping) — 44 grün. RFQ FR-5
  aktualisiert.

---

## 1.24.0 — 2026-09-27

### Schöneres Upload-Feld (Galerie + Homepage-Gallery-Tab)

Das rohe File-Input („No file chosen") ist weg — beide Upload-Felder sind
jetzt Dropzones im Stil der Hauptseite (aktuelle Upload-UX-Patterns):

- **Icon + zweizeilige Beschriftung**: „Bilder hierher ziehen oder
  klicken" + Constraints **vor der Auswahl** (Formate JPG/PNG/WebP/GIF/
  HEIC, max 30 MB, 1000+ auf einmal).
- **Drag-over-Feedback**: Rahmen + Hintergrund wechseln, sobald eine Datei
  über dem Feld schwebt (drop-zones-müssen-antworten-Regel).
- **Status statt „No file chosen"**: „3 ausgewählt — Upload startet…",
  dann Live-Zähler (X hochgeladen · Y ausstehend).
- **Drag & Drop jetzt auch auf der Galerie-Seite** (vorher nur Klick).
- Screenreader-freundlich: Input sr-only im Label-Wrapper
  (Klick überall im Feld öffnet den Picker, `:focus-within`-Outline).
- Homepage-Gallery-Tab: gleiche Optik (Icon, Sub-Line mit Formaten/Limit).

Reiner Frontend-Polish, API unverändert. Gerendertes JS per node --check
validiert, 41 Tests grün.

---

## 1.23.0 — 2026-09-27

### Galerie: Lightbox — Bilder weiterklicken (‹ › / Pfeiltasten / Swipe)

Klick auf ein Thumbnail öffnet jetzt die **Bildansicht** statt eines
nackten Bild-Loads:

- **‹ › Buttons** überblättern, **← → Pfeiltasten** ebenso,
  **Touch-Swipe** mobil; **Esc** oder Klick neben das Bild schließt.
- Caption mit Position + Dateiname („2 / 6 — capri.jpeg“).
- Nachbarbilder werden **vorgeladen** (flüssiges Blättern).
- Body-Scroll lockt während offen; ohne JS bleiben Thumbs normale Links
  (progressive enhancement).
- Auch im **Admin** — dort mit secret-scoped URLs, inkl. verborgener
  Bilder (nur im Admin sichtbar, public bleibt 404).
- Lightbox deckt pro Seite die 60 gerenderten Thumbs ab (Paginierung
  blättert weiter). Gerendertes JS per node --check validiert, 41 Tests
  grün.

---

## 1.22.1 — 2026-09-27

### Bugfix: `?name=` wird jetzt percent-dekodiert

Dateinamen wie `uniinfer-tu%40x.png` oder `PXL%20foto.jpg` landeten mit
`%40`/`%20` im Metadaten-Namen. `name` (throway-Upload, pics-Upload und
Galerie-Anzeigename) wird jetzt URL-dekodiert — Standard-Query-Verhalten.
1 Test neu, 40 grün. Reiner Bugfix → Patch-Bump.

---

## 1.22.0 — 2026-09-27

### Upload-Seite: ein Multi-Type-Uploader (Segmented Control)

Die Startseite hat jetzt **einen Uploader mit vier Typen** als Segmented
Control — nach aktueller Upload-UX-Praxis (per-file Status, Constraints
vor der Auswahl, Paste-Routing, stabiles Layout, ARIA-Tabs):

- **Files** (default): Drag & Drop / Klick / Paste wie gehabt — jetzt mit
  Constraint-Zeile („max 5 MB · lives 4h …") unter dem Panel.
- **Text** (ersetzt den „+"-Button): Textarea + filename/once/ttl/share
  direkt im Panel.
- **Link** (neu im UI): URL einfügen → server-seitiger Import
  (`POST /?url=`, API existierte schon, war nur UI-los).
- **Gallery**: Galerie-Name + listed + Drop/Paste von Bildern →
  **client-seitiges Downscaling auf ≤ 2048px WebP** vor dem Upload
  (10–20× weniger Bandbreite bei Handyfotos, EXIF-Rotation via
  createImageBitmap erhalten, GIFs unangetastet) → create-or-get der
  Galerie → sequenzielle Uploads mit **Per-File-Status** (queued /
  resizing / uploading / done ✓ / failed + Retry-Button pro Datei,
  429-Warteschleife). Nach dem Anlegen erscheinen Galerie-URL +
  **Admin-Link** mit Copy-Buttons (Einmal-Hinweis).

Details: Tab-Wechsel instant mit stabilem Rahmen, Panel-Zustände bleiben
beim Wechsel erhalten, Roving-Focus + Pfeiltasten (ARIA-Tab-Pattern),
aktiver Tab in der URL (Hash, Restore beim Laden), Paste-Routing
(Bilder → Gallery-Tab wenn aktiv, sonst Dropzone; Pastes in Formular-
felder werden nie gekapert). Mobile: Tabs scrollbar, Inputs volle Breite.
JS-Syntax per node --check geprüft; 39 Tests grün.

---

## 1.21.0 — 2026-09-27

### pics auf der throway-Startseite

Galerie-Anlegen ist jetzt Teil der Default-Site (`skale.dev/throway/`),
nicht mehr nur unter `/pics`:

- **Feature-Karte** in der Feats-Übersicht (Files / Bundles / Dirs / Pics)
  mit Link zum Galerie-Index.
- **Anlege-Formular** direkt unter den Upload-Controls: Name + „listed“-
  Checkbox + Button → `POST /pics?create=1` (gleicher Flow wie auf der
  pics-Seite, inkl. Einmal-Anzeige des Admin-Links).
- Nur UI-Integration, keine API-/Verhaltensänderung. 1 neuer Test
  (Homepage enthält Formular, Flow end-to-end) — 39 Tests grün.

---

## 1.20.0 — 2026-09-27

### pics: Multi-Galerie-Modell — jede:r kann Galerien anlegen (dirs-artig)

Aus *einer* Admin-Galerie (1.19.0) wird ein Verzeichnis beliebiger
Galerien — konzeptionell wie die throway-dirs: create-or-get-Namen,
unlistet per Default, `listed=1` für den öffentlichen Index, sliding
lifetime.

- **Anlegen**: `POST /pics?create=1[&name=<name>][&listed=1]` → JSON mit
  `id`, öffentlicher URL, **`admin_url` + `token` (genau einmal
  angezeigt)**. Dir-style Namen (`[a-z0-9-]`, 5–32 Zeichen, ≥1 Buchstabe,
  nicht reserviert) werden zum Schlüssel: `/pics/g/hochzeit-2026`
  (create-or-get, idempotent). Andere Namen sind Anzeigenamen auf frischer
  Hex-ID.
- **Token-Sicherheit**: Re-Create einer existierenden benannten Galerie
  liefert diese **ohne Token** zurück (`existed:true`) — Name erraten ist
  keine Admin-Übernahme.
- **Upload**: `POST /pics/g/<gid>?name=` (roh oder multipart-batch),
  sofort öffentlich, schiebt das Galerie-Ablaufdatum nach vorn.
- **Admin**: `GET/POST /pics/g/<gid>/<secret>` — hide/unhide/delete/up/down,
  `/json`-Listing inkl. hidden, Aktionen strikt auf Bilder der eigenen
  Galerie beschränkt (Cross-Galerie-Zugriff = No-op bzw. 404).
- **Superadmin**: der Env-Token (`THROWAWAY_PICS_ADMIN_TOKEN`) kuratiert
  zusätzlich **jede** Galerie (Betreiber-Pflicht bei Verstößen); ohne Env-Token gibt es keinen Superadmin, eigene Tokens funktionieren weiter.
- **Ablauf**: Bilder fest 90 Tage; Galerie lebt sliding 90 Tage ab letztem
  Upload, danach räumt der Sweep Galerie + Bilder gemeinsam ab.
- **Pool**: weiterhin ein gemeinsamer 20-GB-Pool über alle Galerien
  (voll → 507 Reject, nie Eviction).
- **Breaking** ggü. 1.19.0 (Feature war Stunden alt, 0 Bilder in Prod):
  `POST /pics` lädt nicht mehr in eine globale Galerie hoch, sondern
  braucht `?create=1` bzw. `POST /pics/g/<gid>`; alter Global-Admin-Route
  `/pics/<secret>` entfällt (Superadmin jetzt pro Galerie).
- Tests: 23 pics-Verhaltenstests neu/umgestellt (Gesamt 38 grün),
  `/api` + `/help/pics` + Docs aktualisiert.

---

## 1.19.0 — 2026-09-27

### pics — Event-Galerie (Upload, öffentliche Ansicht, Admin-Kuration)

Neues Feature-Set unter `/pics` (RFQ.md): eine kuratierte Bildgalerie für
Events — freier Upload für Besucher, sofort öffentlich, ein Admin mit
langlebiger Geheim-URL kuratiert (verbergen / löschen / neu sortieren).

- **Upload**: `POST /pics?name=bild.jpg` (roh) oder multipart (Batch).
  Recompression server-seitig: max 2048px, WebP q80, EXIF-Rotation beachtet,
  Originale verworfen. GIFs pass through (Animation bleibt). HEIC/AVIF via
  `pillow-heif`. Max 30 MB pro Bild.
- **Eigener Pool**: 20 GB in `ROOT/pics/` — voll = Uploads werden abgelehnt
  (507), niemals LRU-Eviction. Vollständig getrennt vom 100-MB-Werfen-Pool:
  `total_size()`, `_units()` und `evict()` sehen pics-Einheiten nie.
- **Lifetime**: fest 90 Tage, abgewickelt über `sweep()`.
- **Öffentliche Galerie**: HTML-Grid mit WebP-Thumbs (lazy), Paginierung,
  JS-Upload-Queue mit Retry + 429-Handling; JSON-Listing für Agenten.
- **Admin**: Geheim-Token als Pfad-Segment (`/pics/<secret>`, Env
  `THROWAWAY_PICS_ADMIN_TOKEN`, constant-time-Vergleich). Verbergen → 404
  für alle außer Admin; Löschen → Bytes+Meta+Thumb weg; Reihenfolge
  (up/down) persistent per `order`-Feld. Pool-Auslastungsanzeige.
- **Self-Service**: `/api` um pics_upload/pics_gallery/pics_image/pics_admin
  ergänzt; neues Help-Topic `/help/pics`; pics-Link in der Homepage-Nav.
- **Modularisierung (Schritt 1+2)**: pytest-Suite (34 Verhaltenstests,
  subprocess-basiert) als Regressionsnetz; neuer Code liegt als Modul
  `throway/pics.py` mit kleiner Schnittstelle (`get/post/sweep/api/HELP`)
  statt im Monolith. store.py nur an 8 Punkten minimal berührt.
- **Deploy**: Paket via `rsync` statt einzelner Datei (Ritual in AGENTS.md
  aktualisiert); neu: `pip install pillow-heif` auf lubu, Env
  `THROWAWAY_PICS_ADMIN_TOKEN` in der systemd-Unit setzen.

---

## 1.18.4 — 2026-09-21

### Download-once (Burn-after-Reading) als Option
- **„once“-Checkbox** in der Web-UI — für Upload und Create. Aktiviert
  löscht sich die Datei nach dem **ersten Download** selbst (zweiter GET →
  404).
- **Backend**: `&once=1` für Einzeldateien (roh-body + multipart). Setzt
  `once:true` im Meta; beim GET wird die Datei vor dem Senden entfernt
  (race-sicher, zweiter Zugriff bekommt 404). Nicht kombinierbar mit
  `&share=` (Dirs).
- Docs: `/api`-Spez, `/help/files`, `/help/limits`, `_home_help`, `API.md`,
  `AGENTS.md`, `README.md` um `&once=` ergänzt.

## 1.18.3 — 2026-09-21

### Share-Name wählbar (Upload & Create)
- **Share-Name-Feld** in der Web-UI — für Upload und Create. Optional einen
  einprägsamen Namen (z.B. `my-note`) wählen; der Inhalt landet dann unter
  `/d/<name>` (create-or-get, wie ein benannter Dir) statt unter einer
  zufälligen Hex-ID.
- **Backend**: `&share=<name>` für Einzeldatei-Uploads (roh-body und
  multipart). Nutzt die Dir-Mechanik: Sliding-Lifetime (default 7d, `&ttl=`
  clamped [4h,14d]), validiert wie benannte Dirs (5-32 Zeichen `[a-z0-9-]`,
  >=1 Buchstabe, nicht reserviert).
- Docs: `/api`-Spez, `/help/files`, `/help/limits`, `_home_help`, `API.md`,
  `AGENTS.md`, `README.md` um `&share=` ergänzt.

## 1.18.2 — 2026-09-21

### Create-Text hinter „+" neben Upload (statt Tabs)
- Die Tabs „Upload | Create" sind wieder raus — der bisherige Upload-Bereich
  bleibt unverändert. Stattdessen sitzt **neben dem Upload-Button ein kleines
  „+"**, das die Create-Text-Box ein-/ausklappt (Textarea + Name + Lebensdauer
  + Create-Button). Erneutes Klick auf „+" klappt sie wieder zu.

## 1.18.1 — 2026-09-21

### Create-Text + einstellbare Lebensdauer in der Web-UI
- **„Create“-Tab neben „Upload“**: In der Web-UI gibt es jetzt zwei Tabs —
  **Upload** (bisherige Dropzone) und **Create** (neuer). Im Create-Tab lässt
  sich Text direkt eintragen/pasten und per Klick als Datei hochladen
  (optional mit Name, z.B. `note.txt`). Der erzeugte Link ist sofort
  teilbar.
- **Lebensdauer setzbar**: Standard 4h, aber per Dropdown auf bis zu **14
  Tage (max)** verlängerbar — getrennt für Upload und Create. Der Wert wird
  als `&ttl=<h|d>` mitgeschickt.
- **Backend**: `&ttl=<h|d>` funktioniert jetzt auch für **Einzeldatei-**
  Uploads (roh-body und multipart), nicht nur für Dirs. Clamped auf
  `[4h, 14d]` (max 14 Tage), Default 4h. `expires_in`/`persistence`/`X-Expires`
  spiegeln die gewählte Lebensdauer.
- Docs: `/api`-Spez, `/help/files`, `/help/limits`, `_home_help`,
  `API.md` um `ttl=` für Einzeldateien ergänzt.

## 1.18.0 — 2026-09-19

### Agent-Hints überall (Konsistenz-Durchlauf)
- **Hint-Block auf allen Browser-Seiten** (bisher nur Dir-Listing):
  Bundle-Listing, `/d`-Browse, `/browse`-Files, Help-Index, Help-Topic und
  Dir-History bekommen denselben einklappbaren `<details class=agenthint>`-
  Block mit lauffähigen, absoluten curl-Zeilen. Neu als `_agent_hint()`-
  Helper (escaped selbst), CSS wanderte in `_BASE_CSS` (keine Duplikate).
- **`Link: <…/api>; rel="help"` Header** (RFC 8288) auf **allen JSON-
  Responses** (Listings, Upload-Result, Errors — via `_send`) und auf
  Datei-Downloads für Agent-UA (`_serve_file`): jede maschinenlesbare
  Antwort verrät nun, wo der Vertrag liegt.
- AGENTS.md: Selbst-Discovery-Absatz beschreibt die Hint-Blöcke jetzt
  seitenübergreifend inkl. Link-Header.

## 1.17.0 — 2026-09-19

- **Agent-Hint auf der Dir-HTML-Seite**: einklappbarer `<details>`-Block
  unter der Dateiliste mit lauffähigen curl-Zeilen (JSON-Listing via
  `-A curl`, Einzeldatei, `?zip=1`, PUT/PATCH-Edit, `/history`). Absolute
  URLs — jede Zeile überall copy-paste-bar. Für Menschen dezent
  eingeklappt, im Quelltext/Accessibility-Tree immer voll lesbar (kein
  Cloaking: auch für Menschen nützlich). Schließt die Lücke für Agenten,
  die mit Browser-UA auf der HTML-Seite landen; `-A curl`-Agenten bekommen
  weiterhin direkt das JSON-Listing.

## 1.16.0 — 2026-09-19

### Smartphone-UX (Mobile-First-Nachschlag für Browser-Seiten)
- **Viewport + theme-color überall**: alle HTML-Seiten (Dir-Listing, Bundle-Listing,
  Browse, Help, Help-Topic, History, Copy-for-Agents) hatten teils keinen
  Viewport-Meta → auf Smartphones zoome-out-Desktop-Layout. Jetzt durchgängig
  `<meta name=viewport>` + `theme-color` (einheitliche `_META_MOBILE`-Konstante).
- **Shared Base-CSS (`_BASE_CSS`)** für alle Sekundärseiten: Touch-Ziele ≥ 44px
  (Zeilen, Buttons, Back-Links), Dateinamen mit Overflow-Wrap/Ellipsis statt
  Überlauf, responsive Padding via `@media(max-width:560px)`.
- **Homepage mobil**: Upload-Button + Dir-Checkbox stapeln sich untereinander
  (voll breit, 46px), URL-Eingabefeld in der Result-Box mit 16px Font (verhindert
  iOS Auto-Zoom), kompaktere Dropzone, `touch-action:manipulation` auf Buttons.
- **Native Share-Sheet**: nach Upload erscheint auf Smartphones ein
  „⇗ share link"-Button (`navigator.share`) — Link direkt per WhatsApp/Mail teilen.

### Bild-Vorschauen (Thumbnails)
- **`?thumb=1`** auf Datei-URLs (Einzeldatei, Bundle-Datei, Dir-Datei): liefert eine
  kleine WebP-Vorschau (96px, quality 70, konfigurierbar via
  `THROWAWAY_THUMB_PX` / `THROWAWAY_THUMB_QUALITY`).
- **Hardware-schonend**: Thumbnail entsteht lazy beim ersten Request (Pillow,
  EXIF-Rotation beachtet — Handyfotos stehen richtig herum), wird als
  `<file>.thumb` next to the original gecacht (einmal CPU-Kosten, dann nur
  Lesezugriff), atomarer Replace gegen Races; Fallback auf Original-Bytes bei
  SVG/Nicht-Bild/Fehler. `loading=lazy` + `decoding=async`: Phones laden
  Thumbnails erst beim Scrollen und dann KBs statt MBs (1MB-JPEG → ~72B WebP).
- **Listings mit Vorschau**: Dir-, Bundle- und Browse-Listing zeigen für Bilder
  44px-Vorschaukästchen; menschenlesbare Größen (`1.0 MB` statt Bytes).
- **Aufräumen**: `.thumb`-Dateien zählen nie als Dateien in Listings/JSON/Zips,
  werden zusammen mit dem Original gelöscht (`_remove`, Sweep), Uploads mit
  reservierten Suffixen (`.meta/.history/.thumb/.thumbtmp`) neutralisiert
  (`_safe_name` hängt `_` an), direkter Zugriff auf Bookkeeping-Dateien → 404.

### Fixed
- **Zip sauber**: Dir-/Bundle-Zips enthielten bisher `<key>.history` (und hätten
  künftig auch `.thumb`-Caches mitgeliefert) — Zips enthalten jetzt nur die
  echten Nutzerdateien.
- **500er auf Legacy-Dir-Pfad behoben**: `GET /<dir-id>` (Bundle-Namespace, alte
  Dirs) crashte für Browser mit TypeError (`_dir_listing` bekam falsche Argumente).

### API-Notes
- `/api`: `download`, `download_bundle_file` und `get_dir_file` dokumentieren
  jetzt `?thumb=1`. Agents/JSON unverändert (keine Thumb-Einträge).

---

## 1.15.0 — 2026-09-17

- Repo-Versöhnung: die am Live-Dir entwickelten Features (Bundles/Zip/Minisites,
  Tags+browse, Agent-Endpoints, UA-Negotiation) zurück im main-Branch gemergt —
  Live-Stand == Git wieder.
- SEO: HTML-Head für Browser/Googlebot (Title, Description, Canonical, OpenGraph
  + og-image); Agenten erhalten weiter text/plain (UA-Negotiation).

## 1.14.0 — 2026-08-25

### Added
- **Tags on single files** (wie bisher schon bei Dirs): bei Upload und
  URL-Import per `&tag=<t>` (wiederholbar, max 5, `[a-z0-9-]`, 1–24
  Zeichen); stehen danach in der Upload-Response und in der File-Meta.
- **Tag-Update ohne Rewrite**: `POST /<id>?tag=a&tag=b&untag=c` ändert nur
  die Tags — Content und Expiry bleiben unangetastet.
- **`GET /browse`** — Filtern & Sortieren über alle lebenden Einzeldateien:
  `?tag=<t>[&tag=<t2>]` (UND-Filter), `&q=<substr>` (Name/Tag),
  `&sort=created|name|size|expires`, `&order=asc|desc`. JSON für Agents,
  HTML-Seite für Browser.
- Dokumentiert in `/api` (neue Endpoints `browse_files`, `tag_file`;
  Upload-Response mit `tags`) und `/help/files`.

---

## 1.13.0 — 2026-08-25

### Added
- **Import from URL**: `POST /?url=<encoded-url>[&name=<filename>]` — the
  server fetches the remote http(s) document itself and stores it like a
  normal upload (name from Content-Disposition / URL path, overridable via
  `&name=`; content type from response header, fallback via extension).
  Guard rails: max 5 MB, max 3 redirects (re-validated per hop), private/
  loopback/link-local/reserved hosts blocked (SSRF), 10s timeout.
- **URL as document format (`&link=1`)**: `POST
  /?url=<url>&link=1[&name=<name>]` stores the URL itself as a tiny,
  editable HTML redirect page — browsers are redirected via meta refresh,
  agents can `PUT`/`PATCH` it like any text file.
- Both documented in `/api` (new `import_url` endpoint) and `/help/files`.

---

## 1.12.3 — 2026-08-25

### Added
- **Build config**: alle wichtigen Betriebsparameter sind jetzt zentrale
  Konfigurationsvariablen am Dateianfang von `store.py` und per
  `THROWAWAY_*`-Env-Vars überschreibbar (z.B. im systemd-Unit):
  `THROWAWAY_ROOT`, `_POOL_BYTES`, `_MAX_FILE_BYTES`, `_RATE_LIMIT`,
  `_TTL_HOURS`, `_DIR_MIN_AGE`, `_DIR_MAX_AGE` (das 14d-Max),
  `_DIR_DEFAULT_AGE`, `_DIR_ABS_MAX`, `_HISTORY_LIMIT`, `_MAX_TAGS`.
  Defaults unverändert.
- **`/api`**: neues Feld `dir_ttl_seconds: {min, default, max}` — Agents
  können das 14d-Max nun direkt als Daten lesen statt aus Prosa zu raten;
  `create_dir.note` führt das MAX 14 days vorne an.

---

## 1.12.2 — 2026-08-25

### Changed
- **Docs: 14d dir-TTL-Maximum explizit gemacht** — AGENTS.md, API.md und die
  eingebetteten Agent-Texte (`/api`, `/help/limits`, write-for-agents)
  sagen jetzt ausdrücklich "**MAX 14 days**", damit Agents das Limit nicht
  nur aus der Clamp-Angabe erraten müssen.

---

## 1.12.1 — 2026-08-25

### Changed
- **AGENTS.md: Bumppush-Regel geschärft** — per Default ein **Patch-Release**
  (`x.y.z` → `x.y.z+1`); Minor für Features, Major für Breaking Changes.

---

## 1.12.0 — 2026-08-25

### Changed
- **Dirs: `ttl=` max raised from 7d to 14d.** `&ttl=` at dir creation is now
  clamped to `[4h, 14d]` (was `[4h, 7d]`). Default (no `ttl=`) stays 7 days;
  sliding behavior and the 30-day absolute cap are unchanged. Updated in the
  embedded help, `/api` spec, homepage copy, AGENTS.md, API.md, PRD.md,
  README.md.

## 1.11.0 — 2026-08-23

### Added
- **Paste-to-upload**: press **Ctrl+V** (or Cmd+V) anywhere on the homepage
  and any image on the clipboard is queued in the dropzone for upload — no
  need to save it to disk first. A page-wide `paste` listener grabs `file`
  items from the clipboard, wraps them in a named `File` (deriving a
  sensible name + extension from the MIME type, e.g. `pasted-…png`), and
  calls `dz.addFile()`. Text pastes are left untouched. Multiple pasted
  images queue together and upload in the same POST as any dropped files.

---

### Fixed
- **Homepage upload was completely broken** (click AND drop did nothing):
  the inline script had a JS syntax error (a ternary branch's `)` closed
  the outer paren, leaving `:d.bundle?` dangling), so the browser never
  ran any of it. Found via `node --check` on the extracted script.

### Changed
- **Homepage upload UI now uses [Dropzone.js](https://dropzone.dev)
  (5.9.3) via CDN.** Battle-tested click-to-browse + drag-and-drop with
  file previews, progress and per-file remove — replacing the hand-rolled
  dropzone. `maxFilesize` is injected from the server's `MAX_FILE`, so the
  client limit always matches the real one.
- **Homepage JS modularized**: small named functions (`esc`, `row`,
  `filesBlock`, `showResult`, …) instead of one nested string-concat
  expression; upload URL derives from `location.pathname`, so the page
  works under any mount point (root or `/throway` behind the proxy).
- Upload still sends **one multipart POST for the whole queue** → file,
  bundle, or dir (`?dir=1`) exactly as before. Result box unchanged.

### Note
- The homepage now loads Dropzone.js from jsDelivr; if the CDN is
  unreachable the upload card won't function (agents/curl unaffected).

---

## 1.9.4 — 2026-08-20

### Fixed
- **Homepage upload dropzone: drag-and-drop uploads now work.** The drop
  handler assigned the read-only `input.files = ev.dataTransfer.files` file
  list directly, which silently becomes an empty `FileList` in some
  browsers, so dropping a file onto the card showed an empty list and the
  upload reported "Choose at least one file". The handler now copies the
  dropped files into a fresh `DataTransfer` and assigns `input.files =
  dt.files`, the standard cross-browser technique (Chrome + Firefox).
  The file-picker path and direct API uploads already worked.
- **Version is now single-sourced from `store.py` `VERSION`.** The
  "Current version" line served on the releases page is derived from the
  code constant at request time, so it can never drift from the running
  build.

---

## 1.9.3 — 2026-08-20

### Fixed
- **Dir files with spaces in their names now fetch correctly.**
  `GET /d/<key>/<file>` (and `PUT`/`PATCH`/`DELETE`) now URL-decode the
  filename segment before matching, so a `%20` in the request matches the
  literal space in the stored name. Previously these returned `404` even
  when the filename was correctly percent-encoded; only space-free names
  worked. Same fix applied to per-file fetches from bundles.
- **Dir/bundle listings now return URL-encoded `files[].url` values**
  (e.g. `%20` instead of a raw space), so the URLs the server hands back
  actually work when fetched by programmatic consumers.

---

## 1.9.1 — 2026-08-18

### Changed
- **Homepage stats: two cards** — each shows files + size. Card 1 is the
  current live state ("now"); card 2 is activity since this server started
  ("since start"), a RAM-only counter reset on each restart. Replaces the
  old four cards (files now / stored / files ever / uploaded ever).

---

## 1.9.0 — 2026-08-18

### Changed
- **Unified the two dir types into one concept.** Mutable dirs (`/<dirid>`,
  sliding 4h/24h) and named dirs (`n/<name>`) are now a single **dir** under
  `/d/<key>`, addressable by an opaque id or a memorable name. One storage
  layout, one TTL model (fixed lifetime, default 7 days, `ttl=` clamped to
  [4h, 7d]), no duplicated code.
- All dir endpoints moved under `/d/`: `POST /d/<key>` (add files),
  `GET /d/<key>` (listing), `GET /d/<key>/<file>`, `PUT`/`PATCH`
  `/d/<key>/<file>` (edit/append), `DELETE /d/<key>[/<file>]`,
  `GET /d` (list `listed=1` dirs).
- **Edit history per dir.** `GET /d/<key>/history` returns the last
  `HISTORY_LIMIT` (50) entries, newest first — date, file, action
  (`add`|`put`|`append`|`delete`) and byte deltas. Lightweight by design:
  no full-text versions, no revert. JSON for agents, HTML for browsers.
- Removed the old `n/` namespace and the short-lived mutable dirs; existing
  real dirs were migrated from `n/<name>` to `d/<name>`.
- Responses carry `editable` (per file) and a `persistence` block (`type`,
  `expires_at`, `extendable_by`, `max_age`) so agents can discover
  editability and lifetime from the response instead of guessing.

---

### Changed
- **Canonical URL is now `https://skale.dev/throway`.** The front door moved
  to amd2 (`158.180.42.218`), which proxies `/throway/` to lubu and
  terminates TLS. `skale.dev` and `www.skale.dev` both point there; the root
  `/` redirects to `/throway/`.
- `PUBLIC_BASE` default is `https://skale.dev/throway`; all generated URLs
  (upload responses, dir/named-dir listings) use it. The old
  `https://lubu.skale.dev/throway` still works via the env override on lubu.
- Docs (AGENTS/API/README/PRD) updated to the new canonical URL.

---

## 1.6.1 — 2026-08-12

### Changed
- **Homepage now returns a structured `--help` summary to agents.** Curling
  the root (`GET /throway/` as a non-browser client) returns a compact usage
  overview plus pointers to where to get more — the full guide
  (`/write_for_agents`), the API index (`/api`), and per-topic help
  (`/help`, `/help/<topic>`). ~1 KB instead of the full ~5 KB blob.

---

## 1.6.0 — 2026-08-12

### Added
- **Modular, API-gatherable help** (`/help`). Instead of one giant
  copy-paste blob, help is split into topics served individually at
  `/help/<topic>` (`overview`, `files`, `bundles`, `dirs`, `named_dirs`,
  `view`, `edit`, `delete`, `limits`, `contract`).
  - `GET /help` → JSON index of topics (for agents) or an HTML list (browsers).
  - `GET /help/<topic>` → that topic as plain text (agents) or a rendered
    page (browsers). Unknown topics → 404.
  - Agents fetch only the pieces they need instead of one big blob.
- **Single source of truth**: the `/write_for_agents` description and the
  `/help/*` topics are assembled from the same `HELP` dict — no duplication.

---

## 1.5.0 — 2026-08-12

### Added
- **Named dirs** (`/n/<name>`). A mutable dir addressed by a memorable name
  instead of an opaque hex id, so a team of agents can remember and reuse one
  shared dir.
  - **Create-or-get**: `POST /?dir=1&name=<name>` is idempotent — any agent
    calling the same create converges on the shared dir.
  - **Naming ruling**: 5-32 chars, `[a-z0-9-]`, must contain a letter, no
    reserved words (`api`, `index`, `n`, `releases`, `llms`, `store`, …).
  - **Create flags** (immutable at create): `&listed=1` (appears in `GET /n`),
    `&tag=<t>` (up to 5 discoverability tags), `&ttl=<h|d>` (fixed lifetime).
  - **Fixed lifetime**: `expires_at` set at creation (default 7 days, `ttl=`
    overrides, clamped to [4h, 7d]) and **never moves** — add/edit/delete do
    not extend it.
  - **`updated_at`** tracks last add/edit/delete (does not affect lifetime).
  - **Full CRUD** on `n/<name>`: add files, fetch, zip, PUT/PATCH text edit,
    delete file or whole dir.
  - **Listing `GET /n`** (only `listed=1` dirs): filter by `?q=` (name/tag
    substring), `?created_after/before`, `?updated_after/before`; sort by
    `?sort=created|updated|name&order=asc|desc`. JSON for agents, HTML for
    browsers.
  - **Privacy**: unlisted by default; names never enumerated outside `GET /n`.

---

## 1.4.2 — 2026-08-12

### Fixed
- **Dir/bundle listing 404s in a browser.** The HTML listing pages for dirs
  and bundles (served at `/throway/<id>` with no trailing slash) now inject a
  `<base href="/throway/<id>/">` tag, so relative links (`a.txt`, `b.txt`)
  resolve against the dir/bundle directory instead of the parent path.
  Previously clicking any item in a dir or bundle listing 404'd.

### Changed
- `PUBLIC_BASE` is now overridable via the `THROWAWAY_PUBLIC_BASE` env var
  (default `https://skale.dev/throway`), making it easy to switch the public
  URL without editing code.

---

## 1.4.1 — 2026-08-12

### Changed
- Polished the web UI across all pages: a clean white design with drag &
  drop upload, one-click URL copy, a "create a dir" toggle, feature cards,
  and a stats grid on the landing page. Releases and bundle/dir listing
  pages match the same minimal white look.

---

## 1.4.0 — 2026-08-12

### Added
- **Mutable dirs.** Create a dir (`POST /?dir=1`), keep adding files to it
  (`POST /<dirid>`), and it's deleted **4h after the latest upload** (capped
  at 24h total). `GET /<dirid>` returns a JSON listing to agents / an HTML
  page to browsers; `?zip=1` downloads the whole dir; files are fetched and
  deleted individually.

### Fixed
- **Bundle sub-resource 404s in a browser.** A bundle's `index.html` is now
  served with an injected `<base href="/throway/<id>/">` tag, so relative
  URLs (`style.css`, `app.js`, images) resolve against the bundle directory
  instead of the parent path. Previously every multi-file bundle rendered
  unstyled/broken in a browser.

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
