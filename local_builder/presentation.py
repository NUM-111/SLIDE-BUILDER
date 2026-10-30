"""Dynamic editable PowerPoint generation and content reconciliation."""

from __future__ import annotations

import io
import math
import re
import zipfile
import uuid
from collections import Counter, defaultdict
from html import escape
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from local_builder.brand import image_dimensions

EMU = 914400
SLIDE_WIDTH = 13_333_333
SLIDE_HEIGHT = 7_500_000
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
BASE_PPTX_PATH = Path(__file__).resolve().parent.parent / "templates" / "powerpoint-base.pptx"
ET.register_namespace("p", P_NS)
ET.register_namespace("a", A_NS)
ET.register_namespace("r", R_NS)
ET.register_namespace("", REL_NS)


def _chunks(text: str, limit: int = 420) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start, end)
            if boundary > start + limit // 2:
                end = boundary
        chunks.append(text[start:end])
        start = end
    return chunks or [""]


def _shape_height_inches(block: dict[str, Any], with_image: bool = False) -> float:
    font_size = 24 if block["type"] == "heading" else 17
    line_capacity = (32 if font_size > 20 else 51) if with_image else (54 if font_size > 20 else 88)
    lines = max(1, math.ceil(len(block["text"]) / line_capacity))
    line_height = 0.28 if font_size > 20 else 0.24
    minimum = 0.42 if font_size > 20 else 0.30
    return max(minimum, lines * line_height + 0.08)


def plan_slides(blocks: list[dict[str, str]], max_chars: int = 650, max_blocks: int = 12,
                image_slide_numbers: set[int] | None = None) -> list[list[dict[str, Any]]]:
    """Split long blocks and pack content without dropping source text."""
    image_slide_numbers = image_slide_numbers or set()
    segments = []
    for block in blocks:
        for part, text in enumerate(_chunks(block["text"])):
            segments.append({**block, "text": text, "segment": part})
    slides: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    char_count = 0
    height_used = 0.0
    for segment in segments:
        size = len(segment["text"])
        shape_height = _shape_height_inches(segment, True)
        if current and (segment.get("start_new_slide") or len(current) >= max_blocks or char_count + size > max_chars or height_used + shape_height + 0.03 > 5.45):
            slides.append(current)
            current, char_count, height_used = [], 0, 0.0
        current.append(segment)
        char_count += size
        height_used += shape_height + 0.03
    if current:
        slides.append(current)
    return slides


def _rgb(value: str, fallback: str) -> str:
    return value.lstrip("#").upper() if re.fullmatch(r"#?[0-9A-Fa-f]{6}", value or "") else fallback


def _picture_shape(shape_id: int, relationship_id: str, filename: str, data: bytes,
                   x: int, y: int, width: int, height: int) -> str:
    dimensions = image_dimensions(data, filename.rsplit(".", 1)[-1])
    crop = ""
    if dimensions:
        image_width, image_height = dimensions
        image_ratio = image_width / image_height
        box_ratio = width / height
        if image_ratio > box_ratio:
            side_crop = round((1 - box_ratio / image_ratio) * 50_000)
            crop = f' l="{side_crop}" r="{side_crop}"'
        elif image_ratio < box_ratio:
            vertical_crop = round((1 - image_ratio / box_ratio) * 50_000)
            crop = f' t="{vertical_crop}" b="{vertical_crop}"'
    return (
        f'<p:pic><p:nvPicPr><p:cNvPr id="{shape_id}" name="{escape(filename, quote=True)}" desc="User photo for selected slide"/>'
        '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
        f'<p:blipFill><a:blip r:embed="{relationship_id}"/><a:srcRect{crop}/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{width}" cy="{height}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:ln><a:solidFill><a:srgbClr val="D9DDE9"/></a:solidFill></a:ln></p:spPr></p:pic>'
    )


def _text_shape(shape_id: int, name: str, text: str, x: int, y: int, cx: int, cy: int,
                color: str, size: int, bold: bool = False, metadata: str = "") -> str:
    safe_name = escape(name, quote=True)
    descr = f' descr="{escape(metadata, quote=True)}"' if metadata else ""
    bold_attr = ' b="1"' if bold else ""
    escaped_text = escape("".join("\uFFFD" if (ord(char) < 32 and char not in "\t\n\r") or 0x7F <= ord(char) <= 0x9F else char for char in text))
    preserve = ' xml:space="preserve"' if text[:1].isspace() or text[-1:].isspace() else ""
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{safe_name}"{descr}/>'
        '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr>'
        f'<p:txBody><a:bodyPr wrap="square" anchor="t"><a:noAutofit/></a:bodyPr><a:lstStyle/><a:p>'
        f'<a:pPr/><a:r><a:rPr lang="en-US" sz="{size * 100}"{bold_attr} dirty="0"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill><a:latin typeface="Aptos"/></a:rPr>'
        f'<a:t{preserve}>{escaped_text}</a:t></a:r><a:endParaRPr lang="en-US"/></a:p></p:txBody></p:sp>'
    )


def _shape_tree(shapes: str) -> str:
    return (
        '<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        f'{shapes}</p:spTree>'
    )


def _slide_xml(content: list[dict[str, Any]], slide_number: int, total: int, brand: dict[str, Any],
               logo: bytes | None, logo_ext: str | None,
               slide_image: tuple[bytes, str] | None = None) -> tuple[str, str, dict[str, bytes]]:
    primary = _rgb(str(brand.get("primary_color", "")), "1A0F49")
    accent = _rgb(str(brand.get("accent_color", "")), "C00000")
    cyan = _rgb(str(brand.get("secondary_color", "")), "25C6E3")
    company = str(brand.get("company", "")) or "FLOLIZ NIGERIA LTD"
    shapes = []
    shape_id = 2
    shapes.append(_text_shape(shape_id, "Brand", company, int(0.55 * EMU), int(0.25 * EMU),
                              int(9.8 * EMU), int(0.48 * EMU), accent, 15, True))
    shape_id += 1
    shapes.append(
        f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="Brand accent"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{SLIDE_WIDTH}" cy="{int(0.075*EMU)}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:solidFill><a:srgbClr val="{primary}"/></a:solidFill></p:spPr></p:sp>'
    )
    shape_id += 1
    slide_relationships = []
    if logo and logo_ext:
        dimensions = image_dimensions(logo, logo_ext)
        if dimensions:
            image_width, image_height = dimensions
            fit = min(2.0 * EMU / image_width, 0.65 * EMU / image_height)
            logo_width = int(image_width * fit)
            logo_height = int(image_height * fit)
            logo_x = int(11.65 * EMU - logo_width / 2)
            logo_y = int(0.52 * EMU - logo_height / 2)
        else:
            logo_width, logo_height = int(1.0 * EMU), int(0.55 * EMU)
            logo_x, logo_y = int(11.15 * EMU), int(0.245 * EMU)
        shapes.append(
            f'<p:pic><p:nvPicPr><p:cNvPr id="{shape_id}" name="Company logo" descr="User-provided logo"/>'
            '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
            '<p:blipFill><a:blip r:embed="rId2"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
            f'<p:spPr><a:xfrm><a:off x="{logo_x}" y="{logo_y}"/><a:ext cx="{logo_width}" cy="{logo_height}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
        )
        slide_relationships.append(("rId2", "../media/brand." + logo_ext))
        shape_id += 1
    content_width = int(7.45 * EMU)
    panel_x, panel_y = int(8.55 * EMU), int(1.38 * EMU)
    panel_w, panel_h = int(4.15 * EMU), int(4.85 * EMU)
    shapes.append(
        f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="Photo panel"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{panel_x}" y="{panel_y}"/><a:ext cx="{panel_w}" cy="{panel_h}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:solidFill><a:srgbClr val="F5F5F8"/></a:solidFill>'
        f'<a:ln w="12700"><a:solidFill><a:srgbClr val="{cyan}"/></a:solidFill></a:ln></p:spPr></p:sp>'
    )
    shape_id += 1
    if slide_image:
        image_data, image_filename = slide_image
        picture = _picture_shape(shape_id, "rId3", image_filename, image_data,
                                 panel_x + int(0.08 * EMU), panel_y + int(0.08 * EMU),
                                 panel_w - int(0.16 * EMU), panel_h - int(0.16 * EMU))
        shapes.append(picture)
        shape_id += 1
        slide_relationships.append(("rId3", f"../media/slide-{slide_number}-photo.{image_filename.rsplit('.', 1)[-1].lower().replace('jpeg', 'jpg')}"))
    else:
        shapes.append(_text_shape(shape_id, "Optional picture slot", "OPTIONAL IMAGE", panel_x + int(0.35 * EMU),
                                  panel_y + int(2.22 * EMU), panel_w - int(0.7 * EMU), int(0.35 * EMU),
                                  "777783", 12, True))
        shape_id += 1
    y = int(1.25 * EMU)
    for block in content:
        font_size = 24 if block["type"] == "heading" else 17
        height = int(_shape_height_inches(block, bool(slide_image)) * EMU)
        metadata = f'inventory:{block["id"]}:{block["segment"]}'
        shapes.append(_text_shape(shape_id, f'Content {block["id"]} {block["segment"]}', block["text"],
                                  int(0.65 * EMU), y, content_width, height,
                                  accent if block["type"] == "heading" else "222222", font_size,
                                  block["type"] == "heading", metadata))
        shape_id += 1
        y += height + int(0.03 * EMU)
    shapes.append(
        f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="Footer band"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="0" y="{int(7.12 * EMU)}"/><a:ext cx="{SLIDE_WIDTH}" cy="{int(0.38 * EMU)}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:solidFill><a:srgbClr val="{primary}"/></a:solidFill></p:spPr></p:sp>'
    )
    shape_id += 1
    shapes.append(
        f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="Footer cyan rule"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="0" y="{int(7.10 * EMU)}"/><a:ext cx="{SLIDE_WIDTH}" cy="{int(0.025 * EMU)}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:solidFill><a:srgbClr val="{cyan}"/></a:solidFill></p:spPr></p:sp>'
    )
    shape_id += 1
    footer = f"Slide {slide_number} of {total}"
    shapes.append(_text_shape(shape_id, "Slide number", footer, int(11.25 * EMU), int(7.16 * EMU),
                              int(1.45 * EMU), int(0.22 * EMU), "FFFFFF", 9))
    rels = ''.join(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="{target}"/>' for rid, target in slide_relationships)
    slide = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld>'
        f'<p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>'
        f'{_shape_tree("".join(shapes))}</p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
        f'{rels}</Relationships>'
    )
    media = {}
    if slide_image:
        suffix = slide_image[1].rsplit(".", 1)[-1].lower().replace("jpeg", "jpg")
        media[f"ppt/media/slide-{slide_number}-photo.{suffix}"] = slide_image[0]
    return slide, relationships, media


def _assemble_pptx(rendered_slides: list[tuple[str, str, dict[str, bytes]]], brand: dict[str, Any],
                   logo: bytes | None, logo_ext: str | None) -> bytes:
    with zipfile.ZipFile(BASE_PPTX_PATH) as source:
        parts = {name: source.read(name) for name in source.namelist()}

    presentation = ET.fromstring(parts["ppt/presentation.xml"])
    slide_list = presentation.find(f"{{{P_NS}}}sldIdLst")
    for child in list(slide_list):
        slide_list.remove(child)

    relationships = ET.fromstring(parts["ppt/_rels/presentation.xml.rels"])
    for child in list(relationships):
        if child.get("Type", "").endswith("/slide"):
            relationships.remove(child)
    used_ids = {child.get("Id", "") for child in relationships}
    next_id = max([int(value[3:]) for value in used_ids if re.fullmatch(r"rId\d+", value)] or [0]) + 1
    for index in range(1, len(rendered_slides) + 1):
        relationship_id = f"rId{next_id + index - 1}"
        ET.SubElement(relationships, f"{{{REL_NS}}}Relationship", {
            "Id": relationship_id,
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide",
            "Target": f"slides/slide{index}.xml",
        })
        ET.SubElement(slide_list, f"{{{P_NS}}}sldId", {"id": str(255 + index), f"{{{R_NS}}}id": relationship_id})

    theme = ET.fromstring(parts["ppt/theme/theme1.xml"])
    color_scheme = theme.find(".//a:clrScheme", {"a": A_NS})
    colors = {
        "dk1": _rgb(str(brand.get("primary_color", "")), "1A0F49"),
        "lt1": "FFFFFF",
        "dk2": _rgb(str(brand.get("accent_color", "")), "C00000"),
        "lt2": "F2F2F2",
        "accent1": _rgb(str(brand.get("secondary_color", "")), "25C6E3"),
        "accent2": _rgb(str(brand.get("accent_color", "")), "C00000"),
        "accent3": "A9E26F",
        "accent4": "EAD000",
        "accent5": "1A0F49",
        "accent6": "FF4A01",
        "hlink": "25C6E3",
        "folHlink": "25C6E3",
    }
    for name, value in colors.items():
        entry = color_scheme.find(f"{{{A_NS}}}{name}")
        for child in list(entry):
            entry.remove(child)
        ET.SubElement(entry, f"{{{A_NS}}}srgbClr", {"val": value})

    content_types = ET.fromstring(parts["[Content_Types].xml"])
    for child in list(content_types):
        if child.tag == f"{{{CONTENT_NS}}}Override" and re.fullmatch(r"/ppt/slides/slide\d+\.xml", child.get("PartName", "")):
            content_types.remove(child)
        if child.tag == f"{{{CONTENT_NS}}}Default" and child.get("Extension", "").lower() in ("png", "jpg"):
            content_types.remove(child)
    for extension, content_type in (("png", "image/png"), ("jpg", "image/jpeg")):
        ET.SubElement(content_types, f"{{{CONTENT_NS}}}Default", {"Extension": extension, "ContentType": content_type})
    for index in range(1, len(rendered_slides) + 1):
        ET.SubElement(content_types, f"{{{CONTENT_NS}}}Override", {
            "PartName": f"/ppt/slides/slide{index}.xml",
            "ContentType": "application/vnd.openxmlformats-officedocument.presentationml.slide+xml",
        })

    def serialize(element: ET.Element) -> bytes:
        return ET.tostring(element, encoding="utf-8", xml_declaration=True)

    parts["ppt/presentation.xml"] = serialize(presentation)
    parts["ppt/_rels/presentation.xml.rels"] = serialize(relationships)
    parts["ppt/theme/theme1.xml"] = serialize(theme)
    parts["[Content_Types].xml"] = serialize(content_types)
    with io.BytesIO() as output:
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, value in parts.items():
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name) or re.fullmatch(r"ppt/slides/_rels/slide\d+\.xml\.rels", name):
                    continue
                archive.writestr(name, value)
            for index, (slide, slide_relationships, media) in enumerate(rendered_slides, 1):
                archive.writestr(f"ppt/slides/slide{index}.xml", slide)
                archive.writestr(f"ppt/slides/_rels/slide{index}.xml.rels", slide_relationships)
                for name, value in media.items():
                    archive.writestr(name, value)
            if logo and logo_ext:
                archive.writestr(f"ppt/media/brand.{logo_ext}", logo)
        return output.getvalue()


def build_presentation(blocks: list[dict[str, str]], brand: dict[str, Any] | None = None,
                       logo: bytes | None = None, logo_filename: str = "",
                       slide_images: dict[int, tuple[bytes, str]] | None = None) -> bytes:
    brand = brand or {}
    slide_images = slide_images or {}
    logo_ext = None
    if logo:
        suffix = logo_filename.lower().rsplit(".", 1)[-1]
        if suffix in ("png", "jpg", "jpeg"):
            logo_ext = "jpg" if suffix == "jpeg" else suffix
        else:
            logo = None
    slides = plan_slides(blocks, image_slide_numbers=set(slide_images))
    if not slides:
        slides = [[]]
    rendered_slides = [
        _slide_xml(content, index, len(slides), brand, logo, logo_ext, slide_images.get(index))
        for index, content in enumerate(slides, 1)
    ]
    return _assemble_pptx(rendered_slides, brand, logo, logo_ext)


def build_blank_template(brand: dict[str, Any] | None = None, logo: bytes | None = None,
                         logo_filename: str = "") -> bytes:
    """Create a reusable eight-slide deck with editable, clearly labeled placeholders."""
    page_content = [
        ("Presentation title", "Add a subtitle, team, and date."),
        ("Overview", "Add the purpose of this presentation and its key message."),
        ("Section divider", "Replace this with a new section heading."),
        ("Key points", "Add one source-backed point per line or paragraph."),
        ("Comparison", "Add the first side of your comparison."),
        ("Process or timeline", "Add an editable step, milestone, or date."),
        ("Image and caption", "Choose a slide number and upload a photo in the builder."),
        ("Closing", "Add the final takeaway or next step."),
    ]
    blocks = []
    for slide_number, (heading, body) in enumerate(page_content, 1):
        blocks.append({
            "id": str(uuid.uuid4()), "source": "FLOLIZ blank template",
            "location": f"template:slide:{slide_number}", "type": "heading", "text": heading,
            "start_new_slide": True,
        })
        blocks.append({
            "id": str(uuid.uuid4()), "source": "FLOLIZ blank template",
            "location": f"template:slide:{slide_number}:body", "type": "paragraph", "text": body,
        })
    return build_presentation(blocks, brand, logo, logo_filename)


def compare_presentation(blocks: list[dict[str, str]], presentation_bytes: bytes) -> dict[str, Any]:
    """Compare inventory text to marked content shapes in the generated PPTX."""
    emitted: dict[str, list[tuple[int, str]]] = defaultdict(list)
    with zipfile.ZipFile(io.BytesIO(presentation_bytes)) as archive:
        names = sorted((name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                       key=lambda name: int(re.search(r"slide(\d+)", name).group(1)))
        for name in names:
            root = ET.fromstring(archive.read(name))
            for shape in root.findall(f".//{{{P_NS}}}sp"):
                properties = shape.find(f"./{{{P_NS}}}nvSpPr/{{{P_NS}}}cNvPr")
                marker = properties.get("descr", "") if properties is not None else ""
                match = re.fullmatch(r"inventory:([0-9a-f-]+):(\d+)", marker)
                if match:
                    text = "".join(node.text or "" for node in shape.findall(f".//{{{A_NS}}}t"))
                    emitted[match.group(1)].append((int(match.group(2)), text))
    expected_ids = Counter(block["id"] for block in blocks)
    missing, altered, duplicated, uncertain = [], [], [], []
    for block in blocks:
        pieces = emitted.get(block["id"], [])
        if not pieces:
            missing.append(block["id"])
            continue
        if len(pieces) > len(_chunks(block["text"])):
            duplicated.append(block["id"])
        actual = "".join(text for _, text in sorted(pieces))
        if actual != block["text"]:
            finding = {"id": block["id"], "location": block["location"], "expected": block["text"], "actual": actual}
            (uncertain if block.get("uncertain") else altered).append(finding)
    known_ids = set(expected_ids)
    for block_id, pieces in emitted.items():
        if block_id not in known_ids or len(pieces) > len(_chunks(next(b["text"] for b in blocks if b["id"] == block_id))):
            if block_id not in {item.get("id") for item in duplicated}:
                duplicated.append(block_id)
    return {
        "status": "pass" if not (missing or altered or duplicated or uncertain) else "issues_found",
        "inventory_blocks": len(blocks),
        "slide_count": len(names),
        "missing": missing,
        "duplicated": duplicated,
        "altered": altered,
        "uncertain": uncertain,
    }
