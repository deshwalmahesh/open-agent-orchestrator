"""LLM provider catalogue. Add a provider here + a clause in app.llm.build_chat_model."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.db.models import UserDB
from app.users import current_active_user

router = APIRouter(prefix="/providers", tags=["providers"])


_PROVIDERS = [
    {"id": "openai", "label": "OpenAI"},
    {"id": "anthropic", "label": "Anthropic"},
    {"id": "google", "label": "Google (Gemini)"},
    {"id": "vllm", "label": "vLLM (OpenAI-compatible)"},
]


@router.get("")
async def list_providers(
    _user: Annotated[UserDB, Depends(current_active_user)],
) -> list[dict]:
    return _PROVIDERS
