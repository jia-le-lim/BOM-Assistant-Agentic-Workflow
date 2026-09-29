"""Offline safety checks; no app imports, Cloud access, or running Docker required."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from manage import Deployment, TABLES, export_cloud, filter_restore_toc, verify_manifest, write_manifest


class OperationsSafetyTests(unittest.TestCase):
    def test_modified_archive_rejected_before_database_access(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "backup.dump"
            archive.write_bytes(b"PGDMP-original")
            write_manifest(archive)
            archive.write_bytes(b"PGDMP-tampered")
            deployment = Deployment(directory)
            with patch.object(deployment, "sql") as sql:
                with self.assertRaisesRegex(RuntimeError, "checksum"):
                    deployment.restore(archive)
                sql.assert_not_called()

    def test_restore_refuses_populated_database(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "backup.dump"
            archive.write_bytes(b"PGDMP-content")
            write_manifest(archive)
            deployment = Deployment(directory)
            with patch.object(deployment, "sql", return_value="19") as sql, \
                 patch.object(deployment, "compose") as compose:
                with self.assertRaisesRegex(RuntimeError, "EMPTY"):
                    deployment.restore(archive)
                self.assertEqual(sql.call_count, 1)
                compose.assert_not_called()

    def test_failed_backup_is_not_published(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "backup.dump"
            deployment = Deployment(directory)
            with patch.object(deployment, "sql", return_value="\n".join(TABLES)), \
                 patch.object(deployment, "compose", side_effect=RuntimeError("failed")):
                with self.assertRaises(RuntimeError):
                    deployment.backup(archive)
            self.assertFalse(archive.exists())
            self.assertFalse(Path(str(archive) + ".json").exists())
            self.assertTrue(archive.with_suffix(".dump.partial").exists())

    def test_existing_backup_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "backup.dump"
            archive.write_bytes(b"keep")
            with self.assertRaisesRegex(RuntimeError, "exists"):
                Deployment(directory).backup(archive)
            self.assertEqual(archive.read_bytes(), b"keep")

    def test_empty_application_not_reported_as_successful_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            deployment = Deployment(directory)
            archive = Path(directory) / "empty.dump"
            with patch.object(deployment, "sql", return_value=""):
                with self.assertRaisesRegex(RuntimeError, "incomplete backup"):
                    deployment.backup(archive)
            self.assertFalse(archive.exists())

    def test_restore_list_keeps_objects_but_not_public_schema(self):
        toc = (b"5; 2615 2200 SCHEMA - public pg_database_owner\n"
               b"9; 0 0 COMMENT - SCHEMA public pg_database_owner\n"
               b"12; 1259 123 TABLE public batches postgres\n"
               b"13; 0 123 TABLE DATA public batches postgres\n")
        filtered = filter_restore_toc(toc)
        self.assertNotIn(b"SCHEMA", filtered)
        self.assertIn(b"TABLE public batches", filtered)
        self.assertIn(b"TABLE DATA public batches", filtered)

    def test_cloud_password_not_in_command_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.env"
            source.write_text("MIGRATION_DATABASE_URL=postgresql://postgres.ref:secret%40pass@db.example:5432/postgres?sslmode=require\n")
            archive = Path(directory) / "out.dump"
            def fake_run(args, **kwargs):
                self.assertNotIn("secret", " ".join(map(str, args)))
                self.assertEqual(kwargs["env"]["PGPASSWORD"], "secret@pass")
                kwargs["stdout"].write(b"PGDMP-valid")
            with patch("manage.run", side_effect=fake_run):
                export_cloud(source, archive)
            verify_manifest(archive)

    def test_transaction_pooler_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.env"
            source.write_text("MIGRATION_DATABASE_URL=postgresql://postgres:p@db.example:6543/postgres")
            with patch("manage.run") as runner:
                with self.assertRaisesRegex(RuntimeError, "session pooler"):
                    export_cloud(source, Path(directory) / "out.dump")
                runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
