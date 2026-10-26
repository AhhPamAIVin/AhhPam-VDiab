"""codebase/models/rag/embeddings.py.

The fast tests cover the pure helpers. The `slow` test loads the real
BAAI/bge-m3 model (from the local Hugging Face cache — it is skipped, not
downloaded, when the model isn't cached) and checks that it ranks Vietnamese
paraphrases above unrelated text.
"""

from __future__ import annotations

import pytest

from codebase.models.rag.embeddings import model_slug


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("BAAI/bge-m3", "bge-m3"),
        ("AITeamVN/Vietnamese_Embedding", "vietnamese-embedding"),
        ("text-embedding-3-small", "text-embedding-3-small"),
    ],
)
def test_model_slug(name, slug):
    assert model_slug(name) == slug


def _cached_model_or_skip(model_name: str) -> None:
    pytest.importorskip("sentence_transformers")
    from huggingface_hub import try_to_load_from_cache

    if not isinstance(try_to_load_from_cache(model_name, "config.json"), str):
        pytest.skip(f"{model_name} not in the local Hugging Face cache")


@pytest.mark.slow
def test_bge_m3_ranks_vietnamese_paraphrase_above_unrelated_text():
    _cached_model_or_skip("BAAI/bge-m3")
    from codebase.models.rag.embeddings import SentenceTransformerEmbeddings

    client = SentenceTransformerEmbeddings("BAAI/bge-m3", device="cpu")
    assert client.dimension == 1024

    query = client.embed_query("Người bệnh tiểu đường nên giữ HbA1c dưới mức nào?")
    related, unrelated = client.embed_documents(
        [
            "Mục tiêu điều trị cho người trưởng thành: HbA1c < 7% (53 mmol/mol).",
            "Giày dép cần vừa vặn, không có đường may thô bên trong để tránh cọ xát.",
        ]
    )

    def cos(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert abs(cos(query, query) - 1.0) < 1e-4  # vectors are normalized
    assert cos(query, related) > cos(query, unrelated) + 0.1
