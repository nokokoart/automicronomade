"""Pruebas: parseo de nombres reales, metadata, selección, estado y publicadores (contra un servidor falso).

  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotube import meta
from autotube.publish import Buffer, PublishError, Zernio
from autotube.run import spread_by_release
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

    def do_POST(self):
        body = json.loads(self._body() or b"{}")
        port = self.server.server_address[1]
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
        b = Buffer(self.cfg, api_key="k")
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
        cfg = json.loads(json.dumps(self.cfg)); cfg["publish"]["buffer"]["mode"] = "customScheduled"
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


if __name__ == "__main__":
    unittest.main()
