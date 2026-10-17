"""Audio con ffmpeg/numpy: duración, normalización, ventana más enérgica y envolvente del bajo."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np


class FFmpegError(RuntimeError):
    pass


def run(cmd: list[str], input_bytes: bytes | None = None) -> bytes:
    p = subprocess.run(cmd, input=input_bytes, capture_output=True)
    if p.returncode != 0:
        tail = p.stderr.decode("utf-8", "replace")[-1500:]
        raise FFmpegError(f"{' '.join(cmd[:6])} … falló:\n{tail}")
    return p.stdout


def probe_seconds(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    return float(out.decode().strip())


def _norm_chain(cfg: dict, seconds: float) -> str:
    a = cfg["audio"]
    fade = min(float(a["fade_seconds"]), max(seconds / 4, 0.1))
    chain = []
    if a.get("loudnorm", True):
        chain.append(f"loudnorm=I={a['target_lufs']}:TP=-1.0:LRA=11")
    chain.append(f"afade=t=in:d={fade:.2f}")
    chain.append(f"afade=t=out:st={max(seconds - fade, 0):.2f}:d={fade:.2f}")
    return ",".join(chain)


def process_track(src: Path, dst: Path, cfg: dict) -> float:
    """Normaliza + fades; deja WAV 48k estéreo 16 bit (todos iguales para poder concatenar)."""
    secs = probe_seconds(src)
    run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn",
         "-af", _norm_chain(cfg, secs), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(dst)])
    return probe_seconds(dst)


def cut_segment(src: Path, dst: Path, start: float, dur: float, cfg: dict) -> float:
    c = {**cfg, "audio": {**cfg["audio"], "fade_seconds": min(0.4, dur / 6)}}
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-vn",
         "-af", _norm_chain(c, dur), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(dst)])
    return probe_seconds(dst)


def decode_mono(path: Path, sr: int, lowpass: float | None = None,
                start: float | None = None, dur: float | None = None) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", str(path), "-vn", "-ac", "1", "-ar", str(sr)]
    if lowpass:
        cmd += ["-af", f"lowpass=f={lowpass}"]
    cmd += ["-f", "f32le", "-"]
    return np.frombuffer(run(cmd), dtype=np.float32)


def best_window(path: Path, window_s: float, total_s: float) -> float:
    """Inicio (s) de la ventana de `window_s` con más energía media, sin tocar intro/outro."""
    if total_s <= window_s + 1:
        return 0.0
    sr, hop = 8000, 0.5
    x = decode_mono(path, sr)
    n = int(len(x) // (sr * hop))
    if n < 4:
        return 0.0
    rms = np.sqrt((x[: n * int(sr * hop)].reshape(n, -1) ** 2).mean(axis=1))
    w = max(int(window_s / hop), 1)
    csum = np.concatenate([[0.0], np.cumsum(rms)])
    scores = (csum[w:] - csum[:-w]) / w                     # energía media por ventana
    lo = int((total_s * 0.08) / hop)
    hi = int(max((total_s - window_s - total_s * 0.08) / hop, lo + 1))
    hi = min(hi, len(scores))
    if hi <= lo:
        return 0.0
    return float(np.argmax(scores[lo:hi]) + lo) * hop


def bass_envelope(path: Path, fps: int, cutoff_hz: float) -> np.ndarray:
    """Envolvente 0..1 del bajo, un valor por frame de video, con ataque rápido y caída suave."""
    sr = 16000
    x = decode_mono(path, sr, lowpass=cutoff_hz)
    secs = len(x) / sr
    n_frames = max(int(secs * fps), 1)
    edges = np.linspace(0, len(x), n_frames + 1).astype(int)
    rms = np.array([np.sqrt((x[a:b] ** 2).mean()) if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])])
    ref = np.percentile(rms, 95) or 1.0
    env = np.clip(rms / ref, 0.0, 1.0)
    out = np.empty_like(env)
    prev = 0.0
    for i, v in enumerate(env):
        prev = max(v, prev * 0.80)
        out[i] = prev
    return out
