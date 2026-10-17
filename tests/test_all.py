"""Pruebas: parseo de nombres reales, metadata, selección, estado y publicadores (contra un servidor falso).

  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import email
import hashlib
import json
import os
import random
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotube import meta
from autotube.publish import Buffer, Cloudinary, PublishError, Zernio
from autotube.run import main as run_main, publish_short_via_buffer, spread_by_release
from autotube.sources import FileRef, Release, Track, parse_release_name, parse_track_name, build_release
from autotube.state import State

CFG = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text(encoding="utf-8"))


class Parsing(unittest.TestCase):
    def test_release_names_from_real_drive(self):
        self.assertEqual(parse_release_name("Wami - Galaxias - Micronomade & Wallar"),
                         ("Galaxias", "Micronomade & Wallar"))
        self.assertEqual(parse_release_name("Wami - Tune Up Di Sound - Wallar ft. Benjaminz"),
                         ("Tune Up Di Sound", "Wallar ft. Benjaminz"))
        self.assertEqual(parse_release_name("Wami - Faça Você Mesmo"), ("Faça Você Mesmo", ""))

    def test_track_names(self):
        folder = "Wami - Galaxias - Micronomade & Wallar"
        self.assertEqual(parse_track_name(f"{folder} - 01 Andrómeda.wav", folder), (1, "Andrómeda"))
        self.assertEqual(parse_track_name(f"{folder} - 06 Vía Láctea.wav", folder), (6, "Vía Láctea"))
        self.assertEqual(parse_track_name("YUNGA - Micronomae ft. Gaspar Om - Genaub.wav", "x"),
                         (0, "YUNGA - Micronomae ft. Gaspar Om - Genaub"))

    def test_build_release_filters_and_orders(self):
        folder = "Wami - Galaxias - Micronomade & Wallar"
        files = [FileRef("1", f"{folder} - 02 Bode.wav", 10), FileRef("2", f"{folder} - 01 Andrómeda.wav", 10),
                 FileRef("3", "cover.jpg", 99), FileRef("4", "desktop.ini", 1),
                 FileRef("5", f"{folder} - stem kick.wav", 10)]
        rel = build_release("rid", folder, files, CFG, {})
        self.assertEqual([t.title for t in rel.tracks], ["Andrómeda", "Bode"])   # sin stem, ordenado
        self.assertEqual(rel.cover.name, "cover.jpg")

    def test_overrides_can_disable_and_retitle(self):
        folder = "Wami - X - Y"
        files = [FileRef("1", f"{folder} - 01 A.wav", 10)]
        self.assertIsNone(build_release("r", folder, files, CFG, {folder: {"enabled": False}}))
        rel = build_release("r", folder, files, CFG, {folder: {"title": "Nuevo", "bandcamp_url": "https://x.bandcamp.com/album/n"}})
        self.assertEqual((rel.title, rel.bandcamp_url), ("Nuevo", "https://x.bandcamp.com/album/n"))


class Metadata(unittest.TestCase):
    def setUp(self):
        self.rel = Release("r1", "Wami - Galaxias - Micronomade & Wallar", "Galaxias", "Micronomade & Wallar")
        self.tracks = [Track(FileRef(str(i), f"{i}.wav"), "r1", "Galaxias", i, t) for i, t in enumerate(["A", "B", "C"], 1)]

    def test_chapters_start_at_zero_and_link_is_first_line(self):
        m = meta.compilation_meta("album", [(0, self.tracks[0]), (95.4, self.tracks[1]), (3700, self.tracks[2])],
                                  [self.rel], 3900, CFG, 0)
        lines = m["description"].splitlines()
        self.assertTrue(lines[0].startswith("🎧"))
        self.assertIn("https://wami.bandcamp.com?utm_source=youtube&utm_medium=compilation&utm_campaign=galaxias", lines[0])
        self.assertIn("00:00 A", m["description"])
        self.assertIn("01:35 B", m["description"])
        self.assertIn("1:01:40 C", m["description"])

    def test_title_never_exceeds_100_chars_and_minutes_round_down(self):
        m = meta.compilation_meta("mix", [(0, self.tracks[0])], [self.rel], 58 * 60, CFG, 0)
        self.assertIn("55 Min", m["title"])
        self.assertLessEqual(len(m["title"]), 100)
        long_rel = Release("r", "f", "Un título larguísimo " * 6, "Artista")
        m2 = meta.compilation_meta("album", [(0, self.tracks[0])], [long_rel], 3000, CFG, 0)
        self.assertLessEqual(len(m2["title"]), 100)

    def test_titles_rotate(self):
        titles = {meta.compilation_meta("mix", [(0, self.tracks[0])], [self.rel], 3600, CFG, n)["title"] for n in range(4)}
        self.assertEqual(len(titles), 4)

    def test_bandcamp_override_used(self):
        self.rel.bandcamp_url = "https://wami.bandcamp.com/album/galaxias"
        link = meta.bandcamp_link(CFG, self.rel, "short", "x")
        self.assertTrue(link.startswith("https://wami.bandcamp.com/album/galaxias?utm_source=youtube"))


class Selection(unittest.TestCase):
    def test_spread_avoids_long_runs(self):
        ts = [Track(FileRef(str(i), "a.wav"), "A" if i < 6 else "B", "t", i, str(i)) for i in range(9)]
        out = spread_by_release(ts, 2)
        self.assertEqual(len(out), 9)
        for i in range(2, len(out)):
            if all(t.release_id == out[i].release_id for t in out[i - 2:i]):
                # solo es aceptable cuando ya no queda otro release para intercalar
                self.assertEqual({t.release_id for t in out[i:]}, {out[i].release_id})

    def test_state_roundtrip_and_freshness(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            s = State(p)
            self.assertEqual(s.last_used("tracks", "a"), "")
            s.mark("tracks", "a", "A"); s.bump("short"); s.save()
            s2 = State(p)
            self.assertNotEqual(s2.last_used("tracks", "a"), "")
            self.assertEqual(s2.counter("short"), 1)
            p.write_text("{no es json", encoding="utf-8")
            State(p)                                    # un estado corrupto no debe romper
            self.assertTrue(p.with_suffix(".corrupt.json").exists())


class FakeServer(BaseHTTPRequestHandler):
    log: list = []
    fail_post = False
    buffer_error = False
    SECRET = "secret"

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n)

    def _send(self, code, obj=None):
        data = json.dumps(obj or {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        FakeServer.log.append(("GET", self.path, self.headers.get("Authorization")))
        self._send(200, {"accounts": [{"_id": "acc1", "platform": "youtube", "isActive": True}]})

    def do_PUT(self):
        body = self._body()
        FakeServer.log.append(("PUT", self.path, len(body), self.headers.get("Content-Type")))
        self._send(200)

    def _sig_ok(self, fields: dict) -> bool:
        p = {k: v for k, v in fields.items() if k not in ("signature", "api_key")}
        to_sign = "&".join(f"{k}={p[k]}" for k in sorted(p)) + self.SECRET
        return fields.get("signature") == hashlib.sha1(to_sign.encode()).hexdigest()

    def _cloudinary(self, raw: bytes, port: int):
        if self.path.endswith("/video/upload"):
            msg = email.message_from_bytes(f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + raw)
            fields, size = {}, 0
            for part in msg.get_payload():
                name = part.get_param("name", header="content-disposition")
                payload = part.get_payload(decode=True) or b""
                if name == "file":
                    size = len(payload)
                else:
                    fields[name] = payload.decode()
            FakeServer.log.append(("CLD_UP", self.path, fields, size))
            if not self._sig_ok(fields):
                return self._send(401, {"error": {"message": "Invalid Signature"}})
            pid = fields["public_id"]
            self._send(200, {"public_id": pid, "secure_url": f"http://127.0.0.1:{port}/res/{pid}.mp4"})
        elif self.path.endswith("/video/destroy"):
            fields = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            FakeServer.log.append(("CLD_DEL", self.path, fields))
            if not self._sig_ok(fields):
                return self._send(401, {"error": {"message": "Invalid Signature"}})
            self._send(200, {"result": "ok"})
        else:
            self._send(404)

    def do_POST(self):
        raw = self._body()
        port = self.server.server_address[1]
        if self.path.startswith("/cld/"):
            return self._cloudinary(raw, port)
        body = json.loads(raw or b"{}")
        if self.path == "/v1/media/presign":
            FakeServer.log.append(("PRESIGN", body))
            self._send(200, {"uploadUrl": f"http://127.0.0.1:{port}/put/{body['filename']}",
                             "publicUrl": f"http://127.0.0.1:{port}/pub/{body['filename']}", "expiresIn": 3600})
        elif self.path == "/v1/posts":
            FakeServer.log.append(("POST", body))
            if FakeServer.fail_post:
                self._send(207, {"message": "Post created but publishing failed", "post": {
                    "status": "failed", "platforms": [{"status": "failed", "errorMessage": "quota"}]}})
            else:
                self._send(201, {"post": {"_id": "p1", "status": "published", "platforms": [
                    {"status": "published", "platformPostUrl": "https://youtu.be/xyz"}]}})
        elif self.path == "/":                       # Buffer GraphQL
            FakeServer.log.append(("GQL", body))
            if "GetOrganizations" in body["query"]:
                self._send(200, {"data": {"account": {"organizations": [{"id": "org1", "name": "o"}]}}})
            elif "GetChannels" in body["query"]:
                self._send(200, {"data": {"channels": [{"id": "ch1", "name": "c", "service": "youtube"},
                                                       {"id": "ch2", "name": "i", "service": "instagram"}]}})
            elif FakeServer.buffer_error:
                self._send(200, {"data": {"createPost": {"message": "Channel not connected"}}})
            else:
                self._send(200, {"data": {"createPost": {"post": {"id": "b1", "dueAt": "2026-10-18T12:00:00Z"}}}})
        else:
            self._send(404)

    def log_message(self, *a):
        pass


class Publishers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), FakeServer)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.cfg = json.loads(json.dumps(CFG))
        cls.cfg["publish"]["zernio"]["base_url"] = f"http://127.0.0.1:{cls.port}/v1"
        cls.cfg["publish"]["buffer"]["api_url"] = f"http://127.0.0.1:{cls.port}/"
        cls.cfg["publish"]["cloudinary"]["api_base"] = f"http://127.0.0.1:{cls.port}/cld"
        cls.cld_url = "cloudinary://key:secret@democloud"
        cls.tmp = tempfile.TemporaryDirectory()
        cls.video = Path(cls.tmp.name) / "v.mp4"; cls.video.write_bytes(b"x" * 5000)
        cls.thumb = Path(cls.tmp.name) / "t.jpg"; cls.thumb.write_bytes(b"y" * 100)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.tmp.cleanup()

    def setUp(self):
        FakeServer.log = []; FakeServer.fail_post = False; FakeServer.buffer_error = False
        self.md = {"title": "T", "description": "D", "tags": ["dub"]}
        self.pub = self.cfg["publish"]

    def test_zernio_long_video(self):
        z = Zernio(self.cfg, api_key="k")
        acc = z.youtube_account()
        res = z.post_youtube(acc, self.video, self.thumb, self.md, self.pub)
        self.assertEqual(res["url"], "https://youtu.be/xyz")
        post = next(x[1] for x in FakeServer.log if x[0] == "POST")
        yt = post["platforms"][0]
        self.assertEqual(yt["accountId"], "acc1")
        self.assertEqual(yt["platformSpecificData"]["visibility"], "private")   # arranca privado por defecto
        self.assertEqual(yt["platformSpecificData"]["categoryId"], "10")
        self.assertTrue(post["publishNow"])
        media = post["mediaItems"][0]
        self.assertTrue(media["url"].endswith("/pub/v.mp4") and media["thumbnail"].endswith("/pub/t.jpg"))
        puts = [x for x in FakeServer.log if x[0] == "PUT"]
        self.assertEqual([p[2] for p in puts], [5000, 100])
        self.assertEqual(puts[0][3], "video/mp4")
        self.assertTrue(all(x[2] == "Bearer k" for x in FakeServer.log if x[0] == "GET"))

    def test_zernio_failure_is_reported(self):
        FakeServer.fail_post = True
        z = Zernio(self.cfg, api_key="k")
        with self.assertRaisesRegex(PublishError, "quota"):
            z.post_youtube("acc1", self.video, None, self.md, self.pub)

    def test_buffer_short_queue(self):
        cfg = json.loads(json.dumps(self.cfg)); cfg["publish"]["buffer"]["mode"] = "addToQueue"
        b = Buffer(cfg, api_key="k")
        self.assertEqual(b.youtube_channel(), "ch1")            # elige solo el canal de YouTube
        res = b.post_short("ch1", "http://x/pub/s.mp4", self.md, self.pub)
        self.assertEqual(res["id"], "b1")
        inp = next(x[1] for x in FakeServer.log if x[0] == "GQL" and "CreatePost" in x[1]["query"])["variables"]["input"]
        self.assertEqual(inp["channelId"], "ch1")
        self.assertEqual(inp["mode"], "addToQueue")
        self.assertEqual(inp["assets"], [{"video": {"url": "http://x/pub/s.mp4"}}])
        self.assertEqual(inp["metadata"]["youtube"]["privacy"], "private")
        self.assertEqual(inp["metadata"]["youtube"]["title"], "T")

    def test_buffer_custom_schedule_sends_dueAt(self):
        self.assertEqual(self.cfg["publish"]["buffer"]["mode"], "customScheduled")      # es el modo por defecto
        cfg = self.cfg
        Buffer(cfg, api_key="k").post_short("ch1", "http://x/s.mp4", self.md, self.pub)
        inp = next(x[1] for x in FakeServer.log if x[0] == "GQL")["variables"]["input"]
        self.assertEqual(inp["mode"], "customScheduled")
        self.assertRegex(inp["dueAt"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.000Z$")

    def test_buffer_error_is_reported(self):
        FakeServer.buffer_error = True
        with self.assertRaisesRegex(PublishError, "Channel not connected"):
            Buffer(self.cfg, api_key="k").post_short("ch1", "http://x/s.mp4", self.md, self.pub)

    def test_missing_keys_fail_clearly(self):
        import os
        os.environ.pop("ZERNIO_API_KEY", None); os.environ.pop("BUFFER_API_KEY", None)
        with self.assertRaisesRegex(PublishError, "ZERNIO_API_KEY"):
            Zernio(self.cfg)
        with self.assertRaisesRegex(PublishError, "BUFFER_API_KEY"):
            Buffer(self.cfg)
        os.environ.pop("CLOUDINARY_URL", None)
        with self.assertRaisesRegex(PublishError, "CLOUDINARY_URL"):
            Cloudinary(self.cfg)
        with self.assertRaisesRegex(PublishError, "formato"):
            Cloudinary(self.cfg, url="https://nope")

    # -- Cloudinary --------------------------------------------------------------
    def test_cloudinary_upload_is_signed_and_returns_public_url(self):
        c = Cloudinary(self.cfg, url=self.cld_url)
        url, pid = c.upload(self.video, "2026-10-17-short-1")
        self.assertEqual(pid, "autotube/2026-10-17-short-1")
        self.assertTrue(url.endswith("/res/autotube/2026-10-17-short-1.mp4"))
        up = next(x for x in FakeServer.log if x[0] == "CLD_UP")
        self.assertIn("/democloud/video/upload", up[1])
        self.assertEqual(up[3], 5000)                               # el archivo viajó completo
        self.assertEqual(up[2]["api_key"], "key")

    def test_cloudinary_bad_secret_is_reported(self):
        c = Cloudinary(self.cfg, url="cloudinary://key:otro@democloud")
        with self.assertRaisesRegex(PublishError, "Invalid Signature"):
            c.upload(self.video, "x")

    def test_cloudinary_delete_is_signed(self):
        Cloudinary(self.cfg, url=self.cld_url).delete("autotube/viejo")
        d = next(x for x in FakeServer.log if x[0] == "CLD_DEL")
        self.assertEqual(d[2]["public_id"], "autotube/viejo")

    def test_cloudinary_rejects_files_over_plan_limit(self):
        big = Path(self.tmp.name) / "big.mp4"; big.write_bytes(b"z" * 10)
        c = Cloudinary(self.cfg, url=self.cld_url); c.MAX_BYTES = 5
        with self.assertRaisesRegex(PublishError, "100 MB"):
            c.upload(big, "big")


class ShortViaBuffer(unittest.TestCase):
    """Flujo completo del Short: hosting → Buffer, con limpieza de lo viejo y sin dejar basura si falla."""

    @classmethod
    def setUpClass(cls):
        Publishers.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        Publishers.tearDownClass.__func__(cls)

    def setUp(self):
        FakeServer.log = []; FakeServer.buffer_error = False
        os.environ["CLOUDINARY_URL"] = self.cld_url
        os.environ["BUFFER_API_KEY"] = "k"
        self.state = State(Path(self.tmp.name) / f"st-{id(self)}.json")
        self.md = {"title": "T", "description": "D", "tags": []}

    def tearDown(self):
        os.environ.pop("CLOUDINARY_URL", None); os.environ.pop("BUFFER_API_KEY", None)

    def test_buffer_gets_the_cloudinary_url_and_state_tracks_it(self):
        res = publish_short_via_buffer(self.cfg, {}, self.state, self.video, "2026-10-17-short-1",
                                       self.md, self.cfg["publish"])
        self.assertEqual(res["hosted"], "autotube/2026-10-17-short-1")
        inp = next(x[1] for x in FakeServer.log if x[0] == "GQL" and "CreatePost" in x[1]["query"])["variables"]["input"]
        self.assertTrue(inp["assets"][0]["video"]["url"].endswith("/res/autotube/2026-10-17-short-1.mp4"))
        self.assertEqual([h["id"] for h in self.state.data["hosted"]], ["autotube/2026-10-17-short-1"])

    def test_old_hosted_files_are_deleted_and_recent_ones_kept(self):
        self.state.data["hosted"] = [{"id": "autotube/viejo", "at": "2020-01-01T00:00:00+00:00"},
                                     {"id": "autotube/reciente", "at": meta_now()}]
        publish_short_via_buffer(self.cfg, {}, self.state, self.video, "nuevo", self.md, self.cfg["publish"])
        deleted = [x[2]["public_id"] for x in FakeServer.log if x[0] == "CLD_DEL"]
        self.assertEqual(deleted, ["autotube/viejo"])
        self.assertEqual(sorted(h["id"] for h in self.state.data["hosted"]), ["autotube/nuevo", "autotube/reciente"])

    def test_buffer_rejection_cleans_the_upload(self):
        FakeServer.buffer_error = True
        with self.assertRaisesRegex(PublishError, "Channel not connected"):
            publish_short_via_buffer(self.cfg, {}, self.state, self.video, "fallido", self.md, self.cfg["publish"])
        self.assertEqual([x[2]["public_id"] for x in FakeServer.log if x[0] == "CLD_DEL"], ["autotube/fallido"])
        self.assertEqual(self.state.data["hosted"], [])


def meta_now() -> str:
    from autotube.state import now_iso
    return now_iso()


class LongVideosDisabled(unittest.TestCase):
    def test_compilation_is_skipped_without_touching_drive(self):
        self.assertEqual(CFG["publish"]["long_publisher"], "none")
        self.assertEqual(run_main(["compilation"]), 0)             # sale antes de listar música


if __name__ == "__main__":
    unittest.main()
