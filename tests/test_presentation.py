import unittest
import zipfile
import struct
import zlib
import io
from xml.etree import ElementTree as ET

from local_builder.brand import detect_png_colors
from local_builder.presentation import _shape_height_inches, build_blank_template, build_presentation, compare_presentation, plan_slides


def block(identity, text, kind="paragraph"):
    return {"id": identity, "source": "source.txt", "location": "line:1", "type": kind, "text": text}


def tiny_png(color=(20, 80, 60)):
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    pixel = bytes(color)
    image = b"\x00" + pixel + pixel + b"\x00" + pixel + pixel
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(image)) + chunk(b"IEND", b"")


class PresentationTests(unittest.TestCase):
    def test_slide_plan_splits_long_content_without_loss(self):
        source = block("long-id", "important words " * 100)
        plan = plan_slides([source])
        self.assertGreater(len(plan), 1)
        self.assertEqual(source["text"], "".join(item["text"] for slide in plan for item in slide))

    def test_planned_shapes_fit_inside_each_slide(self):
        source = [block(f"00000000-0000-0000-0000-{index:012d}", "Short source text.") for index in range(120)]
        plan = plan_slides(source)
        self.assertGreater(len(plan), 1)
        for slide in plan:
            self.assertLessEqual(sum(_shape_height_inches(item) + 0.03 for item in slide), 5.45)

    def test_generated_presentation_is_editable_and_reconciles(self):
        blocks = [block("00000000-0000-0000-0000-000000000001", "AML Controls", "heading"),
              block("00000000-0000-0000-0000-000000000002", "Retain all source wording.")]
        output = build_presentation(blocks, {"company": "FLO LIZ NIGERIA LTD", "primary_color": "#123B37"})
        report = compare_presentation(blocks, output)
        with zipfile.ZipFile(__import__("io").BytesIO(output)) as archive:
            slides = [name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
            xml = archive.read(slides[0]).decode("utf-8")
            for name in archive.namelist():
                if name.endswith((".xml", ".rels")) or name == "[Content_Types].xml":
                    ET.fromstring(archive.read(name))
        self.assertEqual("pass", report["status"])
        self.assertEqual(1, report["slide_count"])
        self.assertIn("Retain all source wording.", xml)
        self.assertIn("FLO LIZ NIGERIA LTD", xml)

    def test_inventory_compare_detects_removed_and_altered_shapes(self):
        blocks = [block("00000000-0000-0000-0000-000000000001", "First sentence."),
              block("00000000-0000-0000-0000-000000000002", "Second sentence.")]
        output = build_presentation(blocks)
        with zipfile.ZipFile(__import__("io").BytesIO(output)) as source:
            files = {name: source.read(name) for name in source.namelist()}
        slide_name = "ppt/slides/slide1.xml"
        files[slide_name] = files[slide_name].replace(b"Second sentence.", b"Altered sentence.")
        with __import__("io").BytesIO() as buffer:
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, value in files.items():
                    archive.writestr(name, value)
            report = compare_presentation(blocks, buffer.getvalue())
        self.assertEqual("issues_found", report["status"])
        self.assertEqual(["00000000-0000-0000-0000-000000000002"], [item["id"] for item in report["altered"]])

    def test_undecodable_inventory_block_is_reported_as_uncertain(self):
        source = block("00000000-0000-0000-0000-000000000004", "Bad glyph \x03 remains in inventory.")
        source["uncertain"] = True
        report = compare_presentation([source], build_presentation([source]))
        self.assertEqual("issues_found", report["status"])
        self.assertEqual([], report["altered"])
        self.assertEqual(source["location"], report["uncertain"][0]["location"])

    def test_logo_color_is_suggestion_and_logo_is_embedded(self):
        logo = tiny_png()
        candidates = detect_png_colors(logo)
        self.assertTrue(candidates)
        self.assertEqual("#185838", candidates[0]["hex"])
        output = build_presentation([block("00000000-0000-0000-0000-000000000003", "Logo test.")], logo=logo, logo_filename="mark.png")
        with zipfile.ZipFile(io.BytesIO(output)) as archive:
            self.assertEqual(logo, archive.read("ppt/media/brand.png"))

    def test_slide_photo_is_embedded_only_on_selected_slide(self):
        source = block("00000000-0000-0000-0000-000000000005", "FLOLIZ AML title and source wording " * 24)
        logo = tiny_png((192, 0, 0))
        output = build_presentation([source], slide_images={2: (logo, "staff-photo.png")})
        report = compare_presentation([source], output)
        with zipfile.ZipFile(io.BytesIO(output)) as archive:
            self.assertIn("ppt/media/slide-2-photo.png", archive.namelist())
            slide_one = archive.read("ppt/slides/slide1.xml")
            slide_two = archive.read("ppt/slides/slide2.xml")
            rels_two = archive.read("ppt/slides/_rels/slide2.xml.rels")
            theme = archive.read("ppt/theme/theme1.xml")
        self.assertNotIn(b"r:embed=\"rId3\"", slide_one)
        self.assertIn(b"r:embed=\"rId3\"", slide_two)
        self.assertIn(b"slide-2-photo.png", rels_two)
        self.assertIn(b"val=\"1A0F49\"", theme)
        self.assertIn(b"val=\"C00000\"", theme)
        self.assertIn(b"val=\"25C6E3\"", theme)
        self.assertEqual("pass", report["status"])

    def test_blank_template_has_eight_editable_placeholder_slides(self):
        brand = {"company": "FLOLIZ NIGERIA LTD", "primary_color": "#1A0F49",
                 "accent_color": "#C00000", "secondary_color": "#25C6E3"}
        output = build_blank_template(brand)
        with zipfile.ZipFile(io.BytesIO(output)) as archive:
            slides = sorted(name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml"))
            texts = [ET.fromstring(archive.read(name)).findall(".//{http://schemas.openxmlformats.org/drawingml/2006/main}t") for name in slides]
            flattened = [item.text or "" for group in texts for item in group]
            for name in archive.namelist():
                if name.endswith((".xml", ".rels")) or name == "[Content_Types].xml":
                    ET.fromstring(archive.read(name))
        self.assertEqual(8, len(slides))
        self.assertIn("Presentation title", flattened)
        self.assertIn("Closing", flattened)
        self.assertIn("OPTIONAL IMAGE", flattened)
        self.assertIn("FLOLIZ NIGERIA LTD", flattened)
        with zipfile.ZipFile(io.BytesIO(output)) as archive:
            theme = archive.read("ppt/theme/theme1.xml")
        self.assertIn(b"val=\"1A0F49\"", theme)
        self.assertIn(b"val=\"C00000\"", theme)
        self.assertIn(b"val=\"25C6E3\"", theme)


if __name__ == "__main__":
    unittest.main()