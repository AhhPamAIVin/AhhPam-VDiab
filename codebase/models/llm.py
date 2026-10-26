from langchain_openai import ChatOpenAI

from codebase.backend.config import get_settings
from codebase.models.tracing import tracing_callbacks


def get_llm() -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        model=settings.model_name,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
        temperature=settings.llm_temperature,
        callbacks=tracing_callbacks(),
    )
