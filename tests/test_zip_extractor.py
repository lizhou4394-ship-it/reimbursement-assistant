import io
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.zip_extractor import ZipExtractor


class ZipExtractorSecurityTest(unittest.TestCase):
    def test_rejects_path_traversal_member(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("../outside.txt", "blocked")

        extractor = ZipExtractor()
        with self.assertRaises(ValueError):
            extractor.extract_zip(payload.getvalue())
        extractor.cleanup()


if __name__ == "__main__":
    unittest.main()
