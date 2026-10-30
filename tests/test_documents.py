import io
import unittest
import zipfile

from local_builder.documents import extract_document


class DocumentExtractionTests(unittest.TestCase):
    def test_text_inventory_has_stable_locations_and_ids(self):
        first = extract_document("notes.txt", b"Risk Controls\n- Keep every record\n\nReview dates.")
        second = extract_document("notes.txt", b"Risk Controls\n- Keep every record\n\nReview dates.")
        self.assertEqual(first["blocks"], second["blocks"])
        self.assertEqual(["line:1", "line:2", "line:4"], [b["location"] for b in first["blocks"]])
        self.assertEqual(["heading", "bullet", "paragraph"], [b["type"] for b in first["blocks"]])

    def test_docx_preserves_paragraphs_and_table_rows(self):
        document_xml = (
            b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            b'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Risk Controls</w:t></w:r></w:p>'
            b'<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Owner</w:t></w:r></w:p></w:tc>'
            b'<w:tc><w:p><w:r><w:t>Review date</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            b'<w:sectPr/></w:body></w:document>'
        )
        with io.BytesIO() as buffer:
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("word/document.xml", document_xml)
            result = extract_document("source.docx", buffer.getvalue())
        self.assertEqual(["heading", "table_row"], [b["type"] for b in result["blocks"]])
        self.assertEqual("Owner | Review date", result["blocks"][1]["text"])
        self.assertEqual("table:1:row:1", result["blocks"][1]["location"])

    def test_pdf_extracts_text_with_page_location(self):
        source = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Contents 4 0 R >> endobj
4 0 obj << /Length 44 >> stream
BT /F1 12 Tf (Original wording stays.) Tj ET
endstream endobj
trailer << /Root 1 0 R >>
%%EOF"""
        result = extract_document("source.pdf", source)
        self.assertEqual("Original wording stays.", result["blocks"][0]["text"])
        self.assertEqual("page:1:1", result["blocks"][0]["location"])

    def test_pptx_extracts_text_and_slide_locations(self):
        slide_one = b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:sp><p:txBody><a:p><a:r><a:t>First title</a:t></a:r></a:p></p:txBody></p:sp></p:sld>'
        slide_two = b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:sp><p:txBody><a:p><a:r><a:t>Second page text.</a:t></a:r></a:p></p:txBody></p:sp></p:sld>'
        with io.BytesIO() as buffer:
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("ppt/slides/slide1.xml", slide_one)
                archive.writestr("ppt/slides/slide2.xml", slide_two)
            result = extract_document("source.pptx", buffer.getvalue())
        self.assertEqual(["slide:1:paragraph:1", "slide:2:paragraph:1"], [b["location"] for b in result["blocks"]])
        self.assertEqual(["First title", "Second page text."], [b["text"] for b in result["blocks"]])

    def test_unsupported_format_is_rejected(self):
        with self.assertRaises(ValueError):
            extract_document("archive.zip", b"data")


if __name__ == "__main__":
    unittest.main()