"""Gemeinsame Bausteine: Papierformate, Foto-Vorbereitung, Text-Maske, PDF-Export, Druck-Simulation."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas

# ---------------------------------------------------------------------------
# Parameter
# ---------------------------------------------------------------------------
PAPER_MM = {"A4": (210.0, 297.0), "A5": (148.0, 210.0), "10x15cm": (100.0, 150.0)}
MARGIN_MM = {"A4": 12.0, "A5": 10.0, "10x15cm": 6.0}   # Rand um das Bild (Platz für Passmarken/Hinweis)

CLAHE_CLIP = 2.0          # CLAHE-Kontrastbegrenzung (höher = mehr lokaler Kontrast)
CLAHE_TILES = 8           # CLAHE-Kachelraster
FACE_Y_POS = 0.40         # Gesicht wird auf diese relative Höhe im Zuschnitt gesetzt

MASK_FILL_W = 0.90        # Text-Maske: max. Anteil der Bildbreite
MASK_FILL_H = 0.80        # Text-Maske: max. Anteil der Bildhöhe
MASK_LINE_SPACING = 0.05  # zusätzlicher Zeilenabstand (Anteil der Schriftgröße)

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
            self._cache[key] = render_text_mask(text, *self.size_px(dpi), self.font, **(self.text_layout or {}))
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
                     drehung: float = 0) -> np.ndarray:
    """Text so groß wie möglich in einen Kasten setzen (Zeilenumbruch automatisch).

    x, y: Mitte des Texts in % der Bildbreite/-höhe (0 = links/oben, 100 = rechts/unten).
    breite, hoehe: maximale Größe des Textblocks in % der Bildbreite/-höhe.
    drehung: Grad gegen den Uhrzeigersinn. Ragt der Text über den Rand, wird er nach innen geschoben.
    """
    words = text.split() or [" "]
    if len(words) > 10:  # zu viele Kombinationen – Wörter grob vorgruppieren
        words = [" ".join(words[i:i + 2]) for i in range(0, len(words), 2)]
    box_w, box_h = int(w * breite / 100), int(h * hoehe / 100)
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


def new_pdf(ctx: Context, path: Path, label: str, marks=True):
    """Leere PDF-Seite in Papiergröße mit Hinweis, Kontrollmaß und Passmarken. Rückgabe: Canvas."""
    pw, ph = ctx.paper_mm
    c = rl_canvas.Canvas(str(path), pagesize=(pw * mm, ph * mm))
    c.setTitle(label)
    # Druck-Hinweis + Kontrollmaß 50 mm
    m = MARGIN_MM[ctx.paper]
    c.setFont("Helvetica", 5.5)
    c.setFillGray(0.35)
    c.drawString(m * mm, (m * 0.35) * mm,
                 f"{label}  |  Drucken mit 100 % / 'Tatsächliche Größe' (keine Anpassung)  |  Kontrollmaß: 50 mm →")
    x0 = (pw - m - 50) * mm
    y0 = (m * 0.42) * mm
    c.setStrokeGray(0.35)
    c.setLineWidth(0.3)
    c.line(x0, y0, x0 + 50 * mm, y0)
    for x in (x0, x0 + 50 * mm):
        c.line(x, y0 - 1 * mm, x, y0 + 1 * mm)
    if marks:
        draw_registration_marks(ctx, c)
    c.setFillGray(0)
    c.setStrokeGray(0)
    return c


def draw_registration_marks(ctx: Context, c):
    """Passmarken (Kreuz + Kreis) an den vier Ecken des Bildbereichs, außerhalb des Bilds."""
    ox, oy = ctx.image_origin_mm
    iw, ih = ctx.image_mm
    m = MARGIN_MM[ctx.paper]
    r = min(3.0, m * 0.35)
    off = m * 0.5
    c.setStrokeGray(0)
    c.setLineWidth(0.25)
    for cx, cy in [(ox - off, oy - off), (ox + iw + off, oy - off),
                   (ox - off, oy + ih + off), (ox + iw + off, oy + ih + off)]:
        c.circle(cx * mm, cy * mm, r * 0.6 * mm, stroke=1, fill=0)
        c.line((cx - r) * mm, cy * mm, (cx + r) * mm, cy * mm)
        c.line(cx * mm, (cy - r) * mm, cx * mm, (cy + r) * mm)
    # Rahmen-Ecken
    c.setLineWidth(0.2)
    L = min(5.0, iw * 0.05)
    for x, y, sx, sy in [(ox, oy, 1, 1), (ox + iw, oy, -1, 1), (ox, oy + ih, 1, -1), (ox + iw, oy + ih, -1, -1)]:
        c.line(x * mm, y * mm, (x + sx * L) * mm, y * mm)
        c.line(x * mm, y * mm, x * mm, (y + sy * L) * mm)


def save_pdf_raster(ctx: Context, path: Path, img: np.ndarray | Image.Image, label: str, marks=True):
    """Rasterbild exakt in Bildbereichsgröße (100 %) auf die Seite setzen."""
    if isinstance(img, np.ndarray):
        img = Image.fromarray(img if img.dtype == np.uint8 else to_u8(img))
    c = new_pdf(ctx, path, label, marks)
    ox, oy = ctx.image_origin_mm
    iw, ih = ctx.image_mm
    c.drawImage(ImageReader(img.convert("L")), ox * mm, oy * mm, iw * mm, ih * mm)
    c.showPage()
    c.save()


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
