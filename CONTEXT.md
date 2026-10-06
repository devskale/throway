# CONTEXT.md — throway domain glossary

Kleine, präzise Sprache des Projekts. Implementierungsfrei: nur Begriffe
und ihre Bedeutung. Erste Fassung mit der dirs-Extraktion (1.49.0); Begriffe
wachsen, wenn Gespräche sie schärfen.

## Objekte

**File** — Eine Einzeldatei unter `/<hex-id>`. Feste Lifetime (Default 4 h,
`ttl=` bis 14 d), Metadaten in `<id>.meta`. Kleinste Einheit des Pools.

**Bundle** — Mehrere Dateien aus einem Multipart-Upload unter `/<hex-id>/`;
feste Lifetime wie ein File. Engine-Objekt: kein Sliding, keine Write-Gates.

**Dir** — Ein Workspace-Verzeichnis unter `/d/<key>` (`key` = opaque Hex-Id
oder Name), adressierbar per Id ODER Name. Was einen Dir ausmacht:
- **sliding lifetime** — jede Aktivität (add/edit/delete) schiebt
  `expires_at` um das `ttl` nach vorn, gedeckelt am absoluten Maximum
  (30 d ab Creation); ein aktiver Dir lebt, ein idle Dir stirbt.
- **create-or-get** — benannte Dirs: zweiter Create-Aufruf liefert das
  existierende Objekt zurück; Flags wirken nur bei der ersten Erzeugung.
- **History** — `<key>.history` protokolliert jede Mutation (add|put|
  append|delete mit Byte-Deltas), begrenzt auf die jüngsten Einträge.
- **Meta-Format** — `<key>.meta` ist das Manifest (type, files, tags,
  lifetime, write_token, retain). Das Format gehört dem dirs-Modul.

**Share** — Eine Ein-Datei-Dir unter selbstgewähltem Namen (`?share=`);
nutzt die komplette Dir-Maschinerie (sliding, create-or-get, write-gates).

**Show-dir** — Ein Dir, der retained (unbefristet) UND öffentlich
beschreibbar ist (`open: true`). Whole-dir-Delete bleibt token-gepflöckt.

**Gallery (pics)** — Ereignis-Galerie unter `/pics` mit eigenem Pool,
eigenen Limits, 90 Tagen pro Bild, Admin per Galerietoken. Eigene Domäne,
kein Dir.

## Lifetime & Schutz

**Retention** — Token-gepflöste Unbefristetheit: tokenauthentifizierte
Uploads/Writes erzeugen oder flippen Objekte auf `retain` („write implies
retention"); retained Objekte sind public-read, token-gated-write, vom
Sweep und Pool-Eviction ausgenommen.

**Write token** — Optionale Dir-Schreibprotektion (`&write=1` bei
Erzeugung, genau einmal gezeigt). Schreibzugriffe brauchen danach
`X-Throway-Write`-Header oder `?write=`, konstantzeitverglichen.

**Pool** — Das Wegwerf-Budget (Default 100 MB, LRU-evicted). Pics hat
einen eigenen Pool (reject-at-full, nie evicted). Die Pools sehen sich
gegenseitig nicht.

## Architektur-Begriffe

**Namespace** — Ein Route-/Storage-Präfix mit eigener Domäne: `d` (dirs),
`pics` (Galerien). Namespace-Module leben in `throway/` (pics, dirs) und
besitzen ihre Routen-Domäne, ihre HELP-Topics und ihren Storage.

**Querschnitts-Modul** — Ein Modul ohne eigene Route, das ein Konzept
über alle Namenspaces legt (retain: Token-Gates). Dispatcher-nahe
Flip-Routen dürfen in store.py bleiben; die Konzeptlogik lebt im Modul.

**Storage-Mechanik** — Die eine geteilte Mechanik unter allen Namespaces
(`throway/storage.py`, 1.52.0): `atomic_json` (tmp + os.replace — Leser
sehen nie Halbdateien) und `locked(ns)` (pro-Namespace-RLock um
load→mutate→save). Pools, Accounting und Eviction bleiben pro Namespace —
geteilt ist nur die Mechanik. Lock-Reihenfolge bei Schachtelung:
pics → files; Sweeps und Bildverarbeitung bleiben bewusst ungelockt.

**Request-Kit** — Die eine Naht zwischen Handler und den Modulen: das
explizite Objekt (Senden, Request-Kontext, geteilte Helfer, Konfig), das
Module statt des Handlers erhalten. Geboren mit dirs (1.49.0).

**Agent** — Nicht-Browser-Client (curl, Skripte, Bots). Agents bekommen
JSON und Agent-Hinweise; Browser bekommen HTML. `?json=1`/`?html=1`
überschreiben die UA-Heuristik.

**Card** — Die Social-Preview-Repräsentation (`og:`/`twitter:`-Meta)
für Card-Crawler (Twitterbot, facebookexternalhit, Slackbot, Discordbot,
…). Server-gerenderte Seiten (Docs, Dir-Listings, Bundle-Index) tragen
sie für alle; auf rohen HTML-Uploads wird sie nur Crawlern injiziert —
eigene `og:`-Tags des Uploads gewinnen, Browser/Agents bekommen die
Bytes byte-identisch (`Vary: User-Agent` auf der Crawler-Variante).
Lebt in `throway/og.py` (1.54.0).

**Homepage** — Die Upload-UI unter `/` (nur Browser; Agents bekommen den
Klartext-Help): Files/Text/Link/Gallery-Tabs, Dropzone, Pool- und
Session-Stats, eingebettete Agent-Info. Lebt als reines Präsentations-
Modul (`throway/index.py`, `page(stats)`) über einem flachen Stats-Dict;
Engine-Zustand sammelt der Handler.
