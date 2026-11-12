# Foto-Rätsel – versteckter Hinweis im Halftone-Foto

Ein Kamerafoto wird in ein 1-Bit-Punktraster (Halftone) umgewandelt. Innerhalb des Hinweistexts ist
das Raster gedreht, bei exakt gleicher Helligkeit. Der Text steckt also nur in der Textur. Aus normaler
Entfernung sieht man ein ganz normales Rasterfoto. Wer genau (oder mit Lupe) hinsieht, entdeckt die
Buchstaben.

Pro Kamerabild entstehen zwei Varianten:

| Variante | Raster im Text | Wirkung |
|---|---|---|
| `standard` | um 25° gedreht | subtil, schwerer zu finden |
| `winkel45` | um 45° gedreht (waagerecht/senkrecht) | deutlicher erkennbar |

## Ablauf

```
Kamera  →  input/<bild>.jpg  →  raetsel.py  →  output/<bild>_standard.pdf
                                                output/<bild>_winkel45.pdf
                                                output/<bild>_kontrolle.png
```

1. **Hinweis festlegen** in `einstellungen.toml` (Text, Position, Größe, Drehung, Papier).
2. **Foto ablegen** in `input/` (JPG, PNG, TIFF …). Die Kamera-Ausrichtung (EXIF) wird berücksichtigt.
3. **Starten:**
   ```bash
   .venv/bin/python raetsel.py               # verarbeitet alle neuen Bilder in input/
   .venv/bin/python raetsel.py --beobachten  # läuft dauerhaft, jedes neue Bild wird nach ~20 s fertig
   .venv/bin/python raetsel.py --alle        # alles neu erzeugen (z. B. nach Änderung der Einstellungen)
   ```
4. **Kontrollieren:** `output/<bild>_kontrolle.png` zeigt das Foto mit dem Text rot markiert, damit du
   die Position prüfen kannst. Dieses Bild **nicht** drucken, es verrät die Lösung.
5. **Drucken:** `output/<bild>_standard.pdf` bzw. `_winkel45.pdf` mit **100 % / „Tatsächliche Größe"**
   (nicht „An Seite anpassen").

Bereits verarbeitete Bilder werden übersprungen. Ein Bild gilt als verarbeitet, sobald seine PDFs existieren.
Im Beobachten-Modus gelten Änderungen an `einstellungen.toml` ab dem nächsten Bild.

## Kamera (ESP32-CAM) und Auslöser

Die ESP32-CAM hängt per USB am Mac, WLAN wird nicht benutzt. Ein Druck auf **C** gilt als Signal
„Knopf aktiviert“:

```
Taste C → kamera.py → Foto per USB (ca. 10 s) → input/foto.jpg (ersetzt das vorige Foto) → PDFs in output/ (ca. 20 s)
```

```bash
.venv/bin/python kamera.py          # Dauerbetrieb: C = Foto, P = drucken, Q = beenden (verarbeitet auch gleich)
.venv/bin/python kamera.py --test   # ein einzelnes Foto aufnehmen
```

- C wirkt nur, solange das Terminalfenster mit `kamera.py` vorne ist.
- `raetsel.py --beobachten` muss nicht zusätzlich laufen, `kamera.py` erledigt das mit.
- Die Kamera liefert Graustufen in 1600 × 1200. Hängt sie hochkant, dreht `[kamera] drehung` das Bild.
  Steht es auf dem Kopf, 90 und 270 vertauschen.
- Mit `[druck] zuschneiden = false` wird das ganze Foto verwendet: Der Bildbereich auf A4 ist dann
  186 × 248 mm statt 186 × 273 mm und liegt mittig auf dem Blatt.
- Die Übertragung läuft mit 115200 Baud. Höhere Raten kamen an diesem USB-Adapter verfälscht an.
  Jedes Foto wird per Prüfsumme kontrolliert und bei Fehlern neu angefordert.

**Firmware neu aufspielen** (nur nötig nach Änderungen an `esp32cam/esp32cam.ino`):

```bash
~/Library/Python/3.9/bin/uv pip install --python .venv/bin/python platformio
cd esp32cam && ../.venv/bin/pio run -t upload
```

**Alte Firmware wiederherstellen:** Vorher lief auf der Kamera eine Firebase-Firmware. Eine
vollständige Sicherung liegt in `esp32cam/sicherung/original_firmware_4MB.bin` (nicht im Git, weil
sie WLAN- und Firebase-Zugangsdaten enthalten kann):

```bash
.venv/bin/esptool --port /dev/cu.usbserial-120 write-flash 0 esp32cam/sicherung/original_firmware_4MB.bin
```

## Einstellungen (`einstellungen.toml`)

| Schlüssel | Bedeutung | Standard |
|---|---|---|
| `text` | der versteckte Hinweis (wird automatisch umbrochen) | `"Blockiert WCs"` |
| `x`, `y` | Mitte des Texts in % (x: 0 links → 100 rechts, y: 0 oben → 100 unten) | 50, 42 |
| `breite`, `hoehe` | maximale Größe des Textblocks in % der Bildbreite/-höhe | 90, 80 |
| `drehung` | Grad gegen den Uhrzeigersinn | 0 |
| `schrift` | Pfad zu einer fetten TTF-Schrift, leer = Arial Black | `""` |
| `papier` | `"A4"`, `"A5"` oder `"10x15cm"` | `"A4"` |
| `zuschneiden` | `false` = ganzes Foto, Bildbereich wird angepasst; `true` = auf Papierformat zuschneiden | `false` |
| `[kamera] port` | USB-Anschluss der Kamera, `"auto"` = erster USB-Seriell-Adapter | `"auto"` |
| `[kamera] drehung` | Drehung des Kamerabilds im Uhrzeigersinn (0/90/180/270) | 90 |
| `[kamera] blitz` | Blitz-LED beim Foto | `false` |
| `lpi` | Rasterweite. Kleiner = gröbere Punkte, Textur leichter erkennbar | 40 |
| `varianten` | welche Varianten erzeugt werden | `["standard", "winkel45"]` |

Ragt der Text über den Bildrand, wird er automatisch nach innen verschoben.

**Tipps:**
- Text zu schwer zu finden → `winkel45` drucken, `breite`/`hoehe` vergrößern, `lpi` auf 30–35 senken.
- Text zu leicht zu finden → `standard` drucken, `lpi` auf 50 erhöhen.
- Der Text ist am besten in **mittelhellen, ruhigen Flächen** zu erkennen. In ganz weißen oder ganz
  schwarzen Bereichen gibt es keine Punkte und damit auch keine Textur. Lege ihn per `x`/`y` am besten
  dorthin und prüfe das im Kontrollbild.

## Einrichtung (einmalig)

Benötigt Python ≥ 3.11. Auf diesem Rechner wurde es ohne Admin-Rechte über `uv` installiert:

```bash
python3 -m pip install --user uv
~/Library/Python/3.9/bin/uv venv --python 3.12 .venv
~/Library/Python/3.9/bin/uv pip install --python .venv/bin/python -r requirements.txt
```

## Dateien

| Datei | Inhalt |
|---|---|
| `einstellungen.toml` | alles, was man pro Rätsel ändert |
| `raetsel.py` | Programm (input/ → output/) |
| `kamera.py` | Auslöser: Taste C → Foto von der ESP32-CAM → input/ → output/ |
| `esp32cam/` | Firmware der ESP32-CAM (`esp32cam.ino`, PlatformIO-Projekt) |
| `variants/halftone.py` | Raster-Algorithmus. Feintuning-Parameter stehen oben, die beiden Varianten in `PRESETS` |
| `common.py` | Zuschnitt (Gesichtserkennung oder mittig), CLAHE-Kontrast, Text-Maske, PDF-Export |
| `archiv/` | der frühere Prototyp mit allen 5 Verfahren und Vergleichen (nicht mehr gepflegt) |
