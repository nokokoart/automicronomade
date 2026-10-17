"""Publicadores: Zernio (videos largos y Shorts) y Buffer (Shorts).

Los formatos de pedido salen de la documentación oficial (docs.zernio.com y developers.buffer.com).
Los puntos que la doc no deja 100 % cerrados están marcados con VERIFICAR y se prueban en la
primera corrida supervisada (visibility: private).
"""
from __future__ import annotations

import mimetypes
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

TIMEOUT = 60


class PublishError(RuntimeError):
    pass


def _check(resp: requests.Response, what: str) -> dict:
    if resp.status_code >= 300 and resp.status_code != 207:
        raise PublishError(f"{what}: HTTP {resp.status_code} — {resp.text[:600]}")
    try:
        return resp.json()
    except ValueError:
        return {}


class Zernio:
    def __init__(self, cfg: dict, api_key: str | None = None):
        z = cfg["publish"]["zernio"]
        self.base = z["base_url"].rstrip("/")
        self.tz = z["timezone"]
        self.publish_now = z.get("publish_now", True)
        key = api_key or os.environ.get("ZERNIO_API_KEY", "")
        if not key:
            raise PublishError("Falta el secret ZERNIO_API_KEY.")
        self.h = {"Authorization": f"Bearer {key}"}

    def youtube_account(self, wanted: str = "") -> str:
        if wanted:
            return wanted
        accts = _check(requests.get(f"{self.base}/accounts", headers=self.h, timeout=TIMEOUT), "listar cuentas")
        yt = [a for a in accts.get("accounts", []) if a.get("platform") == "youtube" and a.get("isActive", True)]
        if len(yt) != 1:
            names = [a.get("username") for a in yt]
            raise PublishError(f"Hay {len(yt)} cuentas de YouTube en Zernio {names}; "
                               "poné zernio_account_id en config.yaml para el canal.")
        return yt[0]["_id"]

    def upload(self, path: Path) -> str:
        """Sube a almacenamiento temporal de Zernio (presign → PUT) y devuelve la URL pública."""
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        pre = _check(requests.post(f"{self.base}/media/presign", headers=self.h, timeout=TIMEOUT, json={
            "filename": path.name, "contentType": ctype, "size": path.stat().st_size}), "presign")
        with path.open("rb") as fh:
            put = requests.put(pre["uploadUrl"], data=fh, headers={"Content-Type": ctype}, timeout=3600)
        if put.status_code >= 300:
            raise PublishError(f"subida del archivo: HTTP {put.status_code} — {put.text[:300]}")
        return pre["publicUrl"]

    def post_youtube(self, account_id: str, video: Path, thumb: Path | None, meta: dict, pub: dict) -> dict:
        media = {"type": "video", "url": self.upload(video)}
        if thumb:
            media["thumbnail"] = self.upload(thumb)
        body = {
            "content": meta["description"],
            "tags": meta["tags"],
            "mediaItems": [media],
            "platforms": [{
                "platform": "youtube", "accountId": account_id,
                "platformSpecificData": {
                    "title": meta["title"], "visibility": pub["visibility"],
                    "categoryId": str(pub["category_id"]), "madeForKids": bool(pub["made_for_kids"]),
                    "containsSyntheticMedia": bool(pub.get("contains_ai_visuals", False)),
                },
            }],
        }
        if self.publish_now:
            body["publishNow"] = True
        else:  # VERIFICAR: formato exacto de `timezone` (se asume IANA)
            when = datetime.now() + timedelta(hours=1)
            body["scheduledFor"] = when.strftime("%Y-%m-%dT%H:%M:%S")
            body["timezone"] = self.tz
        data = _check(requests.post(f"{self.base}/posts", headers=self.h, json=body, timeout=TIMEOUT), "crear post")
        post = data.get("post", {})
        plat = (post.get("platforms") or [{}])[0]
        if post.get("status") == "failed" or plat.get("status") == "failed":
            raise PublishError(f"YouTube rechazó el video: {plat.get('errorMessage', data.get('message'))}")
        return {"id": post.get("_id"), "status": post.get("status"), "url": plat.get("platformPostUrl")}


class Buffer:
    CREATE = """
    mutation CreatePost($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess { post { id dueAt } }
        ... on MutationError { message }
      }
    }"""

    def __init__(self, cfg: dict, api_key: str | None = None):
        b = cfg["publish"]["buffer"]
        self.url, self.mode, self.delay = b["api_url"], b.get("mode", "addToQueue"), b.get("delay_hours", 3)
        key = api_key or os.environ.get("BUFFER_API_KEY", "")
        if not key:
            raise PublishError("Falta el secret BUFFER_API_KEY.")
        self.h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _gql(self, query: str, variables: dict | None = None) -> dict:
        r = requests.post(self.url, headers=self.h, json={"query": query, "variables": variables or {}},
                          timeout=TIMEOUT)
        data = _check(r, "Buffer")
        if data.get("errors"):
            raise PublishError(f"Buffer GraphQL: {data['errors']}")
        return data["data"]

    def youtube_channel(self, wanted: str = "") -> str:
        if wanted:
            return wanted
        orgs = self._gql("query GetOrganizations { account { organizations { id name } } }")
        org_id = orgs["account"]["organizations"][0]["id"]
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", str(org_id)):
            raise PublishError(f"Id de organización con formato inesperado: {org_id!r}")
        # El id va inline: la doc no muestra el nombre del tipo de la variable.
        ch = self._gql('query GetChannels { channels(input: {organizationId: "%s"}) { id name service } }'
                       % org_id)["channels"]
        yt = [c for c in ch if str(c.get("service", "")).lower() == "youtube"]
        if len(yt) != 1:
            raise PublishError(f"Hay {len(yt)} canales de YouTube en Buffer; poné buffer_channel_id en config.yaml.")
        return yt[0]["id"]

    def post_short(self, channel_id: str, video_url: str, meta: dict, pub: dict) -> dict:
        inp = {
            "text": meta["description"], "channelId": channel_id, "schedulingType": "automatic",
            "assets": [{"video": {"url": video_url}}],
            "metadata": {"youtube": {
                "title": meta["title"], "categoryId": str(pub["category_id"]),
                "privacy": pub["visibility"], "madeForKids": bool(pub["made_for_kids"]),
                "notifySubscribers": True, "isAiGenerated": bool(pub.get("contains_ai_visuals", False)),
            }},
        }
        if self.mode == "customScheduled":
            due = datetime.now(timezone.utc) + timedelta(hours=self.delay)
            inp |= {"mode": "customScheduled", "dueAt": due.strftime("%Y-%m-%dT%H:%M:%S.000Z")}
        else:
            inp["mode"] = "addToQueue"
        res = self._gql(self.CREATE, {"input": inp})["createPost"]
        if "message" in res:
            raise PublishError(f"Buffer rechazó el post: {res['message']}")
        post = res.get("post", {})
        return {"id": post.get("id"), "due": post.get("dueAt")}
