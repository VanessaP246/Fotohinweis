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
   (nicht „An Seite anpassen"). Unten auf jeder Seite ist ein 50-mm-Kontrollmaß.

Bereits verarbeitete Bilder werden übersprungen. Ein Bild gilt als verarbeitet, sobald seine PDFs existieren.
Im Beobachten-Modus gelten Änderungen an `einstellungen.toml` ab dem nächsten Bild.

## Einstellungen (`einstellungen.toml`)

| Schlüssel | Bedeutung | Standard |
|---|---|---|
| `text` | der versteckte Hinweis (wird automatisch umbrochen) | `"Blockiert WCs"` |
| `x`, `y` | Mitte des Texts in % (x: 0 links → 100 rechts, y: 0 oben → 100 unten) | 50, 42 |
| `breite`, `hoehe` | maximale Größe des Textblocks in % der Bildbreite/-höhe | 90, 80 |
| `drehung` | Grad gegen den Uhrzeigersinn | 0 |
| `schrift` | Pfad zu einer fetten TTF-Schrift, leer = Arial Black | `""` |
| `papier` | `"A4"`, `"A5"` oder `"10x15cm"` | `"A4"` |
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
| `variants/halftone.py` | Raster-Algorithmus. Feintuning-Parameter stehen oben, die beiden Varianten in `PRESETS` |
| `common.py` | Zuschnitt (Gesichtserkennung oder mittig), CLAHE-Kontrast, Text-Maske, PDF-Export |
| `archiv/` | der frühere Prototyp mit allen 5 Verfahren und Vergleichen (nicht mehr gepflegt) |
