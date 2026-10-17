"""Visuales: tarjetas con la portada, respiración lenta, zoom al bajo y miniaturas."""
from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .audio import FFmpegError, run

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]


def font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


# --------------------------------------------------------------------------- #
# Composición de imagen
# --------------------------------------------------------------------------- #
def _cover_fill(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Escala y recorta para llenar `size` (como CSS object-fit: cover)."""
    w, h = size
    r = max(w / img.width, h / img.height)
    big = img.resize((math.ceil(img.width * r), math.ceil(img.height * r)), Image.LANCZOS)
    x, y = (big.width - w) // 2, (big.height - h) // 2
    return big.crop((x, y, x + w, y + h))


def cover_card(cover: Path, size: tuple[int, int], fg_ratio: float = 0.80, dim: float = 0.42,
               cy: float = 0.5) -> Image.Image:
    """Portada nítida (centrada en la fracción `cy` de la altura) sobre su versión desenfocada y oscurecida."""
    src = Image.open(cover).convert("RGB")
    bg = _cover_fill(src, size).filter(ImageFilter.GaussianBlur(radius=max(size) / 40))
    bg = Image.blend(bg, Image.new("RGB", size, (0, 0, 0)), dim)
    side = int(min(size) * fg_ratio)
    r = min(side / src.width, side / src.height)
    fg = src.resize((max(int(src.width * r), 1), max(int(src.height * r), 1)), Image.LANCZOS)
    shadow = Image.new("RGBA", (fg.width + 120, fg.height + 120), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle((60, 60, 60 + fg.width, 60 + fg.height), fill=(0, 0, 0, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(28))
    px, py = (size[0] - fg.width) // 2, int(size[1] * cy - fg.height / 2)
    bg.paste(shadow, (px - 60, py - 60), shadow)
    bg.paste(fg, (px, py))
    return bg


def fill_image(path: Path, size: tuple[int, int]) -> Image.Image:
    return _cover_fill(Image.open(path).convert("RGB"), size)


def fit_font(draw: ImageDraw.ImageDraw, text: str, max_w: int, start: int, minimum: int = 22):
    """Achica la tipografía hasta que el texto entre en una línea."""
    size = start
    while size > minimum:
        f = font(size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return font(minimum)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=fnt) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def thumbnail(base: Image.Image, headline: str, sub: str, out: Path) -> None:
    """Miniatura 1280x720 legible; JPEG < 2 MB (límite de YouTube)."""
    img = _cover_fill(base, (1280, 720)).convert("RGBA")
    grad = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(720):
        a = int(max(0, (y - 260) / 460) * 215)
        gd.line((0, y, 1280, y), fill=(0, 0, 0, a))
    img = Image.alpha_composite(img, grad)
    d = ImageDraw.Draw(img)
    f1, f2 = font(78), font(34)
    lines = wrap(d, headline.upper(), f1, 1140)[:2]
    y = 720 - 64 - 34 - 12 - len(lines) * 88
    for ln in lines:
        d.text((70, y), ln, font=f1, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))
        y += 88
    d.text((72, 720 - 78), sub.upper(), font=f2, fill=(255, 214, 90, 255))
    out.parent.mkdir(parents=True, exist_ok=True)
    quality = 90
    while True:
        img.convert("RGB").save(out, "JPEG", quality=quality, optimize=True)
        if out.stat().st_size < 1_900_000 or quality <= 60:
            break
        quality -= 8


def thumbnail_split(cover: Path, headline: str, sub: str, out: Path) -> None:
    """Miniatura con la portada a la izquierda y el texto a la derecha (sin pisar el arte)."""
    W, H = 1280, 720
    src = Image.open(cover).convert("RGB")
    bg = _cover_fill(src, (W, H)).filter(ImageFilter.GaussianBlur(30))
    img = Image.blend(bg, Image.new("RGB", (W, H), (0, 0, 0)), 0.52).convert("RGBA")
    side = H - 140
    r = min(side / src.width, side / src.height)
    fg = src.resize((max(int(src.width * r), 1), max(int(src.height * r), 1)), Image.LANCZOS)
    sh = Image.new("RGBA", (fg.width + 100, fg.height + 100), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rectangle((50, 50, 50 + fg.width, 50 + fg.height), fill=(0, 0, 0, 170))
    sh = sh.filter(ImageFilter.GaussianBlur(22))
    x0, y0 = 70, (H - fg.height) // 2
    img.alpha_composite(sh, (x0 - 50, y0 - 50))
    img.paste(fg, (x0, y0))
    d = ImageDraw.Draw(img)
    tx, tw = x0 + fg.width + 56, W - (x0 + fg.width + 56) - 56
    lines, f1 = [headline.upper()], font(44)
    for size in range(92, 43, -4):
        f1 = font(size)
        lines = wrap(d, headline.upper(), f1, tw)
        if len(lines) <= 3 and all(d.textlength(ln, font=f1) <= tw for ln in lines):
            break
    f2 = font(32)
    sub_lines = wrap(d, sub.upper(), f2, tw)[:3]
    lh1, lh2 = int(f1.size * 1.12), 42
    block = len(lines) * lh1 + 22 + len(sub_lines) * lh2
    y = (H - block) // 2
    for ln in lines:
        d.text((tx, y), ln, font=f1, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))
        y += lh1
    y += 22
    for ln in sub_lines:
        d.text((tx, y), ln, font=f2, fill=(255, 214, 90, 255))
        y += lh2
    out.parent.mkdir(parents=True, exist_ok=True)
    quality = 90
    while True:
        img.convert("RGB").save(out, "JPEG", quality=quality, optimize=True)
        if out.stat().st_size < 1_900_000 or quality <= 60:
            break
        quality -= 8


def short_overlay(size: tuple[int, int], title: str, artist: str, cta: str, out: Path) -> None:
    w, h = size
    ov = Image.new("RGBA", size, (0, 0, 0, 0))
    g = ImageDraw.Draw(ov)
    for y in range(int(h * 0.58), h):
        a = int(max((y - h * 0.58) / (h * 0.42), 0.0) ** 1.4 * 225)
        g.line((0, y, w, y), fill=(0, 0, 0, a))
    f_t, f_a = font(70), font(40)
    f_c = fit_font(g, cta, w - 144, 38)
    lines = wrap(g, title, f_t, w - 140)[:3]
    y = int(h * 0.70)
    for ln in lines:
        g.text((70, y), ln, font=f_t, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))
        y += 82
    g.text((72, y + 6), artist.upper(), font=f_a, fill=(255, 214, 90, 255))
    g.text((72, int(h * 0.90)), cta, font=f_c, fill=(235, 235, 235, 255))
    ov.save(out)


# --------------------------------------------------------------------------- #
# Animación → ffmpeg
# --------------------------------------------------------------------------- #
def _encoder(size, fps, out: Path, audio: Path | None = None, overlay: Path | None = None,
             crf: int = 21, preset: str = "medium") -> subprocess.Popen:
    w, h = size
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
    if audio:
        cmd += ["-i", str(audio)]
    if overlay:
        cmd += ["-loop", "1", "-framerate", str(fps), "-i", str(overlay)]
        cmd += ["-filter_complex", f"[0:v][{2 if audio else 1}:v]overlay=0:0:format=auto,format=yuv420p[v]",
                "-map", "[v]"]
        if audio:
            cmd += ["-map", "1:a"]
    else:
        cmd += ["-vf", "format=yuv420p"]
        if audio:
            cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-r", str(fps), "-g", str(fps * 2),
            "-bf", "2", "-movflags", "+faststart"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    else:
        cmd += ["-an"]
    cmd.append(str(out))
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)


def _pump(proc: subprocess.Popen, frames: Iterable[bytes]) -> None:
    try:
        for fr in frames:
            proc.stdin.write(fr)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode("utf-8", "replace")
    if proc.wait() != 0:
        raise FFmpegError(f"ffmpeg (pipe) falló:\n{err[-1500:]}")


def _zoom_frame(base: Image.Image, size: tuple[int, int], z: float, drift: tuple[float, float] = (0, 0)) -> bytes:
    """Recorte centrado con zoom `z` (>=1) sobre una base más grande que la salida."""
    bw, bh = base.size
    cw, ch = bw / z, bh / z
    cx, cy = bw / 2 + drift[0] * bw, bh / 2 + drift[1] * bh
    x0 = min(max(cx - cw / 2, 0), bw - cw)
    y0 = min(max(cy - ch / 2, 0), bh - ch)
    return base.resize(size, Image.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch)).tobytes()


def breathing_clip(base: Image.Image, size, fps: int, seconds: float, amp: float, out: Path) -> None:
    """Clip que respira (zoom ida y vuelta): el último frame empalma con el primero → loop sin salto."""
    n = int(seconds * fps)

    def gen():
        for i in range(n):
            ph = i / n
            z = 1 + amp * 0.5 * (1 - math.cos(2 * math.pi * ph))
            drift = (0.004 * math.sin(2 * math.pi * ph), 0.003 * math.sin(4 * math.pi * ph))
            yield _zoom_frame(base, size, z, drift)

    proc = _encoder(size, fps, out)
    _pump(proc, gen())


def bass_clip(base: Image.Image, size, fps: int, env: np.ndarray, amp: float, audio: Path,
              overlay: Path, out: Path) -> None:
    """Short: la imagen late con el bajo; el texto va por encima y no se mueve."""
    def gen():
        for i, e in enumerate(env):
            z = 1 + amp * float(e) + 0.015 * (i / max(len(env), 1))   # deriva lenta + pulso
            yield _zoom_frame(base, size, z)

    proc = _encoder(size, fps, out, audio=audio, overlay=overlay, crf=20)
    _pump(proc, gen())


def normalize_loop(src: Path, size, fps: int, out: Path, pingpong: bool = True, max_seconds: float = 8.0) -> None:
    """Loop de video externo (Kie/Magnific) → mismos parámetros que el resto, listo para concatenar."""
    w, h = size
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},format=yuv420p"
    if pingpong:
        vf += ",split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1:a=0"
    run(["ffmpeg", "-v", "error", "-y", "-t", f"{max_seconds}", "-i", str(src), "-an", "-vf", vf,
         "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-r", str(fps), "-g", str(fps * 2),
         "-bf", "2", "-movflags", "+faststart", str(out)])
