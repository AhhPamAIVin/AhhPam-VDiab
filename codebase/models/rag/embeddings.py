"""Local embedding model for the RAG corpus and patient insights.

BAAI/bge-m3 via sentence-transformers, run in-process — no text (corpus or
patient insight, which is PHI) leaves the machine to be embedded. Chosen over
the other BGE variants because it is the multilingual one: bge-*-en-v1.5 are
English-only, bge-*-zh are Chinese, and bge-multilingual-gemma2 is a 9B model
that does not fit a CPU deployment. bge-m3 needs no query instruction prefix
(unlike bge v1.5), so documents and queries are encoded the same way.

The model is loaded once per process (get_embeddings_client is cached) and
vectors are L2-normalized, so cosine similarity in Qdrant equals the dot
product the model was trained with.
"""

from __future__ import annotations

import logging
import re
import threading
from functools import lru_cache
from typing import Any, Protocol

from codebase.backend.config import get_settings

logger = logging.getLogger("codebase.models.rag.embeddings")


class EmbeddingsClient(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


def model_slug(model_name: str) -> str:
    """'BAAI/bge-m3' -> 'bge-m3'. Used to name collections per model, so
    switching models never writes vectors of one size into another's store."""
    return re.sub(r"[^a-z0-9]+", "-", model_name.rsplit("/", 1)[-1].lower()).strip("-")


def _resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


class SentenceTransformerEmbeddings:
    def __init__(self, model_name: str, *, device: str = "auto", batch_size: int = 16, max_seq_length: int = 1024):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.batch_size = batch_size
        self.device = _resolve_device(device)
        self._model = SentenceTransformer(model_name, device=self.device)
        self._model.max_seq_length = min(max_seq_length, self._model.max_seq_length or max_seq_length)
        # SentenceTransformer.encode is not documented as thread-safe, and the
        # chat graph calls embed_query from asyncio.to_thread workers.
        self._lock = threading.Lock()
        logger.info("loaded embedding model %s on %s (dim=%s)", model_name, self.device, self.dimension)

    @property
    def dimension(self) -> int:
        # get_embedding_dimension is the sentence-transformers >=6 name.
        getter = getattr(self._model, "get_embedding_dimension", None) or self._model.get_sentence_embedding_dimension
        return int(getter())

    def _encode(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            vectors = self._model.encode(
                texts,
                batch_size=self.batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        return vectors.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._encode(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._encode([text])[0]


@lru_cache(maxsize=1)
def get_embeddings_client() -> SentenceTransformerEmbeddings:
    settings = get_settings()
    return SentenceTransformerEmbeddings(
        settings.embedding_model,
        device=settings.embedding_device,
        batch_size=settings.embedding_batch_size,
        max_seq_length=settings.embedding_max_seq_length,
    )


def warm_up() -> None:
    """Load the model and run one tiny encode, so the first real request does
    not pay the multi-second model load. Called from app startup."""
    get_embeddings_client().embed_query("khởi động")


@lru_cache(maxsize=4)
def get_tokenizer(model_name: str) -> Any | None:
    """The embedding model's own tokenizer (bge-m3: XLM-RoBERTa
    SentencePiece), so chunk budgets are measured in the tokens the model
    actually sees. None if transformers / the tokenizer files are unavailable
    — callers fall back to a character heuristic."""
    try:
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(model_name)
    except Exception as exc:  # no transformers, no cache and no network, ...
        logger.warning("tokenizer for %s unavailable (%s); using character estimate", model_name, exc)
        return None
