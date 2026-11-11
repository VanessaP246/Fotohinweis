"""Foto-Rätsel: Kamerabild aus input/ → zwei Halftone-Fotos mit verstecktem Hinweis in output/.

    .venv/bin/python raetsel.py               # alle neuen Bilder aus input/ verarbeiten
    .venv/bin/python raetsel.py --alle        # auch bereits verarbeitete Bilder neu erzeugen
    .venv/bin/python raetsel.py --beobachten  # läuft weiter und verarbeitet jedes neu abgelegte Bild sofort

Text, Position und Größe des Hinweises stehen in einstellungen.toml.

Pro Bild entstehen in output/:
    <bild>_standard.png / .pdf    Raster im Text 25° gedreht
    <bild>_winkel45.png / .pdf    Raster im Text 45° gedreht
    <bild>_kontrolle.png          Foto mit rot markierter Textposition (nur zur Kontrolle, nicht drucken)
"""
from __future__ import annotations

import argparse
import time
import tomllib
from pathlib import Path

import numpy as np
from PIL import Image

from common import PAPER_MM, Context, default_font, save_pdf_raster, save_png, to_u8
from variants import halftone as H

ROOT = Path(__file__).parent
CONFIG = ROOT / "einstellungen.toml"
INPUT = ROOT / "input"
OUTPUT = ROOT / "output"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
POLL_S = 2.0  # --beobachten: Prüfintervall in Sekunden


def load_config() -> dict:
    with open(CONFIG, "rb") as f:
        cfg = tomllib.load(f)
    h = cfg["hinweis"]
    if cfg["druck"]["papier"] not in PAPER_MM:
        raise SystemExit(f"Papier '{cfg['druck']['papier']}' unbekannt – erlaubt: {', '.join(PAPER_MM)}")
    unknown = set(cfg["raster"]["varianten"]) - set(H.PRESETS)
    if unknown:
        raise SystemExit(f"Variante(n) {', '.join(unknown)} unbekannt – erlaubt: {', '.join(H.PRESETS)}")
    for k in ("x", "y", "breite", "hoehe"):
        if not 0 <= h[k] <= 100:
            raise SystemExit(f"[hinweis] {k} = {h[k]} muss zwischen 0 und 100 liegen")
    return cfg


def control_image(ctx: Context) -> Image.Image:
    """Foto mit rot eingefärbter Text-Maske, um die Position zu prüfen."""
    dpi = 100
    photo = to_u8(ctx.photo(dpi))
    mask = ctx.mask(dpi) > 0.5
    rgb = np.stack([photo] * 3, axis=-1)
    rgb[mask] = (0.45 * rgb[mask] + 0.55 * np.array([230, 30, 30])).astype(np.uint8)
    return Image.fromarray(rgb)


def process(src: Path, cfg: dict):
    h = cfg["hinweis"]
    ctx = Context(src=src, name=src.stem, text=h["text"], out_root=OUTPUT, dpi=H.DPI,
                  paper=cfg["druck"]["papier"], font=h.get("schrift") or default_font(),
                  text_layout={k: h[k] for k in ("x", "y", "breite", "hoehe", "drehung")})
    OUTPUT.mkdir(exist_ok=True)
    control_image(ctx).save(OUTPUT / f"{ctx.name}_kontrolle.png")
    for name in cfg["raster"]["varianten"]:
        img, black, out_black, mask = H.render(ctx, LPI=cfg["raster"]["lpi"], **H.PRESETS[name])
        base = OUTPUT / f"{ctx.name}_{name}"
        save_png(base.with_suffix(".png"), img, H.DPI, bilevel=True)
        save_pdf_raster(ctx, base.with_suffix(".pdf"), Image.fromarray(img).convert("1"),
                        f"{ctx.name} – {name}")
        print(f"  → {base.name}.png / .pdf")


def is_done(src: Path, cfg: dict) -> bool:
    return all((OUTPUT / f"{src.stem}_{v}.pdf").exists() for v in cfg["raster"]["varianten"])


def pending(cfg: dict, force: bool) -> list[Path]:
    files = sorted((p for p in INPUT.iterdir() if p.suffix.lower() in IMAGE_EXT), key=lambda p: p.stat().st_mtime)
    return [p for p in files if force or not is_done(p, cfg)]


def run_once(cfg: dict, force: bool):
    todo = pending(cfg, force)
    if not todo:
        print("Keine neuen Bilder in input/.")
    for src in todo:
        t = time.time()
        print(f"{src.name}: Hinweis „{cfg['hinweis']['text']}“")
        process(src, cfg)
        print(f"  fertig in {time.time() - t:.1f}s")


def watch():
    print(f"Beobachte {INPUT}/ – neue Bilder werden automatisch verarbeitet (Strg+C zum Beenden).")
    sizes: dict[Path, int] = {}
    failed: set[Path] = set()
    while True:
        cfg = load_config()  # Änderungen an einstellungen.toml gelten sofort für das nächste Bild
        for src in pending(cfg, force=False):
            if src in failed:
                continue
            size = src.stat().st_size
            if sizes.get(src) != size:  # erst verarbeiten, wenn die Datei fertig geschrieben ist
                sizes[src] = size
                continue
            try:
                print(f"{src.name}: Hinweis „{cfg['hinweis']['text']}“")
                process(src, cfg)
            except Exception as e:  # ein defektes Bild soll den Dauerbetrieb nicht beenden
                print(f"  FEHLER bei {src.name}: {e}")
                failed.add(src)
                (OUTPUT / f"{src.stem}_FEHLER.txt").write_text(str(e), encoding="utf-8")
        time.sleep(POLL_S)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alle", action="store_true", help="auch bereits verarbeitete Bilder neu erzeugen")
    ap.add_argument("--beobachten", action="store_true", help="input/ dauerhaft beobachten")
    args = ap.parse_args()
    INPUT.mkdir(exist_ok=True)
    if args.beobachten:
        watch()
    else:
        run_once(load_config(), args.alle)


if __name__ == "__main__":
    main()
