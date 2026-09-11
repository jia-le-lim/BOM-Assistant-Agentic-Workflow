-- Local Qwen embeddings use a separate collection from the legacy 1536-d store.
-- Existing vectors are deliberately not copied across embedding models.
CREATE TABLE public.bom_engineer_memory_qwen3_1024 (
    id uuid PRIMARY KEY,
    vector extensions.vector(1024) NOT NULL,
    payload jsonb NOT NULL,
    CONSTRAINT qwen_memory_owner CHECK (
        COALESCE(payload->>'user_id', '') <> ''
    ),
    CONSTRAINT qwen_memory_model CHECK (
        COALESCE(payload->>'embedding_model', '') = 'qwen3-embedding:0.6b'
    ),
    CONSTRAINT qwen_memory_text CHECK (
        COALESCE(jsonb_typeof(payload->'data'), '') = 'string'
    )
);

ALTER TABLE public.bom_engineer_memory_qwen3_1024 ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.bom_engineer_memory_qwen3_1024 FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.bom_engineer_memory_qwen3_1024 TO service_role;

CREATE INDEX bom_engineer_memory_qwen3_1024_user_idx
    ON public.bom_engineer_memory_qwen3_1024 ((payload->>'user_id'));
CREATE INDEX bom_engineer_memory_qwen3_1024_hnsw_idx
    ON public.bom_engineer_memory_qwen3_1024
    USING hnsw (vector extensions.vector_cosine_ops);

COMMENT ON TABLE public.bom_engineer_memory_qwen3_1024 IS
    'Server-only preference recall. Ollama qwen3-embedding:0.6b, 1024 dimensions. Re-embed into a new collection when changing models.';
