import sys
from pathlib import Path
from types import SimpleNamespace


def test_mem0_uses_supabase_vector_store_without_persistent_history(monkeypatch):
    from app import memory

    captured = {}

    class FakeMemory:
        @staticmethod
        def from_config(config):
            captured.update(config)
            return object()

    monkeypatch.setenv("MEM0_ENABLED", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/postgres")
    monkeypatch.delenv("MEM0_DATABASE_URL", raising=False)
    monkeypatch.delenv("MEM0_DIR", raising=False)
    monkeypatch.setitem(sys.modules, "mem0", SimpleNamespace(Memory=FakeMemory))
    monkeypatch.setattr(memory, "_client", None)
    monkeypatch.setattr(memory, "_unavailable", False)
    monkeypatch.setattr(memory, "_runtime_dir", None)

    assert memory._get_client() is not None
    assert captured["history_db_path"] == ":memory:"
    assert captured["vector_store"]["provider"] == "pgvector"
    assert captured["vector_store"]["config"]["connection_string"] == (
        "postgresql://example.invalid/postgres"
    )
    assert captured["vector_store"]["config"]["collection_name"] == (
        "bom_engineer_memory"
    )
    assert Path(memory._runtime_dir.name).is_dir()

    memory._runtime_dir.cleanup()
    monkeypatch.setattr(memory, "_runtime_dir", None)
