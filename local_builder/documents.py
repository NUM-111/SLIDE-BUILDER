"""Local document extraction with stable, traceable content blocks."""

from __future__ import annotations

import hashlib
import re
import uuid
import zipfile
import zlib
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


NAMESPACES = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}
BLOCK_NAMESPACE = uuid.UUID("7706d8bc-531b-4ee7-9206-338d0281acbf")
HEADING = re.compile(r"^(?:\d+(?:\.\d+)*\s+|[A-Z][A-Z0-9 ,:&()'/-]{4,}$)")
BULLET = re.compile(r"^\s*(?:[-*•▪◦‣]|\d+[.)])\s+")


def _block(source: str, location: str, kind: str, text: str) -> dict[str, str]:
    identity = uuid.uuid5(BLOCK_NAMESPACE, f"{source}\0{location}\0{kind}\0{text}")
    return {"id": str(identity), "source": source, "location": location, "type": kind, "text": text}


def _classify(text: str, preferred: str = "paragraph") -> str:
    if preferred != "paragraph":
        return preferred
    if BULLET.match(text):
        return "bullet"
    if len(text) < 100 and (HEADING.match(text) or (text.istitle() and not text.endswith("."))):
        return "heading"
    return "paragraph"


def _text_blocks(source: str, text: str, prefix: str) -> list[dict[str, str]]:
    blocks = []
    for index, value in enumerate(text.splitlines(), 1):
        value = value.strip()
        value = re.sub(r"(?<!\w)(?:[A-Za-z]\s+){2,}[A-Za-z](?!\w)", lambda match: re.sub(r"\s+", "", match.group()), value)
        value = re.sub(r" {2,}", " ", value)
        if value:
            blocks.append(_block(source, f"{prefix}:{index}", _classify(value), value))
    return blocks


def _xml_text(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.iter() if node.tag.endswith("}t"))


def _extract_docx(source: str, raw: bytes) -> list[dict[str, str]]:
    import io

    blocks: list[dict[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find(".//w:body", NAMESPACES)
    if body is None:
        return blocks
    paragraph_number = 0
    table_number = 0
    for child in body:
        if child.tag.endswith("}p"):
            text = _xml_text(child).strip()
            if text:
                paragraph_number += 1
                style = child.find("./w:pPr/w:pStyle", NAMESPACES)
                style_name = style.get(f"{{{NAMESPACES['w']}}}val", "") if style is not None else ""
                kind = "heading" if style_name.lower().startswith("heading") else _classify(text)
                blocks.append(_block(source, f"paragraph:{paragraph_number}", kind, text))
        elif child.tag.endswith("}tbl"):
            table_number += 1
            for row_number, row in enumerate(child.findall("./w:tr", NAMESPACES), 1):
                cells = [_xml_text(cell).strip() for cell in row.findall("./w:tc", NAMESPACES)]
                cells = [cell for cell in cells if cell]
                if cells:
                    blocks.append(_block(source, f"table:{table_number}:row:{row_number}", "table_row", " | ".join(cells)))
    return blocks


def _extract_pptx(source: str, raw: bytes) -> list[dict[str, str]]:
    import io

    blocks: list[dict[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        slide_names = sorted(
            (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=lambda name: int(re.search(r"slide(\d+)", name).group(1)),
        )
        for slide_number, name in enumerate(slide_names, 1):
            root = ET.fromstring(archive.read(name))
            shape_number = 0
            for shape in root.findall(".//p:sp", NAMESPACES):
                for paragraph in shape.findall("./p:txBody/a:p", NAMESPACES):
                    text = _xml_text(paragraph).strip()
                    if text:
                        shape_number += 1
                        blocks.append(_block(source, f"slide:{slide_number}:paragraph:{shape_number}", _classify(text), text))
            for table_number, table in enumerate(root.findall(".//a:tbl", NAMESPACES), 1):
                for row_number, row in enumerate(table.findall("./a:tr", NAMESPACES), 1):
                    cells = [_xml_text(cell).strip() for cell in row.findall("./a:tc", NAMESPACES)]
                    cells = [cell for cell in cells if cell]
                    if cells:
                        blocks.append(_block(source, f"slide:{slide_number}:table:{table_number}:row:{row_number}", "table_row", " | ".join(cells)))
    return blocks


def _pdf_objects(raw: bytes) -> dict[int, tuple[bytes, bytes]]:
    objects = {}
    for match in re.finditer(rb"(\d+)\s+\d+\s+obj\b(.*?)endobj", raw, re.S):
        number, body = int(match.group(1)), match.group(2)
        stream_match = re.search(rb"stream\r?\n(.*?)\r?\nendstream", body, re.S)
        data = stream_match.group(1) if stream_match else b""
        if b"/FlateDecode" in body and data:
            try:
                data = zlib.decompress(data)
            except zlib.error:
                try:
                    data = zlib.decompress(data, -zlib.MAX_WBITS)
                except zlib.error:
                    data = b""
        objects[number] = (body, data)
    return objects


def _pdf_cmap(objects: dict[int, tuple[bytes, bytes]]) -> dict[int, str]:
    result: dict[int, str] = {}
    for _, data in objects.values():
        for section in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
            pairs = re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", section)
            for source, target in pairs:
                try:
                    result[int(source, 16)] = bytes.fromhex(target.decode()).decode("utf-16-be")
                except (ValueError, UnicodeDecodeError):
                    pass
        for section in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
            pattern = rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<([0-9A-Fa-f]+)>|\[(.*?)\])"
            for match in re.finditer(pattern, section, re.S):
                first, last = int(match.group(1), 16), int(match.group(2), 16)
                if last - first > 2048:
                    continue
                if match.group(3).startswith(b"["):
                    values = re.findall(rb"<([0-9A-Fa-f]+)>", match.group(5))
                    for offset, value in enumerate(values):
                        try:
                            result[first + offset] = bytes.fromhex(value.decode()).decode("utf-16-be")
                        except (ValueError, UnicodeDecodeError):
                            pass
                else:
                    target = match.group(4)
                    base, width = int(target, 16), len(target)
                    for offset in range(last - first + 1):
                        try:
                            result[first + offset] = bytes.fromhex(f"{base + offset:0{width}x}").decode("utf-16-be")
                        except (ValueError, UnicodeDecodeError):
                            pass
    return result


def _pdf_literal(value: bytes) -> bytes:
    result = bytearray()
    index = 0
    escapes = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
    while index < len(value):
        char = value[index]
        if char == 92 and index + 1 < len(value):
            index += 1
            char = value[index]
            if char in b"01234567":
                octal = bytearray([char])
                for _ in range(2):
                    if index + 1 < len(value) and value[index + 1] in b"01234567":
                        index += 1
                        octal.append(value[index])
                result.append(int(octal, 8))
            elif char not in (10, 13):
                result.append(escapes.get(char, char))
        else:
            result.append(char)
        index += 1
    return bytes(result)


def _pdf_string(value: bytes, cmap: dict[int, str], hexadecimal: bool = False) -> str:
    if hexadecimal:
        value = re.sub(rb"\s", b"", value)
        if len(value) % 2:
            value += b"0"
        try:
            data = bytes.fromhex(value.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            return ""
    else:
        data = _pdf_literal(value)
    if data.startswith((b"\xfe\xff", b"\xff\xfe")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    if data.count(b"\0") >= max(2, len(data) // 3):
        try:
            return data.decode("utf-16-be")
        except UnicodeDecodeError:
            pass
    if cmap and hexadecimal:
        widths = sorted({max(1, (key.bit_length() + 7) // 8) for key in cmap}, reverse=True)
        output = []
        offset = 0
        while offset < len(data):
            matched = False
            for width in widths:
                code = int.from_bytes(data[offset:offset + width], "big")
                if offset + width <= len(data) and code in cmap:
                    output.append(cmap[code])
                    offset += width
                    matched = True
                    break
            if not matched:
                output.append(data[offset:offset + 1].decode("latin-1"))
                offset += 1
        return "".join(output)
    return data.decode("latin-1", errors="replace")


def _pdf_text(data: bytes, cmap: dict[int, str]) -> str:
    lines = []
    text_pattern = re.compile(rb"\[(.*?)\]\s*TJ|<([0-9A-Fa-f\s]+)>\s*Tj|\(((?:\\.|[^\\)])*)\)\s*Tj", re.S)
    item_pattern = re.compile(rb"<([0-9A-Fa-f\s]+)>|\(((?:\\.|[^\\)])*)\)", re.S)
    for text_object in re.findall(rb"\bBT\b(.*?)\bET\b", data, re.S):
        fragments = []
        for match in text_pattern.finditer(text_object):
            if match.group(1) is not None:
                array_fragments = []
                for item in item_pattern.finditer(match.group(1)):
                    if item.group(1) is not None:
                        array_fragments.append(_pdf_string(item.group(1), cmap, True))
                    else:
                        array_fragments.append(_pdf_string(item.group(2), cmap))
                fragments.append("".join(array_fragments))
            elif match.group(2) is not None:
                fragments.append(_pdf_string(match.group(2), cmap, True))
            else:
                fragments.append(_pdf_string(match.group(3), cmap))
        if fragments:
            lines.append(" ".join(fragments))
    return "\n".join(lines)


def _extract_pdf(source: str, raw: bytes) -> list[dict[str, str]]:
    objects = _pdf_objects(raw)
    cmap = _pdf_cmap(objects)
    pages = []
    for number, (body, _) in objects.items():
        if re.search(rb"/Type\s*/Page\b", body):
            refs = re.findall(rb"/Contents\s+(\d+)\s+\d+\s+R", body)
            array = re.search(rb"/Contents\s*\[(.*?)\]", body, re.S)
            if array:
                refs.extend(re.findall(rb"(\d+)\s+\d+\s+R", array.group(1)))
            pages.append((number, [int(ref) for ref in refs]))
    pages.sort(key=lambda item: item[0])
    if not pages:
        pages = [(number, [number]) for number, (_, data) in objects.items() if b"BT" in data]
    blocks = []
    for page_number, (_, refs) in enumerate(pages, 1):
        text = "\n".join(_pdf_text(objects[ref][1], cmap) for ref in refs if ref in objects)
        page_blocks = _text_blocks(source, text, f"page:{page_number}")
        for block in page_blocks:
            if any((ord(char) < 32 and char not in "\t\n\r") or 0x7F <= ord(char) <= 0x9F for char in block["text"]):
                block["uncertain"] = True
        blocks.extend(page_blocks)
    return blocks


def extract_document(filename: str, raw: bytes) -> dict[str, Any]:
    """Extract a supported file into deterministic blocks with source locations."""
    source = Path(filename).name
    extension = Path(source).suffix.lower()
    if extension == ".txt":
        blocks = _text_blocks(source, raw.decode("utf-8-sig", errors="replace"), "line")
    elif extension == ".docx":
        blocks = _extract_docx(source, raw)
    elif extension == ".pptx":
        blocks = _extract_pptx(source, raw)
    elif extension == ".pdf":
        blocks = _extract_pdf(source, raw)
    else:
        raise ValueError("Supported formats are PDF, DOCX, PPTX, and TXT.")
    uncertain_count = sum(bool(block.get("uncertain")) for block in blocks)
    warnings = []
    if not blocks:
        warnings.append("No extractable text found; scanned PDFs require OCR.")
    if uncertain_count:
        warnings.append(f"{uncertain_count} text block(s) contain undecodable PDF glyphs; verify the marked inventory entries against the source.")
    return {
        "source": source,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "blocks": blocks,
        "warnings": warnings,
    }
