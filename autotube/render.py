"""Armado de los videos finales: compilado largo y Short vertical."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

from . import audio, visuals
from .audio import run
from .sources import Release, Source, Track

Visual = tuple[str, Path, str]          # (nombre, archivo local, tipo: cover | image | loop)


@dataclass
class Rendered:
    video: Path
    thumbnail: Path | None
    seconds: float
    chapters: list[tuple[float, Track]] = field(default_factory=list)
    visuals_used: list[str] = field(default_factory=list)
    thumb_src: tuple[Path, str] | None = None


def collect_visuals(releases: list[Release], source: Source, work: Path,
                    orientation: str) -> tuple[dict[str, Visual], list[Visual]]:
    """Portadas por release + banco extra (imágenes y loops de la carpeta de visuales)."""
    vdir = work / "visuals"
    covers: dict[str, Visual] = {}
    for i, rel in enumerate(releases):
        if rel.cover:
            path = source.fetch(rel.cover, vdir / f"cover_{i}{rel.cover.ext}")
            covers[rel.id] = (f"cover:{rel.title}", path, "cover")
    bank: list[Visual] = []
    vis = source.list_visuals()
    for j, ref in enumerate(vis.get(orientation, [])):
        bank.append((f"img:{ref.name}", source.fetch(ref, vdir / f"v_{j}{ref.ext}"), "image"))
    if orientation == "landscape":
        for j, ref in enumerate(vis.get("loops", [])):
            bank.append((f"loop:{ref.name}", source.fetch(ref, vdir / f"l_{j}{ref.ext}"), "loop"))
    return covers, bank


def _make_clip(item: Visual, idx: int, cfg: dict, work: Path) -> Path:
    cc = cfg["compilation"]
    size, fps = tuple(cc["size"]), int(cc["fps"])
    _, path, typ = item
    out = work / f"clip_{idx:02d}.mp4"
    if typ == "loop":
        visuals.normalize_loop(path, size, fps, out)
    else:
        base_size = (int(size[0] * 1.25), int(size[1] * 1.25))
        base = visuals.cover_card(path, base_size) if typ == "cover" else visuals.fill_image(path, base_size)
        visuals.breathing_clip(base, size, fps, float(cc["loop_seconds"]), float(cc["breathing_zoom"]), out)
    return out


def _emit(lines: list[str], clip: Path, clip_len: float, span: float) -> None:
    """Agrega `span` segundos del clip en loop; el último pedazo se corta con `outpoint`."""
    full = int(span // clip_len)
    lines += [f"file '{clip.resolve()}'"] * full
    rem = span - full * clip_len
    if rem > 0.04:
        lines += [f"file '{clip.resolve()}'", f"outpoint {rem:.3f}"]


def render_compilation(tracks: list[Track], releases: list[Release], cfg: dict, source: Source,
                       work: Path, rng: random.Random, per_track: bool) -> Rendered:
    """per_track=True (mix): cada tema muestra la portada de SU release y el corte cae justo en el cambio."""
    cc = cfg["compilation"]
    work.mkdir(parents=True, exist_ok=True)

    # 1) Audio: normalizar cada tema y medir la duración real (los capítulos salen de acá).
    adir = work / "audio"
    adir.mkdir(exist_ok=True)
    wavs, chapters, t = [], [], 0.0
    for i, tr in enumerate(tracks):
        wav = adir / f"{i:03d}.wav"
        dur = audio.process_track(tr.path, wav, cfg)
        chapters.append((t, tr))
        t += dur
        wavs.append(wav)
    total = t
    (work / "audio.txt").write_text("".join(f"file '{w.resolve()}'\n" for w in wavs), encoding="utf-8")

    # 2) Visuales → un clip corto en loop por cada portada / imagen del banco.
    covers, bank = collect_visuals(releases, source, work, "landscape")
    if not covers and not bank:
        raise RuntimeError("No hay visuales: ni portadas (cover.jpg) ni banco de imágenes.")
    rng.shuffle(bank)
    idx = 0
    cover_clip: dict[str, Path] = {}
    for rid, item in covers.items():
        cover_clip[rid] = _make_clip(item, idx, cfg, work)
        idx += 1
    bank_clips: list[Path] = []
    for item in bank:
        bank_clips.append(_make_clip(item, idx, cfg, work))
        idx += 1
    clip_len = {c: audio.probe_seconds(c) for c in [*cover_clip.values(), *bank_clips]}

    # 3) Línea de tiempo: segmentos (todo el video, o uno por tema) con su lista de clips.
    block = float(cc["block_minutes"]) * 60
    bounds = [(chapters[i][0], chapters[i + 1][0] if i + 1 < len(chapters) else total) for i in range(len(chapters))]
    segments = ([(a, b, tr.release_id) for (a, b), (_, tr) in zip(bounds, chapters)]
                if per_track else [(0.0, total, releases[0].id)])
    lines: list[str] = []
    for a, b, rid in segments:
        playlist = ([cover_clip[rid]] if rid in cover_clip else []) + bank_clips
        if not playlist:
            playlist = list(cover_clip.values())
        pos, k = a, 0
        while b - pos > 0.01:
            clip = playlist[k % len(playlist)]
            span = (b - pos) if len(playlist) == 1 else min(block, b - pos)
            _emit(lines, clip, clip_len[clip], span)
            pos += span
            k += 1
    (work / "video.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 4) Unir sin recodificar el video (los clips ya están codificados igual).
    out = work / "compilation.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(work / "video.txt"),
         "-f", "concat", "-safe", "0", "-i", str(work / "audio.txt"),
         "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
         "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)])

    first = covers.get(tracks[0].release_id) or (next(iter(covers.values())) if covers else bank[0])
    return Rendered(video=out, thumbnail=None, seconds=total, chapters=chapters,
                    visuals_used=[v[0] for v in [*covers.values(), *bank]], thumb_src=(first[1], first[2]))


def make_thumbnail(r: Rendered, headline: str, sub: str, work: Path) -> Path:
    """Miniatura 1280x720 con el título definitivo (se llama una vez conocida la duración real)."""
    path, typ = r.thumb_src
    out = work / "thumbnail.jpg"
    if typ == "cover":
        visuals.thumbnail_split(path, headline, sub, out)
    else:
        if typ == "image":
            base = visuals.fill_image(path, (1280, 720))
        else:  # solo hay loops de video: usamos un frame
            frame = work / "frame0.jpg"
            run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-frames:v", "1", str(frame)])
            base = visuals.fill_image(frame, (1280, 720))
        visuals.thumbnail(base, headline, sub, out)
    r.thumbnail = out
    return out


def render_short(track: Track, release: Release, cfg: dict, source: Source, work: Path,
                 rng: random.Random, cta: str) -> tuple[Rendered, float]:
    sc = cfg["short"]
    size, fps = tuple(sc["size"]), int(sc["fps"])
    work.mkdir(parents=True, exist_ok=True)
    dur = rng.uniform(*sc["seconds"])
    total = audio.probe_seconds(track.path)
    dur = min(dur, total - 1)
    start = audio.best_window(track.path, dur, total)

    wav = work / "short.wav"
    audio.cut_segment(track.path, wav, start, dur, cfg)
    env = audio.bass_envelope(wav, fps, float(sc["bass_cutoff_hz"]))

    covers, bank = collect_visuals([release], source, work, "portrait")
    options = [*covers.values(), *bank]
    if not options:
        raise RuntimeError("Sin visuales para el Short (falta cover.jpg).")
    name, path, typ = (covers[release.id] if release.id in covers and (not bank or rng.random() < 0.6)
                       else rng.choice(bank))
    base_size = (int(size[0] * 1.18), int(size[1] * 1.18))
    base = (visuals.cover_card(path, base_size, fg_ratio=0.86, cy=0.38) if typ == "cover"
            else visuals.fill_image(path, base_size))

    overlay = work / "overlay.png"
    visuals.short_overlay(size, track.title, cfg["label"]["artist"], cta, overlay)
    out = work / "short.mp4"
    visuals.bass_clip(base, size, fps, env, float(sc["bass_zoom"]), wav, overlay, out)
    return Rendered(video=out, thumbnail=None, seconds=audio.probe_seconds(out), visuals_used=[name]), start
