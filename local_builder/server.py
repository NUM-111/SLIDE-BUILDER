"""Loopback-only local application server and project storage."""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import uuid
import zipfile
from datetime import datetime
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from local_builder.brand import detect_png_colors, image_dimensions
from local_builder.documents import extract_document
from local_builder.presentation import build_blank_template, build_presentation, compare_presentation, plan_slides


APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"
STATE_FILE = DATA_DIR / "project.json"
BRANDS_FILE = DATA_DIR / "brands.json"
DATA_LOCK = threading.RLock()
MAX_UPLOAD = 60 * 1024 * 1024
ALLOWED_DOCUMENTS = {".pdf", ".docx", ".pptx", ".txt"}


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _default_state() -> dict:
    return {
        "source": None,
        "blocks": [],
        "warnings": [],
        "report": None,
        "presentation": None,
        "slide_images": {},
        "brand": {
            "id": "floliz-inferred",
            "name": "FLOLIZ Nigeria",
            "company": "FLOLIZ NIGERIA LTD",
            "primary_color": "#1A0F49",
            "accent_color": "#C00000",
            "secondary_color": "#25C6E3",
            "color_sources": {"company": "detected from reference presentation", "primary_color": "detected from reference theme", "accent_color": "detected from reference slides", "secondary_color": "detected from reference theme"},
            "logo": None,
            "color_candidates": [],
        },
    }


def _safe_filename(name: str) -> str:
    base = Path(name.replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")
    return base[:120] or "upload"


def _multipart_files(content_type: str, body: bytes) -> list[tuple[str, str, bytes]]:
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + body
    )
    if not message.is_multipart():
        raise ValueError("Expected a multipart form upload.")
    files = []
    for part in message.iter_parts():
        field = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        if field and filename:
            files.append((field, filename, part.get_payload(decode=True) or b""))
    return files


def _multipart_form(content_type: str, body: bytes) -> tuple[dict[str, str], list[tuple[str, str, bytes]]]:
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + body
    )
    if not message.is_multipart():
        raise ValueError("Expected a multipart form upload.")
    fields = {}
    files = []
    for part in message.iter_parts():
        field = part.get_param("name", header="content-disposition")
        if not field:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files.append((field, filename, payload))
        else:
            fields[field] = payload.decode("utf-8", errors="replace")
    return fields, files


def _state() -> dict:
    state = _read_json(STATE_FILE, _default_state())
    if not isinstance(state, dict):
        state = _default_state()
    brand = state.get("brand", {})
    if brand.get("id") == "floliz-inferred" and brand.get("primary_color") == "#163B35":
        state["brand"] = {**_default_state()["brand"], **brand,
                           "name": "FLOLIZ Nigeria", "company": "FLOLIZ NIGERIA LTD",
                           "primary_color": "#1A0F49", "accent_color": "#C00000", "secondary_color": "#25C6E3",
                           "color_sources": {"company": "detected from reference presentation", "primary_color": "detected from reference theme", "accent_color": "detected from reference slides", "secondary_color": "detected from reference theme"}}
        _write_json(STATE_FILE, state)
    return state


class LocalHandler(BaseHTTPRequestHandler):
    server_version = "LocalPresentationBuilder/1.0"

    def log_message(self, format_string, *args):
        print(f"[{self.log_date_time_string()}] {format_string % args}")

    def _send(self, status: int, payload: bytes, content_type: str, download_name: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{_safe_filename(download_name)}"')
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, value) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_UPLOAD:
            raise ValueError("Upload exceeds the 60 MB local limit.")
        return self.rfile.read(length)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            page = APP_DIR / "static" / "index.html"
            self._send(200, page.read_bytes(), "text/html; charset=utf-8")
        elif path in ("/styles.css", "/app.js"):
            asset = APP_DIR / "static" / path.lstrip("/")
            content_type = "text/css; charset=utf-8" if path.endswith(".css") else "text/javascript; charset=utf-8"
            self._send(200, asset.read_bytes(), content_type)
        elif path == "/api/state":
            with DATA_LOCK:
                state = _state()
                self._json(200, {**state, "brand_profiles": _read_json(BRANDS_FILE, []),
                                 "slide_count": len(plan_slides(state.get("blocks", [])))})
        elif path == "/template":
            state = _state()
            logo_info = state.get("brand", {}).get("logo")
            logo = None
            logo_filename = ""
            if logo_info:
                logo_filename = logo_info.get("filename", "")
                image_path = UPLOAD_DIR / Path(logo_info.get("stored_as", "")).name
                if image_path.is_file():
                    logo = image_path.read_bytes()
            output = build_blank_template(state.get("brand", {}), logo, logo_filename)
            self._send(200, output, "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                       "FLOLIZ-editable-template.pptx")
        elif path.startswith("/download/"):
            name = unquote(path.rsplit("/", 1)[-1])
            state = _state()
            output = state.get("presentation")
            if not output or Path(output).name != name:
                self._json(404, {"error": "Presentation not found. Generate it again."})
                return
            file_path = EXPORT_DIR / name
            if not file_path.is_file():
                self._json(404, {"error": "Presentation file is missing."})
                return
            self._send(200, file_path.read_bytes(), "application/vnd.openxmlformats-officedocument.presentationml.presentation", name)
        elif path == "/health":
            self._json(200, {"status": "ok", "storage": str(DATA_DIR)})
        elif path.startswith("/local-image/"):
            name = Path(unquote(path.rsplit("/", 1)[-1])).name
            if not re.fullmatch(r"[a-f0-9]{12}-.+", name):
                self._json(404, {"error": "Image not found."})
                return
            image = UPLOAD_DIR / name
            if not image.is_file():
                self._json(404, {"error": "Image not found."})
                return
            content_type = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
            self._send(200, image.read_bytes(), content_type)
        else:
            self._json(404, {"error": "Not found."})

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            origin = self.headers.get("Origin")
            if origin:
                origin_host = urlsplit(origin).hostname
                request_host = self.headers.get("Host", "").split(":", 1)[0]
                if origin_host not in ("127.0.0.1", "localhost") or request_host not in ("127.0.0.1", "localhost"):
                    raise ValueError("Requests must come from this local application.")
            if path in ("/api/import", "/api/logo"):
                self._upload(path)
            elif path == "/api/slide-image":
                self._upload_slide_image()
            elif path == "/api/brand":
                self._save_brand()
            elif path == "/api/brand/select":
                self._select_brand()
            elif path == "/api/generate":
                self._generate()
            elif path == "/api/slide-image/remove":
                self._remove_slide_image()
            else:
                self._json(404, {"error": "Not found."})
        except ValueError as error:
            self._json(400, {"error": str(error)})
        except (OSError, zipfile.BadZipFile, KeyError) as error:
            self._json(422, {"error": f"Could not process this file: {error}"})
        except Exception as error:
            self._json(500, {"error": f"Local processing failed: {error}"})

    def _upload(self, endpoint: str) -> None:
        files = _multipart_files(self.headers.get("Content-Type", ""), self._read_body())
        field_name = "document" if endpoint.endswith("import") else "logo"
        upload = next((item for item in files if item[0] == field_name), None)
        if not upload:
            raise ValueError(f"Select a {field_name} file first.")
        _, filename, contents = upload
        if not contents:
            raise ValueError("The selected file is empty.")
        safe_name = _safe_filename(filename)
        stored = f"{uuid.uuid4().hex[:12]}-{safe_name}"
        if endpoint.endswith("import"):
            if Path(safe_name).suffix.lower() not in ALLOWED_DOCUMENTS:
                raise ValueError("Supported formats: PDF, DOCX, PPTX, and TXT.")
            inventory = extract_document(safe_name, contents)
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            (UPLOAD_DIR / stored).write_bytes(contents)
            with DATA_LOCK:
                state = _state()
                state.update({"source": {"filename": safe_name, "sha256": inventory["sha256"], "stored_as": stored},
                              "blocks": inventory["blocks"], "warnings": inventory["warnings"],
                              "report": None, "presentation": None, "slide_images": {}})
                _write_json(STATE_FILE, state)
            self._json(200, {"blocks": inventory["blocks"], "source": state["source"],
                             "warnings": inventory["warnings"], "slide_count": len(plan_slides(inventory["blocks"])),
                             "slide_images": state.get("slide_images", {})})
        else:
            suffix = Path(safe_name).suffix.lower()
            if suffix not in (".png", ".jpg", ".jpeg"):
                raise ValueError("Logo files must be PNG or JPG images.")
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            (UPLOAD_DIR / stored).write_bytes(contents)
            with DATA_LOCK:
                state = _state()
                state["brand"]["logo"] = {"filename": safe_name, "stored_as": stored}
                state["brand"]["color_candidates"] = detect_png_colors(contents) if suffix == ".png" else []
                state["brand"]["color_sources"]["logo"] = "uploaded by user"
                profiles = _read_json(BRANDS_FILE, [])
                for profile in profiles:
                    if profile.get("id") == state["brand"].get("id"):
                        profile["logo"] = state["brand"]["logo"]
                        profile["color_candidates"] = state["brand"]["color_candidates"]
                if profiles:
                    _write_json(BRANDS_FILE, profiles)
                _write_json(STATE_FILE, state)
            self._json(200, {"brand": state["brand"]})

    def _json_body(self) -> dict:
        try:
            value = json.loads(self._read_body().decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError("Invalid JSON request.") from error
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object.")
        return value

    def _save_brand(self) -> None:
        submitted = self._json_body()
        current = _default_state()["brand"]
        brand = {**current, **submitted}
        brand["id"] = str(brand.get("id") or uuid.uuid4())
        brand["name"] = str(brand.get("name", "Brand profile"))[:80]
        brand["company"] = str(brand.get("company", ""))[:100]
        brand.setdefault("secondary_color", "#25C6E3")
        for field in ("primary_color", "accent_color", "secondary_color"):
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(brand.get(field, ""))):
                raise ValueError(f"{field.replace('_', ' ').title()} must be a six-digit hex color.")
        with DATA_LOCK:
            state = _state()
            profiles = _read_json(BRANDS_FILE, [])
            stored_profile = next((entry for entry in profiles if entry.get("id") == brand["id"]), None)
            active_brand = state.get("brand", {})
            same_active = active_brand.get("id") == brand["id"]
            brand["logo"] = submitted.get("logo", (stored_profile or (active_brand if same_active else {})).get("logo"))
            brand["color_candidates"] = (stored_profile or (active_brand if same_active else {})).get("color_candidates", [])
            profiles = [entry for entry in profiles if entry.get("id") != brand["id"]]
            profiles.append(brand)
            _write_json(BRANDS_FILE, profiles)
            state["brand"] = brand
            _write_json(STATE_FILE, state)
        self._json(200, {"brand": brand, "brand_profiles": profiles})

    def _upload_slide_image(self) -> None:
        fields, files = _multipart_form(self.headers.get("Content-Type", ""), self._read_body())
        upload = next((item for item in files if item[0] == "picture"), None)
        if not upload:
            raise ValueError("Choose a picture to upload.")
        _, filename, contents = upload
        if not contents:
            raise ValueError("The selected picture is empty.")
        extension = Path(filename).suffix.lower()
        if extension not in (".png", ".jpg", ".jpeg"):
            raise ValueError("Slide pictures must be PNG or JPG images.")
        if image_dimensions(contents, extension.lstrip(".")) is None:
            raise ValueError("The selected file is not a readable PNG or JPG image.")
        try:
            slide_number = int(fields.get("slide_number", ""))
        except ValueError as error:
            raise ValueError("Choose a slide number first.") from error
        with DATA_LOCK:
            state = _state()
            slide_count = len(plan_slides(state.get("blocks", [])))
            if not state.get("blocks"):
                raise ValueError("Import a source document before assigning pictures.")
            if not 1 <= slide_number <= slide_count:
                raise ValueError(f"Choose a slide from 1 to {slide_count}.")
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            stored = f"{uuid.uuid4().hex[:12]}-{_safe_filename(filename)}"
            (UPLOAD_DIR / stored).write_bytes(contents)
            state.setdefault("slide_images", {})[str(slide_number)] = {
                "slide_number": slide_number,
                "filename": _safe_filename(filename),
                "stored_as": stored,
            }
            state["report"] = None
            state["presentation"] = None
            _write_json(STATE_FILE, state)
        self._json(200, {"slide_images": state["slide_images"]})

    def _remove_slide_image(self) -> None:
        submitted = self._json_body()
        try:
            slide_number = int(submitted.get("slide_number", 0))
        except (ValueError, TypeError) as error:
            raise ValueError("Invalid slide number.") from error
        with DATA_LOCK:
            state = _state()
            state.setdefault("slide_images", {}).pop(str(slide_number), None)
            state["report"] = None
            state["presentation"] = None
            _write_json(STATE_FILE, state)
        self._json(200, {"slide_images": state["slide_images"]})

    def _select_brand(self) -> None:
        submitted = self._json_body()
        with DATA_LOCK:
            profiles = _read_json(BRANDS_FILE, [])
            brand = next((entry for entry in profiles if entry.get("id") == submitted.get("id")), None)
            if not brand:
                raise ValueError("Saved brand profile not found.")
            state = _state()
            state["brand"] = brand
            _write_json(STATE_FILE, state)
        self._json(200, {"brand": brand})

    def _generate(self) -> None:
        with DATA_LOCK:
            state = _state()
            blocks = state.get("blocks", [])
            if not blocks:
                raise ValueError("Import a document with extractable text first.")
            logo_info = state.get("brand", {}).get("logo")
            logo = None
            logo_filename = ""
            if logo_info:
                logo_filename = logo_info.get("filename", "")
                logo_path = UPLOAD_DIR / Path(logo_info.get("stored_as", "")).name
                if logo_path.is_file():
                    logo = logo_path.read_bytes()
            slide_images = {}
            for slide_number, picture in state.get("slide_images", {}).items():
                image_path = UPLOAD_DIR / Path(picture.get("stored_as", "")).name
                if image_path.is_file():
                    slide_images[int(slide_number)] = (image_path.read_bytes(), picture.get("filename", "photo.png"))
            output = build_presentation(blocks, state.get("brand", {}), logo, logo_filename, slide_images)
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            filename = f"presentation-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pptx"
            (EXPORT_DIR / filename).write_bytes(output)
            report = compare_presentation(blocks, output)
            state["report"] = report
            state["presentation"] = filename
            _write_json(STATE_FILE, state)
        self._json(200, {"report": report, "download": f"/download/{filename}"})


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), LocalHandler)
    print(f"Local-First AI Presentation Builder: http://{host}:{port}")
    print(f"Project files are stored locally in: {DATA_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping local server.")
    finally:
        server.server_close()
