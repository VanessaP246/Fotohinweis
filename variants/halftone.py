"""Variante 2 – 1-Bit-Halftone-Porträt mit Textur-Text.

Außerhalb der Text-Maske: klassisches Punktraster (Winkel ANGLE_OUT).
Innerhalb der Text-Maske: andere Rasterstruktur bei gleicher mittlerer Helligkeit
(MODE_IN = "angle": gleiches Raster um ANGLE_DELTA verdreht; "bayer": geordnetes Dithering;
"fs": Floyd-Steinberg; "line": Linienraster; "ellipse": Kettenpunkte). Der Text ist nur durch die
Textur sichtbar, nicht durch Helligkeit.

PRESETS (unten) bündeln die Einstellungen für das finale Rätsel (raetsel.py).
"""
import cv2
import numpy as np
from PIL import Image

from common import PRINTSIM_OUT_DPI, Context, save_pdf_raster, save_png, side_by_side, to_u8

# ---------------------------------------------------------------------------
# Parameter
# ---------------------------------------------------------------------------
DPI = 600              # Ausgabeauflösung (1-Bit); >= 600 für saubere Punkte
LPI = 40               # Rasterweite in Linien pro Zoll (kleiner = gröbere Punkte, Text-Textur deutlicher)
ANGLE_OUT = 45.0       # Rasterwinkel außerhalb des Texts (Grad)
MODE_IN = "angle"      # "angle" | "bayer" | "fs" | "line" | "ellipse"
ANGLE_DELTA = 25.0     # nur MODE_IN="angle": Verdrehung im Text (20–30° empfohlen; 45° = maximal sichtbar)
LPI_IN = None          # optional andere Rasterweite im Text (None = gleich LPI; anders = auffälliger)
BAYER_SIZE = 8         # nur "bayer": Matrixgröße (2, 4, 8, 16)
BAYER_CELL_PX = 3      # nur "bayer": Pixel pro Matrixzelle (gröber = druckstabiler)
LINE_ANGLE_IN = 0.0    # nur "line": Winkel der Linien im Text (0 = waagerecht, 90 = senkrecht)
ELLIPSE_RATIO = 0.4    # nur "ellipse": 0 = rund, 1 = reine Linien (Kettenpunkte dazwischen)
GAMMA = 1.0            # Tonwertkorrektur vor dem Rastern (>1 heller, <1 dunkler)
PREVIEW_BLUR_MM = 0.8  # Gauß-Blur für die Vorschau
PREVIEW_SCALE = 0.08   # Verkleinerung für die Vorschau

# Fertige Einstellungen zum Vergleichen (überschreiben die Parameter oben)
PRESETS = {
    "standard": dict(MODE_IN="angle", ANGLE_DELTA=25.0),
    "winkel45": dict(MODE_IN="angle", ANGLE_DELTA=45.0),
}


def _screen(h, w, dpi, lpi, angle_deg, shape="round", ratio=0.0):
    """Schwellenmatrix eines Rasters (0..1). shape: "round" | "ellipse" (ratio) | "line"."""
    period = dpi / lpi
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    a = np.deg2rad(angle_deg)
    u = (xx * np.cos(a) + yy * np.sin(a)) / period
    v = (-xx * np.sin(a) + yy * np.cos(a)) / period
    if shape == "line":
        spot = 0.5 - 0.5 * np.cos(2 * np.pi * v)
    else:
        wu, wv = (0.25, 0.25) if shape == "round" else (0.25 * (1 - ratio), 0.25 * (1 + ratio))
        spot = 0.5 - (wu * np.cos(2 * np.pi * u) + wv * np.cos(2 * np.pi * v))  # 0 (Zellmitte) … 1
    # Spot-Funktion → gleichverteilte Schwellen (Histogramm-Ausgleich), damit Flächendeckung = Dunkelheit
    flat = np.sort(spot.ravel()[:: max(1, spot.size // 200000)])
    return np.interp(spot, flat, np.linspace(0, 1, flat.size)).astype(np.float32)


def _bayer(n):
    m = np.array([[0]])
    while m.shape[0] < n:
        m = np.block([[4 * m, 4 * m + 2], [4 * m + 3, 4 * m + 1]])
    return (m + 0.5) / m.size


def _bayer_screen(h, w, size, cell_px):
    t = np.kron(_bayer(size), np.ones((cell_px, cell_px)))
    reps = (h // t.shape[0] + 1, w // t.shape[1] + 1)
    return np.tile(t, reps)[:h, :w].astype(np.float32)


def _floyd_steinberg(dark):
    from PIL import Image as _I
    im = _I.fromarray(((1 - dark) * 255).astype(np.uint8)).convert("1")  # Pillow: FS-Dither
    return 1 - np.asarray(im, dtype=np.float32)


def texture_map(img: np.ndarray, dpi: int) -> np.ndarray:
    """Gabor-Energie für das Außen-Raster: hell = Außenraster, dunkel = anderes Raster (= Text)."""
    a = img.astype(np.float32) / 255.0
    s = 150 / dpi  # auf 150 dpi rechnen (schneller, entspricht Printsim)
    a = cv2.resize(a, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    period = 150 / LPI
    # Ein Punktraster mit Winkel θ hat Grundfrequenzen entlang θ und θ+90°; diagonale Komponente bei θ±45°
    energy = 0
    for th in (ANGLE_OUT, ANGLE_OUT + 90):
        k = cv2.getGaborKernel((0, 0), sigma=period * 1.2, theta=np.deg2rad(th), lambd=period, gamma=1.0, psi=0)
        k2 = cv2.getGaborKernel((0, 0), sigma=period * 1.2, theta=np.deg2rad(th), lambd=period, gamma=1.0, psi=np.pi / 2)
        energy = energy + cv2.filter2D(a, -1, k) ** 2 + cv2.filter2D(a, -1, k2) ** 2
    energy = cv2.GaussianBlur(np.sqrt(energy), (0, 0), period * 2)
    lo, hi = np.percentile(energy, (2, 98))
    return np.clip((energy - lo) / max(1e-6, hi - lo), 0, 1)


def after_printsim(ctx: Context, res: dict):
    """Zusätzliche Vorschau: Texturanalyse des Printsim (überlebt die Textur den Druck?)."""
    tm = texture_map(res["printsim"], min(DPI, PRINTSIM_OUT_DPI))
    save_png(ctx.out_dir("halftone") / f"{ctx.name}_printsim_texture.png", to_u8(tm))
    res["printsim_extra"] = ("Printsim+Textur", Image.fromarray(to_u8(tm)))


def render(ctx: Context, **overrides):
    """1-Bit-Bild (uint8 0/255) + Schwarzmasken. overrides überschreiben die Parameter oben."""
    P = {k: overrides.get(k, globals()[k]) for k in
         ("DPI", "LPI", "ANGLE_OUT", "MODE_IN", "ANGLE_DELTA", "LPI_IN", "BAYER_SIZE", "BAYER_CELL_PX",
          "LINE_ANGLE_IN", "ELLIPSE_RATIO", "GAMMA")}
    dpi = P["DPI"]
    photo = ctx.photo(dpi) ** P["GAMMA"]
    mask = ctx.mask(dpi) > 0.5
    h, w = photo.shape
    dark = 1.0 - photo

    out_black = dark > _screen(h, w, dpi, P["LPI"], P["ANGLE_OUT"])
    lpi_in = P["LPI_IN"] or P["LPI"]
    mode = P["MODE_IN"]
    if mode == "angle":
        in_black = dark > _screen(h, w, dpi, lpi_in, P["ANGLE_OUT"] + P["ANGLE_DELTA"])
    elif mode == "line":
        in_black = dark > _screen(h, w, dpi, lpi_in, P["LINE_ANGLE_IN"], shape="line")
    elif mode == "ellipse":
        in_black = dark > _screen(h, w, dpi, lpi_in, P["ANGLE_OUT"], shape="ellipse", ratio=P["ELLIPSE_RATIO"])
    elif mode == "bayer":
        in_black = dark > _bayer_screen(h, w, P["BAYER_SIZE"], P["BAYER_CELL_PX"])
    elif mode == "fs":
        in_black = _floyd_steinberg(dark) > 0.5
    else:
        raise ValueError(f"MODE_IN unbekannt: {mode}")
    black = np.where(mask, in_black, out_black)
    return np.where(black, 0, 255).astype(np.uint8), black, out_black, mask


def run(ctx: Context) -> dict:
    dpi = DPI
    img, black, out_black, mask = render(ctx)
    h, w = img.shape

    d = ctx.out_dir("halftone")
    save_png(d / f"{ctx.name}.png", img, dpi, bilevel=True)
    save_pdf_raster(ctx, d / f"{ctx.name}.pdf", Image.fromarray(img).convert("1"), f"halftone – {ctx.name}")

    pil = Image.fromarray(img)
    blur = cv2.GaussianBlur(img.astype(np.float32), (0, 0), PREVIEW_BLUR_MM / 25.4 * dpi)
    small = pil.resize((int(w * PREVIEW_SCALE), int(h * PREVIEW_SCALE)), Image.Resampling.LANCZOS)
    cx, cy = w // 2, int(h * 0.42)
    crop = pil.crop((cx - 500, cy - 350, cx + 500, cy + 350))
    tex = Image.fromarray(to_u8(texture_map(img, dpi)))
    prev = side_by_side([("normal", pil), (f"Blur {PREVIEW_BLUR_MM} mm", Image.fromarray(blur.astype(np.uint8))),
                         (f"verkleinert {int(PREVIEW_SCALE * 100)} %", small), ("Texturanalyse", tex),
                         ("Ausschnitt 100 %", crop)], 600)
    save_png(d / f"{ctx.name}_preview.png", prev)
    return {"image": img, "dpi": dpi, "preview": prev, "sheet_preview": ("Texturanalyse", tex),
            "info": {"Mittelwert innen/außen (Helligkeitsabgleich)":
                     f"{(1 - black[mask]).mean():.3f} / {(1 - out_black[mask]).mean():.3f}"}}
