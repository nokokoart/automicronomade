"""Títulos, descripciones y capítulos."""
from __future__ import annotations

import re
from urllib.parse import quote

from .sources import Release, Track


def hms(sec: float) -> str:
    s = int(sec)
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "x"


def bandcamp_link(cfg: dict, release: Release | None, medium: str, campaign: str) -> str:
    base = (release.bandcamp_url if release and release.bandcamp_url else cfg["label"]["bandcamp_url"]).rstrip("/")
    sep = "&" if "?" in base else "?"
    return (f"{base}{sep}utm_source={cfg['label']['utm_source']}&utm_medium={medium}"
            f"&utm_campaign={quote(campaign)}")


def _title(template: str, **kw) -> str:
    t = template.format(**kw)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > 100:  # YouTube corta en 100: recortamos sin partir una palabra
        t = t[:99].rsplit(" ", 1)[0].rstrip(" |—-·") + "…"
    return t


def pick_template(options: list[str], n: int) -> str:
    return options[n % len(options)]


def compilation_meta(kind: str, tracks_with_start: list[tuple[float, Track]], releases: list[Release],
                     total_seconds: float, cfg: dict, counter: int) -> dict:
    label, artist = cfg["label"]["name"], cfg["label"]["artist"]
    minutes = int(total_seconds // 60 // 5 * 5) or int(total_seconds // 60)   # hacia abajo: nunca prometer de más
    main = releases[0]
    campaign = slug(main.title if kind == "album" else f"mix-{counter}")
    link = bandcamp_link(cfg, main if kind == "album" else None, "compilation", campaign)

    tmpl = pick_template(cfg["meta"]["title_templates"][kind], counter)
    title = _title(tmpl, title=main.title, artist=main.artists if kind == "album" else artist,
                   label=label, minutes=minutes)

    lab = cfg["label"]
    tag_es = lab.get("tagline_es") or f"{label} — sello independiente de dub y soundsystem."
    tag_en = lab.get("tagline_en") or f"{label} — independent dub & soundsystem label."
    lines = [f"🎧 Comprá la música / Get the music on Bandcamp → {link}", "", tag_es, tag_en, ""]
    if kind == "album":
        lines += [f"{main.title} — {main.artists}", ""]
    lines.append("Tracklist:")
    for start, tr in tracks_with_start:
        rel = tr.release_title if kind == "mix" else ""
        lines.append(f"{hms(start)} {tr.title}" + (f" — {rel}" if rel else ""))
    if kind == "mix":
        names = ", ".join(dict.fromkeys(r.title for r in releases))
        lines += ["", f"Releases en este mix / releases in this mix: {names}"]
    lines += ["", f"Música del catálogo de {label} / Music from the {label} catalogue.", "",
              " ".join(cfg["meta"]["hashtags"])]
    desc = "\n".join(lines)
    return {
        "title": title, "description": desc[:4900], "tags": cfg["meta"]["tags"], "link": link,
        "headline": main.title if kind == "album" else f"{minutes} min of heavy dub",
        "sub": f"{artist} · {label}" if kind == "mix" else f"{main.artists} · {label}",
    }


def short_meta(track: Track, release: Release, cfg: dict, counter: int) -> dict:
    label, artist = cfg["label"]["name"], cfg["label"]["artist"]
    link = bandcamp_link(cfg, release, "short", slug(track.title))
    tmpl = pick_template(cfg["meta"]["title_templates"]["short"], counter)
    title = _title(tmpl, track=track.title, artist=artist, label=label)
    desc = "\n".join([
        f"🎧 {track.title} — {release.title}",
        f"Tema completo / full track → {link}", "",
        f"{label} · {artist}", "",
        " ".join(cfg["meta"]["hashtags"] + ["#shorts"]),
    ])
    domain = re.sub(r"^https?://", "", cfg["label"]["bandcamp_url"]).rstrip("/")
    return {"title": title, "description": desc, "tags": cfg["meta"]["tags"], "link": link,
            "cta": f"Tema completo en Bandcamp · {domain}"}
