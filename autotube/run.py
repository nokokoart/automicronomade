"""Orquestador.

  python -m autotube.run catalog                      # chequea el acceso y lista el catálogo
  python -m autotube.run compilation [--dry-run]      # arma (y publica) un compilado largo
  python -m autotube.run short       [--dry-run]      # arma (y publica) un Short
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
from datetime import date
from pathlib import Path

import yaml

from . import meta as metamod
from . import render
from .audio import FFmpegError, probe_seconds
from .publish import Buffer, Cloudinary, PublishError, Zernio
from .sources import Release, Source, Track, make_source
from .state import State

ROOT = Path(__file__).resolve().parent.parent


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}


def log(msg: str) -> None:
    print(msg, flush=True)


def run_ffmpeg_preview(src: Path, dst: Path, seconds: int = 90) -> None:
    from .audio import run as _run
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-t", str(seconds), "-c", "copy", str(dst)])


def summary(lines: list[str]) -> None:
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if p:
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# Selección
# --------------------------------------------------------------------------- #
def order_by_freshness(items, key_fn, state: State, kind: str, rng: random.Random):
    items = list(items)
    rng.shuffle(items)                                   # desempate aleatorio estable por semilla
    return sorted(items, key=lambda it: state.last_used(kind, key_fn(it)))


def spread_by_release(tracks: list[Track], max_run: int = 2) -> list[Track]:
    """Evita más de `max_run` temas seguidos del mismo release."""
    pending, out = list(tracks), []
    while pending:
        for i, t in enumerate(pending):
            tail = out[-max_run:]
            if len(tail) < max_run or any(x.release_id != t.release_id for x in tail):
                out.append(pending.pop(i))
                break
        else:
            out.append(pending.pop(0))
    return out


def download_and_measure(tr: Track, source: Source, work: Path, idx: int) -> None:
    dest = work / "src" / f"{idx:03d}{tr.ref.ext}"
    tr.path = source.fetch(tr.ref, dest)
    tr.seconds = probe_seconds(tr.path)


def pick_compilation(releases: list[Release], kind: str, cfg: dict, state: State, source: Source,
                     work: Path, rng: random.Random, minutes_override=None):
    cc = cfg["compilation"]
    min_track = float(cfg["source"]["min_track_seconds"])
    if kind == "album":
        for rel in order_by_freshness(releases, lambda r: r.id, state, "releases", rng):
            tracks = []
            for i, tr in enumerate(rel.tracks):
                download_and_measure(tr, source, work, i)
                if tr.seconds >= min_track:
                    tracks.append(tr)
            if sum(t.seconds for t in tracks) >= float(cc["min_album_minutes"]) * 60:
                return "album", tracks, [rel]
            log(f"· {rel.title}: dura menos de {cc['min_album_minutes']} min, pruebo otro")
        log("· ningún release alcanza el mínimo para 'album' → paso a 'mix'")
    # mix
    lo, hi = minutes_override or cc["mix_minutes"]
    target = rng.uniform(lo, hi) * 60
    rel_by_id = {r.id: r for r in releases}
    all_tracks = [t for r in releases for t in r.tracks]
    ordered = spread_by_release(order_by_freshness(all_tracks, lambda t: t.id, state, "tracks", rng))
    chosen, total = [], 0.0
    for i, tr in enumerate(ordered):
        if total >= target:
            break
        download_and_measure(tr, source, work, i)
        if tr.seconds < min_track:
            continue
        chosen.append(tr)
        total += tr.seconds
    if total < float(cc.get("min_mix_minutes", 20)) * 60:
        raise RuntimeError(f"Catálogo demasiado corto para un mix ({total/60:.1f} min).")
    chosen = spread_by_release(chosen)
    rels = [rel_by_id[rid] for rid in dict.fromkeys(t.release_id for t in chosen)]
    return "mix", chosen, rels


# --------------------------------------------------------------------------- #
# Modos
# --------------------------------------------------------------------------- #
def do_catalog(releases: list[Release]) -> None:
    n_tracks = sum(len(r.tracks) for r in releases)
    log(f"Catálogo: {len(releases)} releases, {n_tracks} temas")
    no_cover = [r.title for r in releases if not r.cover]
    for r in releases:
        log(f"  · {r.title} — {r.artists}  [{len(r.tracks)} temas{'' if r.cover else ', SIN cover'}]")
    if no_cover:
        log(f"⚠ Sin cover.jpg: {', '.join(no_cover)} (no se podrán usar en videos)")


def cleanup_hosted(host: Cloudinary, state: State) -> None:
    """Borra del hosting los Shorts viejos (Buffer los bajó hace rato). Si falla uno, sigue: no es crítico."""
    for pid in state.expired_hosted(host.keep_days):
        try:
            host.delete(pid)
            state.drop_hosted(pid)
            log(f"· hosting: borrado {pid}")
        except PublishError as e:
            log(f"⚠ no pude borrar {pid} del hosting ({e}); lo reintento en la próxima corrida")


def publish_short_via_buffer(cfg: dict, chan: dict, state: State, video: Path, name: str,
                             md: dict, pub: dict) -> dict:
    host, buf = Cloudinary(cfg), Buffer(cfg)
    channel_id = buf.youtube_channel(chan.get("buffer_channel_id", ""))   # si falla, que sea antes de subir
    cleanup_hosted(host, state)
    url, pid = host.upload(video, name)
    state.add_hosted(pid)
    try:
        res = buf.post_short(channel_id, url, md, pub)
    except PublishError:
        try:                                    # no dejar basura si Buffer rechazó el post
            host.delete(pid)
        except PublishError:
            pass
        state.drop_hosted(pid)
        raise
    return {**res, "hosted": pid}


def finish(args, state: State, entry: dict, out_dir: Path, work: Path, lines: list[str]) -> None:
    (out_dir / "result.json").write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    summary(lines)
    if not args.dry_run:
        state.log(entry)
        state.save()
    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="autotube")
    ap.add_argument("mode", choices=["catalog", "compilation", "short"])
    ap.add_argument("--channel", default="")
    ap.add_argument("--dry-run", action="store_true", help="arma todo pero no publica ni toca el estado")
    ap.add_argument("--kind", choices=["album", "mix"], default="")
    ap.add_argument("--visibility", default="", help="pisa publish.visibility de config.yaml")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--releases", default=str(ROOT / "releases.yaml"))
    ap.add_argument("--state", default=str(ROOT / "state" / "state.json"))
    ap.add_argument("--out", default=str(ROOT / "out"))
    ap.add_argument("--work", default=str(ROOT / "work"))
    ap.add_argument("--local-root", default="")
    ap.add_argument("--seed", default="")
    ap.add_argument("--minutes", default="", help="solo pruebas: 'min,max' para el mix")
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_yaml(Path(args.config))
    overrides = load_yaml(Path(args.releases)).get("releases", {})
    if args.visibility:
        cfg["publish"]["visibility"] = args.visibility
    chan = next((c for c in cfg["channels"] if not args.channel or c["name"] == args.channel), None)
    if not chan:
        log(f"Canal '{args.channel}' no está en config.yaml"); return 2

    if args.mode == "compilation" and not args.dry_run and cfg["publish"].get("long_publisher") == "none":
        msg = ("publish.long_publisher es 'none': Buffer no publica videos largos de YouTube, así que no armo "
               "el compilado. Para publicarlos, conectá Zernio y poné long_publisher: zernio. "
               "Con --dry-run sí se arma para revisarlo.")
        log(f"ℹ {msg}")
        summary(["### compilation — omitido", msg])
        return 0

    source = make_source(cfg, overrides, args.local_root or None)
    releases = source.list_releases()
    if not releases:
        log("No encontré música. Revisá music_folder_ids y que la carpeta esté compartida con la cuenta de servicio.")
        return 2
    if args.mode == "catalog":
        do_catalog(releases)
        return 0

    state = State(Path(args.state))
    seed = args.seed or f"{date.today().isoformat()}-{args.mode}-{chan['name']}-{state.counter(args.mode)}"
    rng = random.Random(seed)
    work = Path(args.work) / args.mode
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    out_dir = Path(args.out) / f"{date.today().isoformat()}-{args.mode}-{state.counter(args.mode) + 1}"
    out_dir.mkdir(parents=True, exist_ok=True)
    pub = {**cfg["publish"]}
    counter = state.counter(args.mode)

    try:
        if args.mode == "compilation":
            kinds = cfg["compilation"]["kinds_weights"]
            kind = args.kind or rng.choices(list(kinds), weights=list(kinds.values()))[0]
            mins = tuple(float(x) for x in args.minutes.split(",")) if args.minutes else None
            kind, tracks, rels = pick_compilation(releases, kind, cfg, state, source, work, rng, mins)
            log(f"▶ {kind}: {len(tracks)} temas de {len(rels)} release(s)")
            # Capítulos y metadata dependen de las duraciones finales → se arma después de renderizar.
            r = render.render_compilation(tracks, rels, cfg, source, work, rng, per_track=(kind == "mix"))
            md = metamod.compilation_meta(kind, r.chapters, rels, r.seconds, cfg, counter)
            thumb = render.make_thumbnail(r, md["headline"], md["sub"], work)
            video = r.video
            marks = [("releases", x.id, x.title) for x in rels] + [("tracks", t.id, t.title) for t in tracks]
        else:
            order = order_by_freshness([t for rel in releases for t in rel.tracks], lambda t: t.id,
                                       state, "tracks", rng)
            rel_by_id = {x.id: x for x in releases}
            track = None
            for i, cand in enumerate(order[:8]):
                download_and_measure(cand, source, work, i)
                if cand.seconds >= float(cfg["short"]["min_track_seconds"]):
                    track = cand
                    break
            if not track:
                raise RuntimeError("No encontré un tema lo bastante largo para un Short.")
            rel = rel_by_id[track.release_id]
            md = metamod.short_meta(track, rel, cfg, counter)
            r, start = render.render_short(track, rel, cfg, source, work, rng, md["cta"])
            md["window_start"] = round(start, 1)
            video, thumb = r.video, None
            marks = [("tracks", track.id, track.title)]
            log(f"▶ short: {track.title} ({rel.title}) desde {start:.0f}s")

        # Guardar salidas para revisión
        stamp = f"{date.today().isoformat()}-{args.mode}-{counter + 1}"      # nombres únicos en el hosting
        final_video = out_dir / f"{stamp}{video.suffix}"
        shutil.move(str(video), final_video)
        final_thumb = None
        if thumb:
            final_thumb = out_dir / f"{stamp}-thumb{thumb.suffix}"
            shutil.move(str(thumb), final_thumb)
        if args.dry_run and args.mode == "compilation":
            # Para revisar sin bajar 1 GB: los primeros 90 s (sin recodificar).
            run_ffmpeg_preview(final_video, out_dir / "preview.mp4")
        (out_dir / "meta.json").write_text(json.dumps(md, ensure_ascii=False, indent=2), encoding="utf-8")
        size_mb = final_video.stat().st_size / 1e6
        log(f"✔ render: {final_video.name} — {r.seconds/60:.1f} min, {size_mb:.0f} MB")
        log(f"  título: {md['title']}")
        log(f"  visuales: {', '.join(r.visuals_used[:8])}")

        entry = {"mode": args.mode, "title": md["title"], "seconds": round(r.seconds),
                 "visuals": r.visuals_used[:12],
                 "visibility": pub["visibility"], "dry_run": args.dry_run, "channel": chan["name"]}

        if not args.dry_run:
            if args.mode == "compilation":
                z = Zernio(cfg)
                res = z.post_youtube(z.youtube_account(chan.get("zernio_account_id", "")),
                                     final_video, final_thumb, md, pub)
            elif pub["shorts_publisher"] == "buffer":
                res = publish_short_via_buffer(cfg, chan, state, final_video, stamp, md, pub)
            else:
                z = Zernio(cfg)
                res = z.post_youtube(z.youtube_account(chan.get("zernio_account_id", "")),
                                     final_video, None, md, pub)
            entry["publish"] = res
            log(f"✔ publicado: {res}")
            for kind_, key, label in marks:
                state.mark(kind_, key, label)
            state.bump(args.mode)

        finish(args, state, entry, out_dir, work,
               [f"### {args.mode} — {'DRY RUN' if args.dry_run else 'publicado'}",
                f"**{md['title']}** · {r.seconds/60:.1f} min · {pub['visibility']}"])
        return 0

    except (FFmpegError, PublishError, RuntimeError) as e:
        log(f"✖ {e}")
        summary([f"### {args.mode} — ERROR", f"```\n{e}\n```"])
        return 1


if __name__ == "__main__":
    sys.exit(main())
