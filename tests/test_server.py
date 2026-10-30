import json
import tempfile
import struct
import threading
import unittest
import urllib.request
import zipfile
import zlib
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from local_builder import server


def tiny_png():
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00\x20\x40\x80\x20\x40\x80")) + chunk(b"IEND", b"")


class LocalServerWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [
            patch.object(server, "APP_DIR", root),
            patch.object(server, "DATA_DIR", root / "data"),
            patch.object(server, "UPLOAD_DIR", root / "data" / "uploads"),
            patch.object(server, "EXPORT_DIR", root / "data" / "exports"),
            patch.object(server, "STATE_FILE", root / "data" / "project.json"),
            patch.object(server, "BRANDS_FILE", root / "data" / "brands.json"),
        ]
        for item in self.patches:
            item.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.LocalHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def test_upload_generate_compare_and_download(self):
        boundary = "local-builder-test-boundary"
        original = b"AML Controls\nKeep this exact sentence."
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"source.txt\"\r\n"
            "Content-Type: text/plain\r\n\r\n"
        ).encode() + original + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            f"{self.base}/api/import", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Origin": self.base},
        )
        imported = json.loads(urllib.request.urlopen(request).read())
        self.assertEqual(2, len(imported["blocks"]))
        stored = Path(server.UPLOAD_DIR / imported["source"]["stored_as"])
        self.assertEqual(original, stored.read_bytes())

        generated = urllib.request.Request(
            f"{self.base}/api/generate", data=b"{}", method="POST",
            headers={"Content-Type": "application/json", "Origin": self.base},
        )
        result = json.loads(urllib.request.urlopen(generated).read())
        self.assertEqual("pass", result["report"]["status"])
        with urllib.request.urlopen(self.base + result["download"]) as response:
            presentation = response.read()
        with zipfile.ZipFile(__import__("io").BytesIO(presentation)) as archive:
            slide = archive.read("ppt/slides/slide1.xml")
        self.assertIn(b"Keep this exact sentence.", slide)

    def test_nonlocal_origin_is_rejected(self):
        request = urllib.request.Request(
            f"{self.base}/api/generate", data=b"{}", method="POST",
            headers={"Content-Type": "application/json", "Origin": "http://example.test"},
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request)
        self.assertEqual(400, raised.exception.code)

    def test_brand_profiles_are_saved_and_selected(self):
        profile = {
            "id": "company-two", "name": "Second Company", "company": "Second Co Ltd",
            "primary_color": "#204060", "accent_color": "#C08040",
        }
        request = urllib.request.Request(
            f"{self.base}/api/brand", data=json.dumps(profile).encode(), method="POST",
            headers={"Content-Type": "application/json", "Origin": self.base},
        )
        saved = json.loads(urllib.request.urlopen(request).read())
        self.assertEqual("company-two", saved["brand"]["id"])
        selection = urllib.request.Request(
            f"{self.base}/api/brand/select", data=json.dumps({"id": "company-two"}).encode(), method="POST",
            headers={"Content-Type": "application/json", "Origin": self.base},
        )
        selected = json.loads(urllib.request.urlopen(selection).read())
        self.assertEqual("Second Co Ltd", selected["brand"]["company"])
        self.assertEqual("company-two", json.loads((Path(server.DATA_DIR) / "project.json").read_text())["brand"]["id"])

    def test_picture_upload_is_bound_to_selected_slide_and_exported(self):
        boundary = "slide-image-test-boundary"
        photo = tiny_png()
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"slide_number\"\r\n\r\n1\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"picture\"; filename=\"staff.png\"\r\n"
            "Content-Type: image/png\r\n\r\n"
        ).encode() + photo + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            f"{self.base}/api/import", data=(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"source.txt\"\r\n"
                "Content-Type: text/plain\r\n\r\nTitle\nSource paragraph.\r\n"
                f"--{boundary}--\r\n"
            ).encode(), method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Origin": self.base},
        )
        urllib.request.urlopen(request).read()
        request = urllib.request.Request(
            f"{self.base}/api/slide-image", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Origin": self.base},
        )
        result = json.loads(urllib.request.urlopen(request).read())
        self.assertEqual("staff.png", result["slide_images"]["1"]["filename"])
        generated = urllib.request.Request(
            f"{self.base}/api/generate", data=b"{}", method="POST",
            headers={"Content-Type": "application/json", "Origin": self.base},
        )
        response = json.loads(urllib.request.urlopen(generated).read())
        state = json.loads((Path(server.STATE_FILE)).read_text())
        with zipfile.ZipFile(__import__("io").BytesIO((Path(server.EXPORT_DIR) / state["presentation"]).read_bytes())) as archive:
            self.assertIn("ppt/media/slide-1-photo.png", archive.namelist())
            self.assertIn(b"r:embed=\"rId3\"", archive.read("ppt/slides/slide1.xml"))
        self.assertEqual("pass", response["report"]["status"])

    def test_blank_template_download_returns_powerpoint(self):
        with urllib.request.urlopen(f"{self.base}/template") as response:
            content_type = response.headers.get_content_type()
            data = response.read()
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
            slides = [name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
        self.assertEqual("application/vnd.openxmlformats-officedocument.presentationml.presentation", content_type)
        self.assertEqual(8, len(slides))


if __name__ == "__main__":
    unittest.main()