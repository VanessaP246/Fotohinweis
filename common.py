"""Gemeinsame Bausteine: Papierformate, Foto-Vorbereitung, Text-Maske, PDF-Export, Druck-Simulation."""
from __future__ import annotations

import os
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfdoc import PDFImageXObject
from reportlab.pdfgen import canvas as rl_canvas

# ---------------------------------------------------------------------------
# Parameter
# ---------------------------------------------------------------------------
PAPER_MM = {"A4": (210.0, 297.0), "A5": (148.0, 210.0), "10x15cm": (100.0, 150.0)}
MARGIN_MM = {"A4": 12.0, "A5": 10.0, "10x15cm": 6.0}   # Rand um das Bild (nicht bedruckbarer Bereich des Druckers)

CLAHE_CLIP = 2.0          # CLAHE-Kontrastbegrenzung (höher = mehr lokaler Kontrast)
CLAHE_TILES = 8           # CLAHE-Kachelraster
FACE_Y_POS = 0.40         # Gesicht wird auf diese relative Höhe im Zuschnitt gesetzt

MASK_FILL_W = 0.90        # Text-Maske: max. Anteil der Bildbreite
MASK_FILL_H = 0.80        # Text-Maske: max. Anteil der Bildhöhe
MASK_LINE_SPACING = 0.05  # zusätzlicher Zeilenabstand (Anteil der Schriftgröße)

CAPTION_PIXEL_MM = 0.5        # Beschriftung unter dem Foto (Pixelschrift 5×7): Größe eines Pixels
CAPTION_GAP_MM = 3.0          # Abstand zwischen Foto-Unterkante und Beschriftung

PRINTSIM_DPI = 150        # simulierte effektive Druckauflösung
PRINTSIM_BLUR_PX = 0.7    # Gauß-Sigma (bei PRINTSIM_DPI)
PRINTSIM_NOISE = 0.025    # Rausch-Std.-Abw. (0..1)
PRINTSIM_GAMMA = 1.2      # >1 = dunkler (Tonwertzuwachs beim Druck)
PRINTSIM_JPEG_Q = 65      # JPEG-Qualität
PRINTSIM_OUT_DPI = 300    # Auflösung des gespeicherten Printsim-Bilds

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Black.ttf",
    "C:/Windows/Fonts/ariblk.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def default_font() -> str:
    here = Path(__file__).parent / "fonts"
    for p in sorted(here.glob("*.ttf")) + [Path(p) for p in FONT_CANDIDATES]:
        if p.exists():
            return str(p)
    raise FileNotFoundError("Keine fette TTF-Schrift gefunden – bitte --font angeben oder in fonts/ ablegen.")


def mm_to_px(v_mm: float, dpi: int) -> int:
    return int(round(v_mm / 25.4 * dpi))


# ---------------------------------------------------------------------------
# Kontext pro Foto
# ---------------------------------------------------------------------------
@dataclass
class Context:
    src: Path
    name: str
    text: str
    out_root: Path
    dpi: int
    paper: str
    font: str
    highlight: bool = False
    highlight_text: str | None = None
    text_layout: dict | None = None  # Position/Größe der Text-Maske, siehe render_text_mask()
    photo_aspect: float | None = None  # gesetzt = Bildbereich an Foto-Seitenverhältnis (B/H) anpassen statt zuschneiden
    caption: str = ""  # links unter dem Foto, z. B. "CAMERA 1" (leer = nichts)
    taken: datetime | None = None  # Aufnahmezeit, rechts unter dem Foto (Datum + Uhrzeit)
    _cache: dict = field(default_factory=dict)

    @property
    def paper_mm(self):
        return PAPER_MM[self.paper]

    @property
    def _max_image_mm(self):
        pw, ph = self.paper_mm
        m = MARGIN_MM[self.paper]
        return pw - 2 * m, ph - 2 * m

    @property
    def image_mm(self):
        """Größe des Bildbereichs auf dem Papier (mm)."""
        w, h = self._max_image_mm
        if self.photo_aspect:
            if w / h > self.photo_aspect:
                w = h * self.photo_aspect
            else:
                h = w / self.photo_aspect
        return w, h

    @property
    def text_ref_mm(self):
        """Bezugsfläche für die Hinweisgröße: Bildbereich des Fotos im Hochformat.
        Ein Querformat-Foto bekommt dadurch denselben Hinweis wie dasselbe Foto hochkant."""
        w, h = self._max_image_mm
        if self.photo_aspect:
            a = min(self.photo_aspect, 1 / self.photo_aspect)  # Hochformat-Seitenverhältnis
            if w / h > a:
                w = h * a
            else:
                h = w / a
        return min(w, h), max(w, h)

    @property
    def image_origin_mm(self):
        """Linke untere Ecke des Bildbereichs (PDF-Koordinaten, mm). Kleinerer Bildbereich wird zentriert."""
        (mw, mh), (w, h) = self._max_image_mm, self.image_mm
        m = MARGIN_MM[self.paper]
        return m + (mw - w) / 2, m + (mh - h) / 2

    def size_px(self, dpi: int):
        w, h = self.image_mm
        return mm_to_px(w, dpi), mm_to_px(h, dpi)

    def out_dir(self, variant: str) -> Path:
        d = self.out_root / variant
        d.mkdir(parents=True, exist_ok=True)
        return d

    def photo(self, dpi: int) -> np.ndarray:
        """Zugeschnittenes, CLAHE-verstärktes Graustufenfoto als float32 0..1 (1 = weiß)."""
        key = ("photo", dpi)
        if key not in self._cache:
            self._cache[key] = prepare_photo(self.src, *self.size_px(dpi))
        return self._cache[key]

    def mask(self, dpi: int, text: str | None = None) -> np.ndarray:
        """Text-Maske als float32 0..1 (1 = Text)."""
        text = text or self.text
        key = ("mask", dpi, text)
        if key not in self._cache:
            rw, rh = self.text_ref_mm
            self._cache[key] = render_text_mask(text, *self.size_px(dpi), self.font,
                                                ref=(mm_to_px(rw, dpi), mm_to_px(rh, dpi)),
                                                **(self.text_layout or {}))
        return self._cache[key]


# ---------------------------------------------------------------------------
# Foto vorbereiten
# ---------------------------------------------------------------------------
def _detect_face(gray: np.ndarray):
    path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    det = cv2.CascadeClassifier(path)
    if det.empty():
        return None
    scale = 800 / max(gray.shape)
    small = cv2.resize(gray, None, fx=scale, fy=scale) if scale < 1 else gray
    s = min(scale, 1.0)
    faces = det.detectMultiScale(small, scaleFactor=1.1, minNeighbors=6, minSize=(40, 40))
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    return (x + w / 2) / s, (y + h / 2) / s


def prepare_photo(path: Path, w: int, h: int) -> np.ndarray:
    img = ImageOps.exif_transpose(Image.open(path))  # Kamera-Ausrichtung (EXIF) berücksichtigen
    img = img.convert("RGB")
    gray = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
    H, W = gray.shape
    target = w / h
    if W / H > target:
        cw, ch = int(round(H * target)), H
    else:
        cw, ch = W, int(round(W / target))
    face = _detect_face(gray)
    if face:
        cx, cy = face[0], face[1] - (FACE_Y_POS - 0.5) * ch
    else:
        cx, cy = W / 2, H / 2
    x0 = int(np.clip(cx - cw / 2, 0, W - cw))
    y0 = int(np.clip(cy - ch / 2, 0, H - ch))
    crop = gray[y0:y0 + ch, x0:x0 + cw]
    interp = cv2.INTER_AREA if cw > w else cv2.INTER_CUBIC
    crop = cv2.resize(crop, (w, h), interpolation=interp)
    clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP, tileGridSize=(CLAHE_TILES, CLAHE_TILES))
    return clahe.apply(crop).astype(np.float32) / 255.0


# ---------------------------------------------------------------------------
# Text-Maske
# ---------------------------------------------------------------------------
def _line_splits(words):
    """Alle Aufteilungen der Wortliste in zusammenhängende Zeilen."""
    n = len(words)
    for mask in range(1 << max(0, n - 1)):
        lines, cur = [], [words[0]]
        for i in range(1, n):
            if mask & (1 << (i - 1)):
                lines.append(" ".join(cur))
                cur = [words[i]]
            else:
                cur.append(words[i])
        lines.append(" ".join(cur))
        yield "\n".join(lines)


def _fit(draw, txt, font_path, max_w, max_h):
    lo, hi = 4, int(max_h) + 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        f = ImageFont.truetype(font_path, mid)
        l, t, r, b = draw.multiline_textbbox((0, 0), txt, font=f, align="center",
                                             spacing=int(mid * MASK_LINE_SPACING))
        if r - l <= max_w and b - t <= max_h:
            lo = mid
        else:
            hi = mid - 1
    return lo


def render_text_mask(text: str, w: int, h: int, font_path: str, x: float = 50, y: float = 50,
                     breite: float = MASK_FILL_W * 100, hoehe: float = MASK_FILL_H * 100,
                     drehung: float = 0, ref: tuple[int, int] | None = None) -> np.ndarray:
    """Text so groß wie möglich in einen Kasten setzen (Zeilenumbruch automatisch).

    x, y: Mitte des Texts in % der Bildbreite/-höhe (0 = links/oben, 100 = rechts/unten).
    breite, hoehe: maximale Größe des Textblocks in % der Bildbreite/-höhe.
    drehung: Grad gegen den Uhrzeigersinn. Ragt der Text über den Rand, wird er nach innen geschoben.
    ref: (Breite, Höhe) in px, auf die sich breite/hoehe beziehen (Standard: das Bild selbst). So bleibt die
         Textgröße gleich, egal ob das Foto hoch oder quer ist. Passt der Text nicht ins Bild, wird er verkleinert.
    """
    words = text.split() or [" "]
    if len(words) > 10:  # zu viele Kombinationen – Wörter grob vorgruppieren
        words = [" ".join(words[i:i + 2]) for i in range(0, len(words), 2)]
    rw, rh = ref or (w, h)
    box_w, box_h = min(int(rw * breite / 100), w), min(int(rh * hoehe / 100), h)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    size, txt = max((_fit(probe, t, font_path, box_w, box_h), t) for t in _line_splits(words))
    f = ImageFont.truetype(font_path, size)
    spacing = int(size * MASK_LINE_SPACING)
    l, t, r, b = (int(round(v)) for v in probe.multiline_textbbox((0, 0), txt, font=f, align="center",
                                                                   spacing=spacing))
    block = Image.new("L", (r - l + 4, b - t + 4), 0)
    ImageDraw.Draw(block).multiline_text((2 - l, 2 - t), txt, font=f, fill=255, align="center", spacing=spacing)
    if drehung:
        block = block.rotate(drehung, resample=Image.Resampling.BICUBIC, expand=True)
    bw, bh = block.size
    cx = int(np.clip(w * x / 100, bw / 2, max(bw / 2, w - bw / 2)))
    cy = int(np.clip(h * y / 100, bh / 2, max(bh / 2, h - bh / 2)))
    img = Image.new("L", (w, h), 0)
    img.paste(block, (cx - bw // 2, cy - bh // 2))
    return np.asarray(img, dtype=np.float32) / 255.0


# ---------------------------------------------------------------------------
# Ausgabe
# ---------------------------------------------------------------------------
def to_u8(a: np.ndarray) -> np.ndarray:
    return (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)


def save_png(path: Path, img: np.ndarray | Image.Image, dpi: int | None = None, bilevel=False):
    if isinstance(img, np.ndarray):
        img = Image.fromarray(img if img.dtype == np.uint8 else to_u8(img))
    if bilevel:
        img = img.convert("1", dither=Image.Dither.NONE)
    img.save(path, dpi=(dpi, dpi) if dpi else None)


def new_pdf(ctx: Context, path: Path, label: str):
    """Leere PDF-Seite in Papiergröße (ohne Markierungen oder Text um das Foto). Rückgabe: Canvas."""
    pw, ph = ctx.paper_mm
    c = rl_canvas.Canvas(str(path), pagesize=(pw * mm, ph * mm))
    c.setTitle(label)
    return c


def save_pdf_raster(ctx: Context, path: Path, img: np.ndarray | Image.Image, label: str):
    """Rasterbild exakt in Bildbereichsgröße (100 %) auf die Seite setzen."""
    if isinstance(img, np.ndarray):
        img = Image.fromarray(img if img.dtype == np.uint8 else to_u8(img))
    c = new_pdf(ctx, path, label)
    ox, oy = ctx.image_origin_mm
    iw, ih = ctx.image_mm
    if img.mode == "1":
        # Echtes 1-Bit-Bild einbetten (ReportLab würde es zu 8-Bit aufblasen: ~25 MB pro Seite bei 600 dpi,
        # das überfordert den Drucker → "PDF beschädigt"). Nur Flate, kein ASCII85.
        c.drawImage(ImageReader(Image.new("L", (1, 1))), ox * mm, oy * mm, iw * mm, ih * mm)
        xobj = next(o for o in c._doc.idToObject.values() if isinstance(o, PDFImageXObject))
        xobj.width, xobj.height = img.size
        xobj.colorSpace = "DeviceGray"
        xobj.bitsPerComponent = 1
        xobj._filters = ("FlateDecode",)
        xobj.streamContent = zlib.compress(img.tobytes(), 9)  # PIL "1": 1 = weiß, Zeilen auf Bytes gepolstert = PDF
    else:
        c.drawImage(ImageReader(img.convert("L")), ox * mm, oy * mm, iw * mm, ih * mm)
    draw_caption(ctx, c)
    c.showPage()
    c.save()


# Pixelschrift 5×7 wie bei Kamera-Einblendungen (1 = Pixel gesetzt). Kleinbuchstaben → Großbuchstaben,
# unbekannte Zeichen → Leerzeichen.
PIXEL_FONT = {
    " ": "00000 00000 00000 00000 00000 00000 00000",
    "0": "01110 10001 10011 10101 11001 10001 01110",
    "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111",
    "3": "11111 00010 00100 00010 00001 10001 01110",
    "4": "00010 00110 01010 10010 11111 00010 00010",
    "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110",
    "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110",
    "9": "01110 10001 10001 01111 00001 00010 01100",
    "A": "01110 10001 10001 10001 11111 10001 10001",
    "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110",
    "D": "11100 10010 10001 10001 10001 10010 11100",
    "E": "11111 10000 10000 11110 10000 10000 11111",
    "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111",
    "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110",
    "J": "00111 00010 00010 00010 00010 10010 01100",
    "K": "10001 10010 10100 11000 10100 10010 10001",
    "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001",
    "N": "10001 10001 11001 10101 10011 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110",
    "P": "11110 10001 10001 11110 10000 10000 10000",
    "Q": "01110 10001 10001 10001 10101 10010 01101",
    "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110",
    "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110",
    "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 10101 01010",
    "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 10001 01010 00100 00100 00100",
    "Z": "11111 00001 00010 00100 01000 10000 11111",
    ".": "00000 00000 00000 00000 00000 01100 01100",
    ":": "00000 01100 01100 00000 01100 01100 00000",
    "-": "00000 00000 00000 11111 00000 00000 00000",
    "/": "00000 00001 00010 00100 01000 10000 00000",
}
PIXEL_ADVANCE = 6      # Zeichenbreite inkl. Abstand (Pixel)
PIXEL_LINE = 10        # Zeilenhöhe inkl. Abstand (Pixel)


def pixel_text_width(text: str, px: float) -> float:
    return (len(text) * PIXEL_ADVANCE - 1) * px if text else 0.0


def draw_pixel_text(c, text: str, x: float, top: float, px: float):
    """Text in Pixelschrift zeichnen. x = linke Kante, top = Oberkante (PDF-Punkte), px = Pixelgröße (Punkte)."""
    path = c.beginPath()
    for i, ch in enumerate(text.upper()):
        rows = PIXEL_FONT.get(ch, PIXEL_FONT[" "]).split()
        x0 = x + i * PIXEL_ADVANCE * px
        for r, row in enumerate(rows):
            y = top - (r + 1) * px
            col = 0
            while col < 5:  # zusammenhängende Pixel einer Zeile als ein Rechteck
                if row[col] == "1":
                    end = col
                    while end < 5 and row[end] == "1":
                        end += 1
                    path.rect(x0 + col * px, y, (end - col) * px, px)
                    col = end
                else:
                    col += 1
    c.drawPath(path, stroke=0, fill=1)


def draw_caption(ctx: Context, c):
    """Überwachungskamera-Beschriftung unter dem Foto: links der Kameraname, rechts Datum und Uhrzeit."""
    ox, oy = ctx.image_origin_mm
    iw, _ = ctx.image_mm
    px = CAPTION_PIXEL_MM * mm
    top = (oy - CAPTION_GAP_MM) * mm
    right = (ox + iw) * mm
    c.setFillGray(0)
    if ctx.caption:
        draw_pixel_text(c, ctx.caption, ox * mm, top, px)
    if ctx.taken:
        for i, line in enumerate((ctx.taken.strftime("%d/%m/%Y"), ctx.taken.strftime("%H:%M:%S"))):
            draw_pixel_text(c, line, right - pixel_text_width(line, px), top - i * PIXEL_LINE * px, px)


def capture_time(path: Path) -> datetime:
    """Aufnahmezeit: EXIF-Aufnahmedatum, sonst Zeitpunkt, an dem die Datei gespeichert wurde."""
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            raw = exif.get_ifd(0x8769).get(0x9003) or exif.get(0x0132)  # DateTimeOriginal / DateTime
        if raw:
            return datetime.strptime(str(raw).strip("\x00 "), "%Y:%m:%d %H:%M:%S")
    except (OSError, ValueError):
        pass
    return datetime.fromtimestamp(path.stat().st_mtime)


# ---------------------------------------------------------------------------
# Druck-Simulation
# ---------------------------------------------------------------------------
def printsim(img: np.ndarray, dpi: int, seed: int = 0) -> tuple[np.ndarray, int]:
    """Simuliert Druck + Scan/Foto. Eingabe uint8 oder float, Rückgabe uint8 bei PRINTSIM_OUT_DPI."""
    a = img.astype(np.float32) / 255.0 if img.dtype == np.uint8 else img.astype(np.float32)
    h, w = a.shape
    s = PRINTSIM_DPI / dpi
    low = cv2.resize(a, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    low = cv2.GaussianBlur(low, (0, 0), PRINTSIM_BLUR_PX)
    rng = np.random.default_rng(seed)
    low = low + rng.normal(0, PRINTSIM_NOISE, low.shape).astype(np.float32)
    low = np.clip(low, 0, 1) ** PRINTSIM_GAMMA
    ok, enc = cv2.imencode(".jpg", to_u8(low), [cv2.IMWRITE_JPEG_QUALITY, PRINTSIM_JPEG_Q])
    low = cv2.imdecode(enc, cv2.IMREAD_GRAYSCALE)
    out_dpi = min(dpi, PRINTSIM_OUT_DPI)
    so = out_dpi / dpi
    out = cv2.resize(low, (int(w * so), int(h * so)), interpolation=cv2.INTER_CUBIC)
    return out, out_dpi


def thumb(img: np.ndarray | Image.Image, height: int) -> Image.Image:
    if isinstance(img, np.ndarray):
        img = Image.fromarray(img if img.dtype == np.uint8 else to_u8(img))
    img = img.convert("L")
    w = max(1, int(img.width * height / img.height))
    return img.resize((w, height), Image.Resampling.LANCZOS)


def label_font(size=22):
    for p in ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc"]:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def side_by_side(images: list[tuple[str, Image.Image]], height: int, pad=16) -> Image.Image:
    """Mehrere Bilder beschriftet nebeneinander (gleiche Höhe)."""
    ths = [(t, thumb(im, height)) for t, im in images]
    font = label_font(max(14, height // 22))
    lab_h = int(font.size * 1.8)
    W = sum(im.width for _, im in ths) + pad * (len(ths) + 1)
    out = Image.new("L", (W, height + lab_h + pad * 2), 255)
    d = ImageDraw.Draw(out)
    x = pad
    for t, im in ths:
        out.paste(im, (x, pad + lab_h))
        d.text((x, pad), t, fill=0, font=font)
        x += im.width + pad
    return out
