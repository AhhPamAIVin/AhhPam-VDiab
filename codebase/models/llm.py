from langchain_openai import ChatOpenAI

from codebase.backend.config import get_settings
from codebase.models.tracing import tracing_callbacks


def _node_model_overrides(raw: str) -> dict[str, str]:
    """Parse LLM_NODE_MODELS ("node=model,node=model"); malformed pairs are ignored."""
    overrides = {}
    for pair in raw.split(","):
        node, sep, model = pair.partition("=")
        if sep and node.strip() and model.strip():
            overrides[node.strip()] = model.strip()
    return overrides


def get_llm(node: str | None = None) -> ChatOpenAI:
    """`node` picks a per-node model from LLM_NODE_MODELS when one is set,
    otherwise MODEL_NAME — so a light classification step can run on a smaller
    model without changing the answer-writing nodes."""
    settings = get_settings()
    model = _node_model_overrides(settings.llm_node_models).get(node or "", settings.model_name)
    return ChatOpenAI(
        model=model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
        temperature=settings.llm_temperature,
        callbacks=tracing_callbacks(),
    )
