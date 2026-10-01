# ADR: Wo lebt die Lightbox — im Embed-iframe oder im Host?

**Status:** vorgeschlagen (offen) · **Datum:** 2026-10-01 · **Bezug:** 1.38.2–1.39.2

## Kontext

Die pics-Galerie wird per `<iframe>` in Fremdseiten eingebettet
(soundspritzer.at). Die Auto-Höhe macht den iframe so hoch wie seinen
Inhalt (bei 102 Bildern ~9000px). Damit hat der iframe **keinen
sichtbaren Viewport** — `position:fixed`, `vh`/`dvh` und
`IntersectionObserver` referenzieren die *gesamte* Inhaltshöhe.

Daraus entstanden drei echte Nutzer-Bugs (alle behoben):

1. **1.38.2** — Lightbox streckte sich über die volle iframe-Höhe,
   Bild 0×0, Controls tausende Pixel unterhalb des Screens.
   Fix: Host postet seine echte Viewport-Höhe (`throway:pics:viewport`).
2. **1.38.4** — Lightbox saß am iframe-Anfang, bei tief gescrollter
   Host-Seite off-screen. Fix: Host postet zusätzlich `offset`.
3. **1.39.0** — Infinite-Scroll lud alle Seiten sofort, weil sein
   Limit `innerHeight` war. Fix: Nachladen folgt dem vom Host
   gemeldeten sichtbaren Ausschnitt.

Alle drei Fixes hängen an derselben Grundwahrheit: **das iframe muss
den Host fragen, wo der Bildschirm ist.** Der Host-Snippet-Vertrag
(`throway:pics:viewport` + `throway:pics:ready`) ist die einzige
saubere Informationsquelle — er ist aber **optional**, also gibt es
Fallbacks (`screen.height`), die ungenau sind.

## Optionen

### A — Lightbox bleibt im iframe (Status quo)
- **Für:** Likes/Kommentare/Historie bleiben in einer Hand; der Host
  muss nichts tun; ein URL, alles drin; keine Rechte über den Host.
- **Wider:** Positions- und Lazy-Load-Logik braucht den
  Host-Handshake; alte Snippets degradieren auf `screen.height`
  (sichtbar zu tief); das Bild-Handling (z. B. mal nichts) hängt an
  cross-origin-Frames.

### B — Lightbox im Host öffnen (Top-Level, Bild per URL)
- **Für:** echtes `position:fixed` gegen den echten Viewport,
  `dvh` funktioniert, keine Handshake-Posts nötig; der Host kann das
  Design an seine Seite anpassen.
- **Wider:** Der Host muss Lightbox-Code selbst besitzen (throway
  liefert ihn nicht aus); Likes/Kommentare/Historie verlassen die
  iframe-Grenze; ein Gast, der nur das iframe kopiert, bekommt gar
  keine Lightbox. throway kann das nicht erzwingen — es ist eine
  **Zusammenarbeits-, keine Technikfrage**.

### C — Hybrid: Handshake heute, Top-Level-Lightbox als Angebot
- **Für:** B ist verfügbar, ohne A zu gefährden (A ist Default).
  Docs/Help bieten ein optionales Snippet für B an.
- **Wider:** zwei Wahrheiten, mehr Doku-Pflege; B ist ohne Host-Code
  wirkungslos.

## Entscheidung (Vorschlag)

**Bleibe bei A (Status quo) für die 1.39.x-Linie**, aus drei Gründen:

1. **Nutzersymptom ist behoben.** Der Handshake löst 1–3; die
   Genauigkeit reicht in der Praxis (exakte Host-Viewport-Höhe und
   -Offset, nicht geraten).
2. **throway ist self-service, iframe-first.** Ein Lightbox-Vertrag,
   den nur Hosts mit eigenem Code nutzen können, verletzt das
   Kernprinzip: „ein URL, alles drin, kein Setup". Ein Gast, der nur
   den iframe teilt, soll die volle Funktionalität behalten.
3. **B ist eine Host-Entscheidung, keine throway-Freigabe.** Wenn
   soundspritzer.at später eine Top-Level-Lightbox will, ist das eine
   Änderung in *diesem* Repo, geteilt als `throway`-Feature über
   `/pics/i/<id>` (Bild-URL existiert bereits und ist öffentlich).

B wird als **Dokumentations-Angebot** in Betracht gezogen (C), wenn
es mindestens einen zweiten Host gibt, der es will.

## Konsequenzen

- Der Handshake-Vertrag bleibt Teil des Embed-Snippets (versioniert über
  die Nachrichtentypen; `pics:ready` macht ihn race-frei).
- Fallbacks bleiben Pflicht, weil der Vertrag optional ist.
- Wenn ein Host den Handshake nachbaut, ist er heute ~10 Zeilen JS.