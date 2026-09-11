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
    monkeypatch.setenv("MEM0_VECTOR_STORE", "pgvector")
    monkeypatch.delenv("MEM0_COLLECTION_NAME", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_API_KEY", "ollama")
    monkeypatch.setenv("LLM_MODEL", "qwen3.8:latest")
    monkeypatch.setenv("MEM0_LLM_MODEL", "# defaults to LLM_MODEL")
    monkeypatch.setenv("MEM0_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    monkeypatch.setenv("MEM0_EMBEDDING_DIMS", "1024")
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
    assert captured["embedder"]["config"] == {
        "model": "qwen3-embedding:0.6b", "embedding_dims": 1024,
        "api_key": "ollama", "openai_base_url": "http://localhost:11434/v1",
    }
    assert captured["vector_store"]["config"]["embedding_model_dims"] == 1024
    assert captured["llm"]["config"]["model"] == "qwen3.8:latest"

    memory._runtime_dir.cleanup()
    monkeypatch.setattr(memory, "_runtime_dir", None)


def test_https_memory_uses_existing_server_credentials_without_dsn(monkeypatch):
    from app import memory, memory_rest

    captured = {}

    class FakeMemory:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("MEM0_ENABLED", "1")
    monkeypatch.setenv("MEM0_VECTOR_STORE", "supabase_rest")
    monkeypatch.setenv("MEM0_COLLECTION_NAME", "bom_engineer_memory_qwen3_1024")
    monkeypatch.setenv("MEM0_DATABASE_URL", "# defaults to DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_API_KEY", "ollama")
    monkeypatch.setenv("LLM_TIMEOUT_S", "180")
    monkeypatch.setenv("MEM0_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    monkeypatch.setenv("MEM0_EMBEDDING_DIMS", "1024")
    monkeypatch.setattr(memory_rest, "SupabaseRestMemory", FakeMemory)
    monkeypatch.setattr(memory, "_client", None)
    monkeypatch.setattr(memory, "_unavailable", False)
    monkeypatch.setattr(memory, "_runtime_dir", None)
    # HTTPS recall should not import the optional mem0 OSS package.
    monkeypatch.setitem(sys.modules, "mem0", None)

    assert memory._get_client() is not None
    assert memory._runtime_dir is None
    assert captured == {
        "base_url": "http://localhost:11434/v1", "api_key": "ollama",
        "model": "qwen3-embedding:0.6b", "dimensions": 1024,
        "collection": "bom_engineer_memory_qwen3_1024", "timeout_s": 180.0,
    }
