"""Workaround for a ragas / langchain-community packaging mismatch.

`ragas.llms.base` unconditionally does
`from langchain_community.chat_models.vertexai import ChatVertexAI` at import time (used only
as one entry in a static isinstance-style list, never instantiated). This project's installed
langchain-community (>=0.4, "sunset" per its own deprecation notice) has dropped that submodule
entirely — Vertex AI chat support moved to the standalone `langchain-google-vertexai` package,
which this project never installs or uses (OpenAI only, see codebase/models/llm.py). Without
this shim, `import ragas` raises ModuleNotFoundError before any of our code runs.

Must be called before the first `import ragas` (or anything importing it) anywhere in the
process — retrieval_recall.py calls this at module load, ahead of its own ragas import.
"""

from __future__ import annotations

import sys
import types


def patch_ragas_vertexai_import() -> None:
    module_name = "langchain_community.chat_models.vertexai"
    if module_name in sys.modules:
        return

    stub = types.ModuleType(module_name)

    class ChatVertexAI:  # never instantiated — see module docstring
        pass

    stub.ChatVertexAI = ChatVertexAI
    sys.modules[module_name] = stub
