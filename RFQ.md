# RFQ — Bild-Galerie für Events: „pics" (Feature von throway)

**Status:** ✅ Umgesetzt in **1.19.0** (2026-09-27) · Abgenommen — alle OFFEN-Punkte entschieden · **Quelle:** Besprechung mit Johann
**Deployment-Ziel:** lubu `/srv/storage2` = **389 GB frei** → 20-GB-Pool unkritisch (2 Messung, 5 % Belegung)

---

## 1. Überblick & Ziel

Ein schlanker Bild-Sharing-Dienst für **Events**: Besucher laden Bilder hoch,
Besucher schauen sie an, ein Admin kuratiert (verbergen, löschen, Reihenfolge).
Keine Accounts, keine Registrierung — freier Upload wie bei throway, aber
**persistent mit fester Lebensdauer statt 4-Stunden-Ephemeralität**.

**pics ist ein Feature von throway** (OFFEN-7): gleiche Datei, gleicher
Service, gleiches Deployment — Route `skale.dev/throway/pics`. Throway
bleibt in seinem Verhalten unverändert; die Galerie bekommt einen **eigenen
Namespace mit eigenem Pool-Budget** und eigene Lifetime-Politik (TR-1).

---

## 2. Rollen

| Rolle | Zugang | Kann |
|---|---|---|
| **Besucher** | öffentliche URL | Galerie ansehen, Bilder einzeln ansehen, Bilder hochladen |
| **Uploader** | = Besucher | eigene Uploads sofort sichtbar (kein Freigabe-Schritt) |
| **Admin** | geheime Admin-URL | verbergen, endgültig löschen, Reihenfolge ändern, einblenden |

---

## 3. Funktionale Anforderungen

### Upload (FR-1 … FR-6)

- **FR-1** Freier Upload ohne Login. Upload via Web-UI (Drag & Drop,
  Dateiauswahl) und via HTTP-API (`POST` raw/multipart), damit auch
  Skripte/Agenten hochladen können.
- **FR-2** **Batch-Upload: 1000 Bilder müssen in einem Rutsch hochladbar sein.**
  Client-seitige Upload-Queue mit Fortschritt, Einzelfehler isoliert
  (ein scheiterndes Bild bricht den Batch nicht ab), automatischer Retry.
- **FR-3** Formate: JPEG, PNG, WebP, GIF. HEIC (iPhone-Fotos) siehe
  **OFFEN-3**.
- **FR-4** **Max. Größe pro Original-Upload: 30 MB** (Event-Kamera-JPEGs sind
  10–25 MB; throways 5-MB-Limit reicht dafür nicht).
- **FR-5** **Serverseitige Komprimierung** nach Empfang (1.26.0: nur wenn
  nötig): Bilder **≤ 2048 px** werden **byte-identisch** gespeichert —
  JPEG-Metadaten (EXIF/GPS/XMP/IPTC) werden lossless aus dem Container
  geschnitten, Pixel bleiben 100% identisch. Nur **größere** Bilder werden
  auf 2048 px runterskaliert und als WebP q90 encodiert, Qualität sinkt
  in 5er-Schritten nur oberhalb 1 MB (Floor q65). Alpha bleibt erhalten,
  GIFs pass through (Animation), HEIC wird transcodiert. Original
  verwerfen, nur komprimierte Fassung speichern (siehe **OFFEN-4**).
- **FR-6** Hochgeladene Bilder sind **sofort öffentlich sichtbar** (kein
  Freigabe-Schritt, kein Pending-Zustand).

### Galerie / Ansicht (FR-7 … FR-10)

- **FR-7** Galerie-Ansicht als Raster mit WebP-Thumbnails (lazy geladen,
  Ansatz aus throway `?thumb=1` übernehmen), Klick öffnet Großansicht
  (Lightbox), mobil bedienbar.
- **FR-8** Default-Sortierung: neueste zuerst. **Manuelle Reihenfolge**
  (Admin, siehe FR-12) überschreibt die Default-Sortierung.
- **FR-9** Einzeldirect-URL pro Bild (`/i/<id>`) — inline im Browser,
  teilbar.
- **FR-10** Verborgene Bilder erscheinen nicht in der Galerie. Verhalten des
  Direct-URLs bei verborgenen Bildern: siehe **OFFEN-2**.

### Admin / Moderation (FR-11 … FR-14)

- **FR-11** Admin-Zugang über **geheime URL** (Secret als Pfad-Segment,
  **niemals als Query-Parameter** — Query-Strings landen in nginx-/Access-Logs).
  Details Einmal vs. langlebig: **OFFEN-1**.
- **FR-12** Admin kann **Reihenfolge ändern** (Drag & Drop oder
  Hoch/Runter-Verschieben; persistente `order`-Kennzahl pro Bild).
- **FR-13** Admin kann **verbergen** (`hidden=1`): Bild verschwindet aus der
  Galerie, Bytes und Metadaten bleiben erhalten (für Reklamation/Nachweis).
  Wiedereinblenden muss möglich sein.
- **FR-14** Admin kann **endgültig löschen** (Bytes + Metadaten + Thumbnails
  unwiderruflich entfernt) — für Verstöße, bei denen das Material auch
  physisch weg muss.

### Lifetime & Pool (FR-15 … FR-18)

- **FR-15** **Feste Lebensdauer: 90 Tage** ab Upload. Danach automatische
  Löschung (Original-Entscheidung „Variante B": TTL ist der
  Haupt-Aufräummechanismus).
- **FR-16** **Pool-Größe: 20 GB** (Env-konfigurierbar, Default 20 GB).
- **FR-17** Verhalten bei vollem Pool: **Uploads werden abgelehnt** mit
  klarer Fehlermeldung; niemals stilles Entfernen bereits hochgeladener
  Bilder (keine LRU-Eviction wie bei throway — dort ist das okay, hier wäre
  es Datenverlust). Admin wird im Admin-UI über Pool-Auslastung informiert.
- **FR-18** Metadaten pro Bild: Upload-Zeit, Uploader-IP (für
  Missbrauchsverfolgung bei Verstößen), Größe, Original-Dateiname,
  `hidden`-Flag, `order`, Lösch-/Verbergegrund (optional, Admin-Freitext).

---

## 4. Nicht-Ziele

- Keine Benutzeraccounts, keine Registrierung, keine Logins für Besucher.
- Keine Kommentare, Likes, Alben-Verwaltung, EXIF-Anzeige.
- Keine Bearbeitung/Cropping hochgeladener Bilder.
- Keine unbegrenzte Speicherung — 90 Tage sind hart.
- throway selbst wird **nicht** geändert (kein Merge, keine gemeinsame DB).

---

## 5. Technische Anforderungen

- **TR-1** **Eigener Namespace innerhalb von throway** (OFFEN-5/7): Storage
  unter `ROOT/pics/`, vollständig getrennt vom throway-Pool. `total_size()`,
  `_units()` und `evict()` (LRU über den 100-MB-Werfen-Pool) **dürfen
  pics-Einheiten nie sehen** — sonst löscht Pool-Druck die ältesten
  Galerie-Bilder und bricht das 90-Tage-Versprechen. Pics hat eigenen Pool
  (20 GB) und eigenes Verhalten bei Voll (Reject statt Evict, FR-17).
  Lifetime: fest 90 Tage ab Upload, abgewickelt vom bestehenden `sweep()`.
- **TR-2** Erweiterung der bestehenden `store.py` (Python-Stdlib, kein
  Framework); Abhängigkeiten **Pillow** + **`pillow-heif`** (HEIC-Decode,
  OFFEN-3). Fehlt `pillow-heif`, lehnen HEIC-Uploads mit klarer Meldung ab
  (kein Crash, JPEG/PNG/WebP/GIF funktionieren weiter).
- **TR-3** Storage: flaches Verzeichnis + `.meta`-JSON-Dateien (wie throway),
  kein DB-Server.
- **TR-4** Rate-Limit so bemessen, dass ein 1000-Bilder-Batch ohne
  Admin-Eingriff durchläuft (≥ 100 Upload-Requests/min; bei 10 GB Rohdaten
  ist ohnehin die Bandbreite der Flaschenhals — Queue darf nicht am
  Rate-Limit ersticken).
- **TR-5** Deployment: unverändert der throway-Weg (Bumppush, `store.py`
  nach lubu, `systemctl restart throway-store`); neu: `pillow-heif` auf lubu
  installieren. Route über amd2 → `skale.dev/throway/pics`.
- **TR-6** Self-Service für Agenten wie bei throway: `GET /api` liefert den
  maschinenlesbaren Vertrag; Upload/Listing auch ohne Browser nutzbar.
- **TR-7** **Tests** (pytest): Admin-Auth (Secret-Konstantzeitvergleich,
  falsches Secret = 404/403), hidden-Bilder nicht in Galerie aber Metadaten
  intakt, 90-Tage-Expiry, Pool-Voll-Verhalten (Reject statt Evict),
  Komprimierung (Dimensionen ≤ 2048 px, Zieldatei kleiner als Original),
  Reihenfolge-Persistenz. throway hat null Tests — dieser Dienst startet
  mit Tests, weil Auth/Moderation stillschweigend falsch sein können.

---

## 6. Sizing-Rechnung (Nachweis „1000 Bilder")

| Szenario | pro Bild | 1000 Bilder | Passt in 20 GB? |
|---|---|---|---|
| Original Event-Kamera-JPEG (unkomprimiert gespeichert) | 10–25 MB | 10–25 GB | knapp / **nein** |
| **Nach FR-5 komprimiert (2048 px, q80)** | **0.3–1.5 MB** | **0.3–1.5 GB** | **ja, mit großem Puffer** |

- **Fazit:** Mit Komprimierung (FR-5) sind 1000 Bilder ~1.5 GB — 20 GB
  bieten Platz für **mehrere Events gleichzeitig** (bis ~10 Events à 1000
  Bilder innerhalb des 90-Tage-Fensters).
- Upload-Bandbreite für einen 1000er-Batch (Rohdaten ~10 GB): bei 40 Mbit/s
  Upstream ca. 35 Minuten → FR-2 (Queue/Retry) ist Pflicht.
- Speicher: 20 GB = ~5 % der freien 389 GB auf `/srv/storage2`. ✔

---

## 7. Offene Entscheidungen

| # | Frage | Empfehlung |
|---|---|---|
| # | Frage | Entscheidung (2026-09-27) |
|---|---|---|
| **OFFEN-1** | Admin-URL einmalig oder langlebig? | **Langlebig.** Secret als Pfad-Segment aus Env-Variable; Rotation = Env neu setzen + Restart. Kein Logout-Zustand (URL-Secret, keine Cookies). |
| **OFFEN-2** | Direct-URL eines verborgenen Bildes? | **User sieht 404, Admin sieht es.** Bytes + Metadaten bleiben erhalten; Zustellung nur mit valide­m Admin-Secret. |
| **OFFEN-3** | HEIC-Unterstützung? | **Ja, effizient.** Decode per `pillow-heif`, direkt auf 2048 px skalieren, einmalig als WebP encoden — keine Zwischenformate, keine Original-Ablage. |
| **OFFEN-4** | Original nach Komprimierung behalten? | **Verwerfen.** Gespeichert wird nur die komprimierte Fassung. |
| **OFFEN-5** | Subdomain oder Pfad? | **Pfad: `skale.dev/throway/pics`** — Teil des throway-Featuresets, kein eigener Host. |
| **OFFEN-6** | Name? | **`pics`.** |
| **OFFEN-7** | Eigener Service oder Teil von throway? | **Teil von throway.** Ein Service, ein Deployment — mit eigenem Namespace + eigenem 20-GB-Pool, damit throways LRU-`evict()` Galerie-Bilder nie anfasst (TR-1). |

---

## 8. Abnahmekriterien

- **AK-1** 1000 echte Kamera-JPEGs (à ~10 MB) in einem Batch über die Web-UI
  hochgeladen: alle 1000 landen in der Galerie, sichtbar, komprimiert; Batch
  überlebt Netzfehler einzelner Dateien (Retry).
- **AK-2** Galerie lädt mit 1000 Bildern in < 3 s (Thumbnails, lazy),
  mobil bedienbar (44px-Touch-Ziele).
- **AK-3** Bild ohne Admin-Aktion verschwindet nach 90 Tagen; Metadaten
  laufen mit ab.
- **AK-4** Pool-Voll (simuliert): weiterer Upload wird **abgelehnt** mit
  klarer Meldung; bestehende Bilder bleiben unangetastet.
- **AK-5** Admin: verbergen → Bild aus Galerie + Direct-URL 404, Bytes
  vorhanden (Nachweis im Admin sichtbar); einblenden kehrt beides um.
- **AK-6** Admin: löschen → Datei, Meta, Thumb physisch weg (Verzeichnis
  leer), keine Recovery.
- **AK-7** Admin: Reihenfolge per Drag & Drop ändern, gilt für alle Besucher.
- **AK-8** Falsches/fehlendes Admin-Secret: kein Unterschied zwischen
  „falsch" und „existiert nicht" (404, keine Timing-/Fehlerlecks).
- **AK-9** Alle TR-7-Tests grün im CI/Lauf (`pytest`).
- **AK-10** `GET /api` beschreibt Upload, Listing, Admin-Endpunkte
  vollständig (Self-Service, TR-6).

---

## 9. Deployment-Bild (Ziel)

```
skale.dev/throway/pics ──► amd2 (TLS, nginx) ──► lubu: throway-store.service
                                                    └─ ROOT/pics/  (eigener 20-GB-Pool, 90-Tage-TTL)
skale.dev/throway/…    ──► amd2 ──► lubu: gleicher Service
                                                    └─ ROOT (100 MB Werfen-Pool, 4 h — unverändert)
```

Ein Service, zwei Budgets: throway-Einheiten bleiben im LRU-evictierenden
100-MB-Pool; pics-Einheiten leben im eigenen 20-GB-Pool mit festem 90-Tage-TTL
und Reject-statt-Evict bei Voll.

---

*Dieses Dokument ist die single source of truth für das pics-Feature.
Änderungen nur mit Versionsstand (analog throway-Release-Disziplin).*
