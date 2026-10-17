"""Acceso al catálogo de música: Google Drive (producción) o una carpeta local (pruebas)."""
from __future__ import annotations

import io
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

FOLDER_MIME = "application/vnd.google-apps.folder"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v"}


@dataclass
class FileRef:
    id: str
    name: str
    size: int = 0

    @property
    def ext(self) -> str:
        return Path(self.name).suffix.lower()


@dataclass
class Track:
    ref: FileRef
    release_id: str
    release_title: str
    number: int
    title: str
    path: Path | None = None        # se completa al descargar
    seconds: float = 0.0            # se completa al medir

    @property
    def id(self) -> str:
        return self.ref.id


@dataclass
class Release:
    id: str
    folder_name: str
    title: str
    artists: str
    tracks: list[Track] = field(default_factory=list)
    cover: FileRef | None = None
    bandcamp_url: str = ""
    path: str = ""                  # ruta legible dentro del catálogo


# --------------------------------------------------------------------------- #
# Parseo de nombres
# --------------------------------------------------------------------------- #
_NUM_RE = re.compile(r"^\s*(\d{1,3})\s*[\s.\-_)]+\s*(.+)$")


def parse_release_name(folder_name: str) -> tuple[str, str]:
    """'Wami - Galaxias - Micronomade & Wallar' -> ('Galaxias', 'Micronomade & Wallar')."""
    name = re.sub(r"^\s*wami\s*[-–.]\s*", "", folder_name, flags=re.I).strip()
    parts = [p.strip() for p in re.split(r"\s+[-–]\s+", name) if p.strip()]
    if not parts:
        return folder_name.strip(), ""
    return parts[0], " & ".join(parts[1:]) if len(parts) > 1 else ""


def parse_track_name(file_name: str, release_folder: str) -> tuple[int, str]:
    """Saca prefijo del release y numeración: '... - 01 Andrómeda.wav' -> (1, 'Andrómeda')."""
    stem = Path(file_name).stem
    if stem.lower().startswith(release_folder.lower()):
        stem = stem[len(release_folder):].lstrip(" -–._")
    elif re.match(r"^\s*wami\s*-", stem, flags=re.I) and " - " in stem:
        stem = stem.rsplit(" - ", 1)[-1]
    m = _NUM_RE.match(stem)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return 0, stem.strip() or Path(file_name).stem


def is_skipped(name: str, patterns: list[str]) -> bool:
    low = name.lower()
    return any(p.lower() in low for p in patterns)


def pick_cover(files: list[FileRef]) -> FileRef | None:
    imgs = [f for f in files if f.ext in IMAGE_EXT]
    if not imgs:
        return None
    for key in ("cover", "front", "artwork", "portada", "folder"):
        for f in imgs:
            if key in f.name.lower():
                return f
    return max(imgs, key=lambda f: f.size)


def build_release(rid: str, folder_name: str, files: list[FileRef], cfg: dict,
                  overrides: dict) -> Release | None:
    src = cfg["source"]
    exts = {e.lower() for e in src["audio_ext"]}
    audio = [f for f in files if f.ext in exts and not is_skipped(f.name, src["skip_name_patterns"])]
    if not audio:
        return None
    title, artists = parse_release_name(folder_name)
    ov = overrides.get(folder_name, {})
    if ov.get("enabled") is False:
        return None
    rel = Release(
        id=rid, folder_name=folder_name,
        title=ov.get("title", title),
        artists=ov.get("artists", artists or cfg["label"]["artist"]),
        cover=pick_cover(files),
        bandcamp_url=ov.get("bandcamp_url", ""),
        path=folder_name,
    )
    for f in audio:
        num, ttl = parse_track_name(f.name, folder_name)
        rel.tracks.append(Track(ref=f, release_id=rid, release_title=rel.title, number=num, title=ttl))
    rel.tracks.sort(key=lambda t: (t.number or 999, t.title.lower()))
    return rel


# --------------------------------------------------------------------------- #
# Fuentes
# --------------------------------------------------------------------------- #
class Source:
    cfg: dict
    overrides: dict

    def list_releases(self) -> list[Release]:  # pragma: no cover - interfaz
        raise NotImplementedError

    def fetch(self, ref: FileRef, dest: Path) -> Path:  # pragma: no cover - interfaz
        raise NotImplementedError

    def list_visuals(self) -> dict[str, list[FileRef]]:
        return {"landscape": [], "portrait": [], "loops": []}


class LocalSource(Source):
    """Carpeta local con la misma forma que Drive. Para pruebas."""

    def __init__(self, cfg: dict, overrides: dict, root: str | None = None):
        self.cfg, self.overrides = cfg, overrides
        self.root = Path(root or cfg["source"]["local_root"]).expanduser()

    def _walk(self, folder: Path, depth: int = 0):
        if depth > 4 or is_skipped(folder.name, self.cfg["source"]["skip_name_patterns"]):
            return
        files = [FileRef(id=str(p), name=p.name, size=p.stat().st_size)
                 for p in sorted(folder.iterdir()) if p.is_file()]
        rel = build_release(str(folder), folder.name, files, self.cfg, self.overrides)
        if rel:
            yield rel
        for sub in sorted(p for p in folder.iterdir() if p.is_dir()):
            yield from self._walk(sub, depth + 1)

    def list_releases(self) -> list[Release]:
        return list(self._walk(self.root))

    def fetch(self, ref: FileRef, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        os.symlink(ref.id, dest)
        return dest

    def list_visuals(self) -> dict[str, list[FileRef]]:
        out = {"landscape": [], "portrait": [], "loops": []}
        vroot = self.root.parent / "visuals"
        for kind in out:
            d = vroot / kind
            if d.is_dir():
                out[kind] = [FileRef(id=str(p), name=p.name, size=p.stat().st_size)
                             for p in sorted(d.iterdir()) if p.is_file()]
        return out


class DriveSource(Source):
    """Lee Drive con una cuenta de servicio de solo lectura (secret GDRIVE_SA_JSON)."""

    def __init__(self, cfg: dict, overrides: dict, sa_json: str | None = None):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        raw = sa_json or os.environ.get("GDRIVE_SA_JSON", "")
        if not raw:
            raise RuntimeError("Falta el secret GDRIVE_SA_JSON (JSON de la cuenta de servicio).")
        creds = service_account.Credentials.from_service_account_info(
            json.loads(raw), scopes=["https://www.googleapis.com/auth/drive.readonly"])
        self.svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.cfg, self.overrides = cfg, overrides

    def _children(self, folder_id: str) -> list[dict]:
        out, token = [], None
        while True:
            resp = self.svc.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, size)",
                pageSize=1000, pageToken=token,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            out += resp.get("files", [])
            token = resp.get("nextPageToken")
            if not token:
                return out

    def _walk(self, folder_id: str, name: str, depth: int = 0):
        if depth > 4 or is_skipped(name, self.cfg["source"]["skip_name_patterns"]):
            return
        kids = self._children(folder_id)
        files = [FileRef(id=k["id"], name=k["name"], size=int(k.get("size", 0)))
                 for k in kids if k["mimeType"] != FOLDER_MIME]
        rel = build_release(folder_id, name, files, self.cfg, self.overrides)
        if rel:
            yield rel
        for k in kids:
            if k["mimeType"] == FOLDER_MIME:
                yield from self._walk(k["id"], k["name"], depth + 1)

    def list_releases(self) -> list[Release]:
        out: list[Release] = []
        for fid in self.cfg["source"]["music_folder_ids"]:
            meta = self.svc.files().get(fileId=fid, fields="name", supportsAllDrives=True).execute()
            out += list(self._walk(fid, meta["name"]))
        return out

    def fetch(self, ref: FileRef, dest: Path) -> Path:
        from googleapiclient.http import MediaIoBaseDownload

        dest.parent.mkdir(parents=True, exist_ok=True)
        req = self.svc.files().get_media(fileId=ref.id, supportsAllDrives=True)
        with io.FileIO(dest, "wb") as fh:
            dl = MediaIoBaseDownload(fh, req, chunksize=64 * 1024 * 1024)
            done = False
            while not done:
                _, done = dl.next_chunk()
        return dest

    def list_visuals(self) -> dict[str, list[FileRef]]:
        out = {"landscape": [], "portrait": [], "loops": []}
        vid = self.cfg["source"].get("visuals_folder_id")
        if not vid:
            return out
        for k in self._children(vid):
            if k["mimeType"] == FOLDER_MIME and k["name"].lower() in out:
                out[k["name"].lower()] = [
                    FileRef(id=c["id"], name=c["name"], size=int(c.get("size", 0)))
                    for c in self._children(k["id"]) if c["mimeType"] != FOLDER_MIME]
        return out


def make_source(cfg: dict, overrides: dict, local_root: str | None = None) -> Source:
    kind = cfg["source"]["type"]
    if local_root or kind == "local":
        return LocalSource(cfg, overrides, local_root)
    return DriveSource(cfg, overrides)
