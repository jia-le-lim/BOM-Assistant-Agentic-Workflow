"""Checks for transfer integrity and safe handling of malformed manifests."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from verify_package import verify

class TransferIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/"record.bin").write_bytes(b"original business record")
        self.write_manifest()

    def write_manifest(self, path="record.bin"):
        payload = b"original business record"
        (self.root/"SHA256SUMS.json").write_text(json.dumps({"files":[{
            "path":path,"bytes":len(payload),"sha256":hashlib.sha256(payload).hexdigest()
        }]}),encoding="utf-8")

    def check(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return verify(self.root)

    def test_intact_package_passes(self):
        self.assertEqual(self.check()[0],1)

    def test_same_size_changed_content_fails(self):
        (self.root/"record.bin").write_bytes(b"x"*len(b"original business record"))
        with self.assertRaises(SystemExit):
            self.check()

    def test_missing_payload_fails(self):
        (self.root/"record.bin").unlink()
        with self.assertRaises(SystemExit):
            self.check()

    def test_unexpected_file_fails(self):
        (self.root/"unreviewed.env").write_text("placeholder")
        with self.assertRaises(SystemExit):
            self.check()

    def test_parent_traversal_is_rejected(self):
        self.write_manifest("../outside.bin")
        with self.assertRaises(ValueError):
            self.check()

if __name__ == "__main__":
    unittest.main()

