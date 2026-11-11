"""Auslöser: Taste C → Foto von der ESP32-CAM (per USB) → input/foto.jpg → Rätsel-PDFs in output/.

    .venv/bin/python kamera.py          # Dauerbetrieb: C = Foto, P = drucken, Q = beenden
    .venv/bin/python kamera.py --test   # ein einzelnes Foto aufnehmen und beenden

P druckt output/foto_standard.pdf auf dem Drucker aus [drucker] in einstellungen.toml.
Die Tasten wirken, solange dieses Terminalfenster vorne ist.
Jedes neue Foto ersetzt das vorige (input/ und output/ enthalten immer nur ein Foto).
Neue Fotos werden im Hintergrund wie bei `raetsel.py --beobachten` verarbeitet (~20 s pro Foto).
Port, Drehung und Blitz stehen in einstellungen.toml unter [kamera].
Die Firmware der Kamera liegt in esp32cam/.
"""
from __future__ import annotations

import argparse
import glob
import io
import os
import sys
import termios
import threading
import time
import tomllib
import tty
import zlib
from datetime import datetime
from pathlib import Path

import subprocess

import serial
from PIL import Image

ROOT = Path(__file__).parent
CONFIG = ROOT / "einstellungen.toml"
INPUT = ROOT / "input"
OUTPUT = ROOT / "output"
MEDIEN = {"A4": "A4", "A5": "A5", "10x15cm": "4x6"}  # Papierformat aus [druck] → CUPS-Name
FOTO_NAME = "foto.jpg"  # jedes neue Foto ersetzt das vorige
BAUD = 115200          # mehr schafft der USB-Adapter nicht zuverlässig (siehe esp32cam/esp32cam.ino)
START_S = 8            # Öffnen des Ports startet die Kamera neu; so lange auf "BEREIT" warten
VERSUCHE = 3
PORT_MUSTER = ["/dev/cu.usbserial*", "/dev/cu.wchusbserial*", "/dev/cu.SLAB_USBtoUART*", "/dev/cu.usbmodem*"]


def _config() -> dict:
    with open(CONFIG, "rb") as f:
        k = tomllib.load(f).get("kamera", {})
    drehung = int(k.get("drehung", 0))
    if drehung % 90:
        raise SystemExit(f"[kamera] drehung = {drehung} – erlaubt sind 0, 90, 180, 270")
    return {"port": k.get("port", "auto"), "drehung": drehung % 360, "blitz": bool(k.get("blitz", False))}


def _port(wunsch: str) -> str:
    if wunsch != "auto":
        return wunsch
    gefunden = sorted(p for m in PORT_MUSTER for p in glob.glob(m))
    if not gefunden:
        raise ConnectionError("Keine Kamera am USB gefunden – ist das Kabel eingesteckt?")
    return gefunden[0]


class Kamera:
    def __init__(self, port: str = "auto"):
        self.port = _port(port)
        self.s = serial.Serial()
        self.s.port, self.s.baudrate, self.s.timeout = self.port, BAUD, 0.5
        self.s.dtr = self.s.rts = False  # sonst hält der Adapter die Kamera im Reset/Flash-Modus
        self.s.open()
        self._warte_bereit()

    def _warte_bereit(self):
        ende, buf = time.time() + START_S, b""
        while time.time() < ende and b"BEREIT" not in buf and b"FEHLER" not in buf:
            buf += self.s.read(256)
        if b"BEREIT" not in buf:  # Startmeldung verpasst oder Kamera hing → nachfragen
            antwort = self._befehl("PING")
            if antwort != "BEREIT":
                raise ConnectionError(f"Kamera an {self.port} antwortet nicht richtig: {antwort or 'keine Antwort'}")

    def _befehl(self, cmd: str, timeout: float = 5) -> str:
        self.s.reset_input_buffer()
        self.s.write(cmd.encode() + b"\n")
        self.s.timeout = timeout
        zeile = self.s.readline().decode(errors="replace").strip()
        self.s.timeout = 0.5
        return zeile

    def foto(self, blitz: bool = False) -> bytes:
        """Ein JPEG holen. Prüft Länge und CRC, wiederholt bei Übertragungsfehlern."""
        fehler = ""
        for _ in range(VERSUCHE):
            kopf = self._befehl("FOTO B" if blitz else "FOTO", timeout=10)
            teile = kopf.split()
            if len(teile) != 3 or teile[0] != "JPEG":
                fehler = kopf or "keine Antwort"
                if kopf.startswith("FEHLER Kamera"):
                    self._befehl("PING")  # Kamera versucht dabei, das Modul neu zu starten
                continue
            n, crc = int(teile[1]), teile[2]
            self.s.timeout = n / (BAUD / 10) + 5
            data = self.s.read(n)
            self.s.timeout = 0.5
            if len(data) == n and f"{zlib.crc32(data):08x}" == crc:
                return data
            fehler = f"Übertragungsfehler ({len(data)}/{n} Bytes)"
            time.sleep(0.5)
            self.s.reset_input_buffer()
        raise ConnectionError(f"Kein Foto von der Kamera: {fehler}")

    def close(self):
        self.s.close()


def foto_aufnehmen(kamera: Kamera) -> Path:
    """Foto aufnehmen, drehen und in input/ ablegen. Rückgabe: Pfad der Datei."""
    cfg = _config()
    img = Image.open(io.BytesIO(kamera.foto(cfg["blitz"])))
    img.load()
    if cfg["drehung"]:
        img = img.rotate(-cfg["drehung"], expand=True)  # PIL dreht gegen den Uhrzeigersinn
    INPUT.mkdir(exist_ok=True)
    ziel = INPUT / FOTO_NAME  # immer derselbe Name → das alte Foto wird überschrieben
    # unter fremder Endung schreiben, dann umbenennen → die Verarbeitung sieht nie eine halbe Datei
    tmp = ziel.with_suffix(".part")
    img.save(tmp, "JPEG", quality=95)
    for alt in INPUT.iterdir():  # in input/ liegt immer nur ein Foto
        if alt.is_file() and alt != ziel and alt != tmp and not alt.name.startswith("."):
            alt.unlink()
    os.replace(tmp, ziel)
    return ziel


def _aufnehmen(kamera: Kamera):
    t = time.time()
    pfad = foto_aufnehmen(kamera)
    with Image.open(pfad) as im:
        print(f"  → {pfad.relative_to(ROOT)}  ({im.width}×{im.height}, {time.time() - t:.1f}s)", flush=True)


def _drucker(wunsch: str) -> str:
    """Druckername für lp. "auto" → erster Drucker mit "DeskJet" im Namen, sonst der Standarddrucker."""
    if wunsch != "auto":
        return wunsch
    aus = subprocess.run(["lpstat", "-e"], capture_output=True, text=True).stdout.split()
    for name in aus:
        if "deskjet" in name.lower():
            return name
    std = subprocess.run(["lpstat", "-d"], capture_output=True, text=True).stdout
    if ":" in std:
        return std.split(":", 1)[1].strip()
    raise ConnectionError("Kein Drucker eingerichtet – Systemeinstellungen → Drucker & Scanner → Drucker hinzufügen")


def drucken():
    """output/foto_standard.pdf in Originalgröße (100 %) drucken."""
    with open(CONFIG, "rb") as f:
        cfg = tomllib.load(f)
    pdf = OUTPUT / f"{Path(FOTO_NAME).stem}_standard.pdf"
    foto = INPUT / FOTO_NAME
    if not pdf.exists():
        raise FileNotFoundError(f"{pdf.relative_to(ROOT)} gibt es noch nicht – erst mit C ein Foto aufnehmen")
    if foto.exists() and foto.stat().st_mtime > pdf.stat().st_mtime:
        raise RuntimeError("Das neue Foto wird noch verarbeitet – in ein paar Sekunden noch einmal P drücken")
    name = _drucker(cfg.get("drucker", {}).get("name", "auto"))
    befehl = ["lp", "-d", name, "-o", "print-scaling=none", "-o", "fit-to-page=false"]
    medium = MEDIEN.get(cfg["druck"]["papier"])
    if medium:
        befehl += ["-o", f"media={medium}"]
    erg = subprocess.run(befehl + [str(pdf)], capture_output=True, text=True)
    if erg.returncode:
        raise RuntimeError(f"Drucken fehlgeschlagen: {(erg.stderr or erg.stdout).strip()}")
    print(f"  → {pdf.name} an {name} geschickt", flush=True)


def dauerbetrieb(kamera: Kamera):
    import raetsel  # erst hier: lädt OpenCV usw.
    print(f"Kamera bereit an {kamera.port}.  C = Foto aufnehmen,  P = drucken,  Q = beenden", flush=True)
    threading.Thread(target=raetsel.watch, daemon=True).start()
    fd = sys.stdin.fileno()
    alt = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)  # Tasten sofort lesen, ohne Enter und ohne Echo
        while True:
            taste = sys.stdin.read(1)
            if taste in ("q", "Q"):
                break
            if taste in ("p", "P"):
                print(f"[{datetime.now():%H:%M:%S}] Drucken …", flush=True)
                try:
                    drucken()
                except Exception as e:
                    print(f"  FEHLER: {e}", flush=True)
                termios.tcflush(fd, termios.TCIFLUSH)  # mehrfaches P nicht mehrfach drucken
                continue
            if taste not in ("c", "C"):
                continue
            print(f"[{datetime.now():%H:%M:%S}] Knopf aktiviert – Foto wird aufgenommen …", flush=True)
            try:
                _aufnehmen(kamera)
            except Exception as e:  # Dauerbetrieb nicht wegen eines Fehlers beenden
                print(f"  FEHLER: {e}", flush=True)
            termios.tcflush(fd, termios.TCIFLUSH)  # C-Drücke während der Aufnahme verwerfen
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, alt)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", action="store_true", help="ein Foto aufnehmen und beenden")
    args = ap.parse_args()
    try:
        kamera = Kamera(_config()["port"])
    except Exception as e:
        sys.exit(f"FEHLER: {e}")
    try:
        if args.test:
            _aufnehmen(kamera)
        else:
            dauerbetrieb(kamera)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        sys.exit(f"FEHLER: {e}")
    finally:
        kamera.close()


if __name__ == "__main__":
    main()
