"""Genera un catálogo falso (audio sintético + portadas) con la misma forma que el Drive real.

  python tests/make_fixture.py /ruta/destino
"""
from __future__ import annotations

import math
import random
import sys
import wave
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFont

SR = 44100
ROOT = Path(__file__).resolve().parent.parent

RELEASES = {
    "Wami - Galaxias - Micronomade & Wallar": ["Andrómeda", "Bode", "Baby Boom", "Vía Láctea"],
    "Wami - One Drop - Wallar ft. Ranking Youth": ["One Drop", "One Drop Dub", "Rankin"],
    "Wami - Tune Up Di Sound - Wallar ft. Benjaminz": ["Tune Up", "Tune Up Version", "Selector"],
}


def synth(seconds: float, bpm: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    beat = 60.0 / bpm
    out = np.zeros(n)
    # bombo en 1 y 3, bajo sostenido, hi-hat a contratiempo, stab con eco (dub)
    for k in range(int(seconds / beat) + 1):
        s = int(k * beat * SR)
        m = min(int(0.35 * SR), n - s)
        if m <= 0:
            continue
        tt = np.arange(m) / SR
        if k % 2 == 0:
            out[s:s + m] += 0.9 * np.sin(2 * np.pi * (48 + 90 * np.exp(-tt * 28)) * tt) * np.exp(-tt * 9)
        else:
            h = min(int(0.05 * SR), n - s)
            out[s:s + h] += 0.08 * rng.standard_normal(h) * np.exp(-np.arange(h) / SR * 70)
        if k % 4 == 3:
            x = min(int(0.30 * SR), n - s)
            tx = np.arange(x) / SR
            stab = 0.22 * (np.sin(2 * np.pi * 330 * tx) + 0.6 * np.sin(2 * np.pi * 415 * tx)) * np.exp(-tx * 7)
            for d, g in ((0, 1.0), (0.375, 0.5), (0.75, 0.25)):
                o = s + int(d * SR)
                if o + x < n:
                    out[o:o + x] += stab * g
    bass_f = 55 * (2 ** (np.floor(t / (beat * 4)) % 3 * 3 / 12))
    out += 0.35 * np.sin(2 * np.pi * np.cumsum(bass_f) / SR) * (0.6 + 0.4 * np.sin(2 * np.pi * t / (beat * 2)))
    out /= max(np.abs(out).max(), 1e-6)
    out *= 0.85
    # intro/outro naturales para que no sea todo plano
    env = np.minimum(1, np.minimum(t / 4.0, (seconds - t) / 3.0))
    return out * env


def write_wav(path: Path, mono: np.ndarray) -> None:
    pan = np.stack([mono * 0.95, mono * 1.0], axis=1)
    data = (np.clip(pan, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes(data.tobytes())


def cover(path: Path, title: str, seed: int) -> None:
    rnd = random.Random(seed)
    size = 1500
    img = Image.new("RGB", (size, size))
    px = img.load()
    c1 = (rnd.randint(10, 60), rnd.randint(40, 120), rnd.randint(20, 90))
    c2 = (rnd.randint(150, 255), rnd.randint(120, 220), rnd.randint(10, 80))
    for y in range(size):
        r = y / size
        row = tuple(int(c1[i] * (1 - r) + c2[i] * r) for i in range(3))
        for x in range(0, size):
            px[x, y] = row
    d = ImageDraw.Draw(img)
    for _ in range(14):
        cx, cy, rad = rnd.randint(0, size), rnd.randint(0, size), rnd.randint(80, 420)
        col = (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
        d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), outline=col, width=rnd.randint(6, 18))
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 120)
    d.text((90, size - 260), title.upper(), font=font, fill=(255, 255, 255), stroke_width=4, stroke_fill=(0, 0, 0))
    img.save(path, "JPEG", quality=92)


def main(dest: Path, long: bool = False) -> None:
    music = dest / "music"
    for i, (rel, tracks) in enumerate(RELEASES.items()):
        folder = music / "wami backup" / rel
        folder.mkdir(parents=True, exist_ok=True)
        cover(folder / "cover.jpg", rel.split(" - ")[1], i)
        for n, name in enumerate(tracks, 1):
            secs = (300 + 6 * n + 4 * i) if long else (55 + 12 * n + 7 * i)
            write_wav(folder / f"{rel} - {n:02d} {name}.wav", synth(secs, 70 + 4 * n + i, seed=i * 10 + n))
    # banco de visuales extra (lo que después llenan Magnific / Kie)
    import subprocess
    for kind, size in (("landscape", (1920, 1080)), ("portrait", (1080, 1920))):
        d = dest / "visuals" / kind
        d.mkdir(parents=True, exist_ok=True)
        for k in range(2):
            img = Image.new("RGB", size)
            dr = ImageDraw.Draw(img)
            for y in range(size[1]):
                dr.line((0, y, size[0], y), fill=(20 + k * 90, 10 + y * 120 // size[1], 90 + k * 60))
            for _ in range(30):
                x, y, r = random.randint(0, size[0]), random.randint(0, size[1]), random.randint(20, 200)
                dr.ellipse((x - r, y - r, x + r, y + r), outline=(255, 220, 120), width=4)
            img.save(d / f"bank_{k}.jpg", "JPEG", quality=90)
    loops = dest / "visuals" / "loops"
    loops.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24",
                    "-t", "4", "-pix_fmt", "yuv420p", str(loops / "loop_0.mp4")], check=True)
    # cosas que NO deben entrar al catálogo
    (music / "MUSICA STEMS").mkdir(exist_ok=True)
    write_wav(music / "MUSICA STEMS" / "kick stem.wav", synth(20, 70, 99))
    # config de prueba
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    cfg["source"].update(type="local", local_root=str(music))
    cfg["source"]["min_track_seconds"] = 30
    if long:       # configuración de producción real (1080p, loops de 24 s, bloques de 3 min)
        (dest / "config.test.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        print("fixture largo listo en", dest)
        return
    cfg["compilation"].update(min_album_minutes=3, min_mix_minutes=2, loop_seconds=6, block_minutes=0.5,
                              size=[1280, 720], fps=24)
    cfg["short"].update(seconds=[14, 16], min_track_seconds=40)
    (dest / "config.test.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    print("fixture listo en", dest)


if __name__ == "__main__":
    main(Path(sys.argv[1]), long=len(sys.argv) > 2 and sys.argv[2] == "long")
