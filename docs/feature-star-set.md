# Feature-Idee · Fotos starren & als Set teilen

**Status:** Entwurf zur Review — noch nicht implementiert.
**Ziel-Galerie:** `skale.dev/throway/pics/g/soundspritzer` (und jede andere Pics-Galerie).
**Anlass:** soundspritzer.at embeddet diese Galerie. Gäste sollen ein paar Fotos
auswählen und **als Set über Social Media teilen** können — der geteilte Link
zeigt denselben View: die gestarrten Fotos zuerst, dann der Rest.

---

## 1 · Problem

Heute kann man die Galerie nur als Ganzes teilen (`/pics/g/soundspritzer`).
Will ein Gast drei Lieblingsfotos als „so war's" posten, gibt es keinen Weg:
Er muss drei Einzel-Links raussuchen oder die ganze Galerie teilen. Es gibt
zwar Likes (öffentlich gezählt), aber kein Konzept für eine **persönliche,
teilbare Auswahl**.

**Ziel:** Ein Gast markiert ein paar Fotos („starren"), tippt „Als Set teilen"
und bekommt einen Link, der genau diese Auswahl zeigt — die gestarrten zuerst.

---

## 1b · Zwei Teilen-Wege

Es gibt **zwei** Teilen-Modi, die sich ergänzen:

| | **Standard-Teilen** | **Spezial-Teilen (Set)** |
|---|---|---|
| Was | Galerie als Ganzes | Nur die gestarrten Fotos |
| Button | bestehender Teilen-Button | „Als Set teilen" (erscheint bei ≥1 Stern) |
| Link | `/pics/g/soundspritzer` | `/pics/g/soundspritzer?stars=<pid1>,<pid2>…` |
| Wann immer verfügbar | ja | nur wenn der Teiler gestarrt hat |

**Standard-Teilen bleibt unverändert** — die Galerie als Ganzes teilen
(der bisherige Weg, z. B. der bestehende Share-Button). **Spezial-Teilen ist
der neue Weg:** sobald jemand eigene Bilder gestarrt hat, bekommt er die
Option, genau diese Auswahl als Set zu teilen. Der geteilte Link zeigt die
gestarrten zuerst, dann den Rest.

Beide Wege führen auf dieselbe Galerie; der Unterschied ist nur, ob die
Auswahl (`?stars=`) im Link steht. Ohne `?stars=` verhält sich die Galerie
exakt wie heute.

---

## 2 · Kontext — wie die Galerie heute funktioniert

- Galerie = Python-App (`throway/pics.py`), soundspritzer.at embeddet sie per
  `<iframe>` (Auto-Höhe + Lightbox-Handshake).
- **Likes** sind das einzige soziale Element: serverseitig, pseudonym
  (Fingerprint aus IP+UA, keyed per Galerie-Token), öffentlich gezählt,
  `?sort=likes` rankt danach. Browser togglen per `POST /pics/i/<id>?like=1`.
- Sortierung: `_sorted_visible` — kuratierte Reihenfolge (`order`), optional
  `?sort=likes` (meiste Likes zuerst, Ties = kuratierte Reihenfolge).
- Bilder heißen `pid` (16-hex, z. B. `a1b2c3d4e5f60708`).
- Es gibt **kein** Selektion-/Set-/Favoriten-Konzept, keine Accounts, kein
  Login. Alles „läuft ab" (Galerie 90 Tage).

---

## 3 · Kernentscheidungen

### 3a · Star ≠ Like — eine eigene, persönliche Aktion

Likes sind **öffentlich** (gezählt, ranken die Galerie). Ein Star ist eine
**private Auswahl** zum Teilen. Zwei getrennte Konzepte:

| | Like | Star |
|---|---|---|
| Zweck | Beliebtheit zeigen | Persönliche Auswahl teilen |
| Sichtbarkeit | öffentlich (Zähler) | privat (nur im geteilten Link) |
| Persistenz | serverseitig (Fingerprint) | **clientseitig (localStorage)** |
| Zählt öffentlich | ja | nein |

**Empfehlung:** Star rein clientseitig (localStorage), analog zu den Likes im
Browser (`ty_likes_<gid>`). Kein Server-Roundtrip, kein Rate-Limit, kein
Cleanup, kein „wer darf das Set löschen"-Problem. Der Star ist ein
Bookmark/Selektionsmerkmal, kein Social-Beweis.

### 3b · Stateless Auswahl in der URL — kein Server-State

Die Auswahl wird **in den Link kodiert**, nicht serverseitig gespeichert:

```
/pics/g/soundspritzer?stars=<pid1>,<pid2>,<pid3>
```

**Warum stateless (statt Set-Objekt mit `?set=<id>`):**

- **Kein Server-State, kein Cleanup.** Die Galerie läuft ab; ein Set-Objekt
  müsste mitgesweept werden, brauchte ein Admin-/Token-Konzept (wer darf es
  löschen?) und einen Create-Endpunkt. Das widerspricht dem Throwaway-Prinzip.
- **Funktioniert sofort überall.** Der Teiler „baut" den Link selbst aus seinen
  localStorage-Sternen. Kein Endpunkt, keine Auth, keine Vorbedingung.
- **URL-Länge ist ok.** 10 Fotos = `?stars=` + 10×16-hex ≈ 175 Zeichen — für
  einen Share-Link völlig akzeptabel. (Für 100+ Fotos wäre es zu viel — dann
  lohnt die stateful Variante. Für „ein paar Fotos" ist stateless richtig.)

**Konsequenz für den Empfänger:** Der Link zeigt die im Link stehenden PIDs
zuerst. Der Empfänger kann zusätzlich **selbst** starren und einen eigenen
(ggf. kombinierten) Link bauen — das ist ein Bonus, kein Muss.

### 3c · Sortierung im Empfänger-View

Der Empfänger-View (`?stars=…`) zeigt:

1. Die gestarrten Fotos **zuerst**, in der Reihenfolge des Links
   (`?stars=b,a,c` → b, a, c — der Teiler bestimmt die Reihenfolge).
2. Danach der **Rest** in der bisherigen Reihenfolge (kuratiert bzw.
   `?sort=likes`, falls gesetzt).

Die gestarrten Fotos bleiben im Grid als „gestarrt" markiert (Stern gefüllt),
damit der Empfänger sie sofort erkennt.

### 3d · Was passiert bei `?stars=` mit unbekannten/abgelaufenen PIDs?

Robustheit: PIDs im Link, die nicht (mehr) existieren oder versteckt sind,
werden **still übersprungen** — kein Fehler, kein 404. Der Link bleibt gültig,
auch wenn ein Bild inzwischen abgelaufen ist. (Galerie läuft 90 Tage, Bilder
können vorher einzeln gelöscht werden.)

---

## 4 · UX-Fluss

### Teiler (Gast)

1. Öffnet die Galerie, **sternt** 3–10 Fotos (Stern-Button auf jedem Thumb +
   in der Lightbox, analog zum Like-Button).
2. Ein **„Als Set teilen"**-Button erscheint (Header/Toolbar), sobald ≥1 Stern
   gesetzt ist — mit Zähler: „Als Set teilen (3)".
3. Tippt darauf → `navigator.share({ url: "<galerie>?stars=…" })` (Web Share
   API, wie die bestehenden Share-Buttons). Fallback: Link kopieren.
4. Beim Teilen wird die Auswahl **live** in die URL geschrieben — der geteilte
   Link zeigt exakt das, was der Teiler gerade gestarrt hat.

### Empfänger

1. Öffnet den Link → Galerie zeigt die gestarrten Fotos zuerst (markiert),
   dann den Rest.
2. Ein Hinweis oben: „Diese Fotos wurden als Set geteilt — starre deine
   Favoriten und teile dein eigenes Set." (motiviert zum Mitmachen, s. §5)
3. Der Empfänger kann selbst starren und teilen.

### Ohne JS / ohne Web Share

- Ohne Web Share API → „Link kopieren" mit Feedback (bestehendes Muster).
- Ohne JS → die Galerie lädt normal; `?stars=` wird beim Rendern serverseitig
  ausgewertet (die Sortierung ist im HTML), nur der Star-Button und der
  Teilen-Button sind progressive Enhancement.

---

## 5 · Motivation — aktives Starren anregen

Der User will, dass Gäste **aktiv** starren. Drei Hebel, kombiniert:

1. **Sichtbarer Einstieg:** Ein kurzer Hinweis direkt über dem Grid
   (bzw. im Embed oben): „★ Sterne deine Lieblingsfotos und teile sie als
   Set." — klein, nicht aufdringlich, mit dem Stern-Symbol.
2. **Sofortige Belohnung:** Der „Als Set teilen"-Button erscheint live mit
   Zähler, sobald der erste Stern gesetzt ist („Als Set teilen (1)" → „(3)").
   Das macht den Wert der Aktion sofort sichtbar.
3. **Sozialer Beweis im Embed:** Auf `sort=likes`-Seiten (die soundspritzer.at
   embeddet) könnte ein Satz stehen: „Die beliebtesten Bilder stehen oben —
   starre deine Favoriten und teile sie." Verknüpft Starren mit dem, was die
   Leute schon tun (liken).

**Ton:** positiv, kurz, kein „Pflicht"-Charakter. Der Hinweis soll einladen,
nicht belehren.

---

## 6 · SOTA-Vergleich (wie machen es die Großen?)

| Plattform | Mechanik | Was wir daraus lernen |
|---|---|---|
| **Google Photos** | Album auswählen → „Teilen" → Link auf genau dieses Album | Auswahl als eigene, teilbare Einheit; Link zeigt exakt die Auswahl. |
| **Instagram** | Collections (privat) / Einzelpost teilen | Auswahl ist persönlich; Teilen über den System-Sheet. |
| **Flickr** | Alben/Collections, kuratierte Reihenfolge | Der Teiler bestimmt die Reihenfolge der Auswahl. |
| **iCloud Shared Albums** | Auswahl teilen, Empfänger sehen genau diese Fotos | Geteilter View == Teiler-View („derselbe View"). |

**Gemeinsamer Nenner (unser Zielbild):** *Der geteilte Link zeigt exakt die
Auswahl des Teilers, in der Reihenfolge des Teilers.* Genau das liefert
`?stars=…` stateless.

**Worin wir bewusst abweichen:** Google Photos & Co. speichern Alben
serverseitig (Accounts!). throway hat keine Accounts und läuft ab — deshalb
die stateless URL. Das ist für „ein paar Fotos" die richtige Vereinfachung.

---

## 7 · Technische Umsetzungsskizze

Alles in `throway/pics.py` (Galerie-Modul). Kein neues Modul nötig — es ist
eine Erweiterung von Sortierung + einem Button.

### Backend (serverseitig, kleine Änderungen)

- **`_get_gallery`:** `?stars=` parsen → `stars = [pid…]` (validieren: nur
  hex, dedupe, Reihenfolge behalten). Die `items`-Liste so sortieren, dass
  `stars`-PIDs zuerst kommen (in Link-Reihenfolge), dann der Rest.
- **`_sorted_visible`** bekommt optional `stars` als Prioritätsliste.
- **`embed_html` / `gallery_html`:** übernehmen die neue Sortierung; das Grid
  markiert gestarrte Zellen (`data-star`).
- **Agent-JSON** (`_get_gallery` für `curl`): `?stars=` wird als
  `selected: [pid…]` in der Antwort gespiegelt, damit Agenten die Auswahl
  nachvollziehen können.

### Frontend (im Galerie-HTML/JS)

- **Stern-Button** pro Thumb (`.st`, analog `.lk`): toggelt `ty_stars_<gid>`
  in localStorage. In der Lightbox ein Stern-Button (analog `#lblk`).
- **„Als Set teilen"-Button** in der Toolbar: erscheint bei ≥1 Stern, zeigt
  Zähler, baut `?stars=` aus localStorage, teilt via `navigator.share` /
  kopiert.
- **Markierung:** gestarrte Zellen bekommen eine gefüllte Stern-Optik
  (auch im Empfänger-View, damit die Auswahl sichtbar ist).

### Keine Änderungen an soundspritzer.at nötig

soundspritzer.at embeddet die Galerie per iframe. Das Feature lebt komplett
in throway. Der geteilte Link geht auf `skale.dev/throway/pics/g/soundspritzer?stars=…`.

---

## 8 · Offene Fragen (bitte entscheiden)

1. **Reihenfolge der gestarrten:** Soll der Link die Reihenfolge des Teilers
   widerspiegeln (`?stars=b,a,c` → b,a,c) oder immer in der Reihenfolge, in
   der sie gestarrt wurden? **Empfehlung:** Link-Reihenfolge = Teilereihenfolge
   (einfachste, intuitivste Variante).
2. **Kombination:** Wenn der Empfänger eigene Sterne hat und teilt — sollen
   seine + die im Link kombiniert werden, oder nur seine? **Empfehlung:** nur
   die eigenen (der Link ist eine frische Auswahl des Teilers). Kombinieren
   wäre verwirrend.
3. **Star auch als „Favorit" speichern?** Soll der Star dauerhaft im Browser
   des Teilers bleiben (localStorage), damit er seine Auswahl später wieder
   sieht / erweitern kann? **Empfehlung:** ja — wie Likes, localStorage
   überlebt einen Reload.
4. **Zähler öffentlich?** Sollen gestarrte Fotos öffentlich gezählt werden
   („12 Leute haben das gestarrt")? **Empfehlung:** nein — Star ist privat.
   Öffentliche Zähler sind Likes. (Falls doch gewünscht: wäre ein
   Server-State-Konzept, das §3b vermeiden will.)

---

## 9 · Abgrenzung / bewusst NICHT

- Kein „Collection"-Verwaltung (Sets benennen, mehrere Sets, teilen/übernehmen).
- Kein Server-State, kein Set-Objekt, kein Admin-Token für Sets.
- Keine öffentlichen Star-Zähler (das wäre Like-Doppelung).
- Kein Multi-User-Kollaboration (mehrere Leute teilen sich ein Set).
- Kein Upload in ein Set (Set ist eine Auswahl bestehender Bilder).

Das ist bewusst auf „ein paar Fotos auswählen und als Set teilen" geschrumpft —
der Kern-Wunsch aus soundspritzer.at.

---

## 10 · Prozess-Lektion (für zukünftige Feature-Ideen)

**Kernentscheidungen als Entscheidungsblock, nicht als offene Frage.**

Diese Idee brauchte drei Review-Runden, weil die wichtigste Architektur-
Entscheidung — **zwei Teilen-Wege** (Standard-Teilen der Galerie als Ganzes
vs. Spezial-Teilen der gestarrten Auswahl) — anfangs als offene Frage
formuliert war. Der Requester musste sie dreimal präzisieren („auf der
Website" → „zwei Modi" → endgültig).

**Regel für die nächste Idee:**
- Die **eine** Entscheidung, die die Architektur bestimmt (hier: wo das
  Feature lebt, wie viele Wege es gibt), gehört als **Entscheidung** in den
  Kernentscheidungen-Block (§3) — mit Begründung, nicht als offene Frage.
- Offene Fragen (§8) nur für **nachrangige** Punkte (Reihenfolge, Kombination,
  Persistenz-Detail), die die Richtung nicht ändern.
- Wenn eine Entscheidung die Richtung umkippen kann, ist sie keine offene
  Frage — sie ist der Kern und gehört nach oben.

Das spart Review-Runden: Der Requester entscheidet die Richtung einmal,
statt sie im Review mehrfach neu zu justieren.
