"""Regression: nested files sharing names, QA 2026-10-13."""
import io
import unittest
import zipfile
import fitz
from services.zip_extractor import ZipExtractor


def pdf_bytes(text):
    with fitz.open() as doc:
        doc.new_page(width=150, height=150).insert_text((20, 30), text)
        return doc.tobytes()


class ZipIdentityTest(unittest.TestCase):
    def test_nested_same_names_have_distinct_sources_and_images(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("a/trips.pdf", pdf_bytes("FIRST"))
            archive.writestr("b/trips.pdf", pdf_bytes("SECOND"))
        extractor = ZipExtractor()
        try:
            files = extractor.extract_zip(payload.getvalue())
            self.assertEqual(len(files), 2)
            self.assertEqual(len({f["filename"] for f in files}), 2)
            self.assertNotEqual(files[0]["images"], files[1]["images"])
            self.assertIn("FIRST", files[0]["page_texts"][0])
            self.assertIn("SECOND", files[1]["page_texts"][0])
        finally:
            extractor.cleanup()

    def test_render_does_not_overwrite_uploaded_image(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("receipt.pdf", pdf_bytes("INVOICE"))
            archive.writestr("receipt_page1.png", b"original image")
        extractor = ZipExtractor()
        try:
            files = extractor.extract_zip(payload.getvalue())
            image = next(f for f in files if f["type"] == "image")
            with open(image["filepath"], "rb") as stream:
                self.assertEqual(stream.read(), b"original image")
        finally:
            extractor.cleanup()


if __name__ == "__main__":
    unittest.main()
