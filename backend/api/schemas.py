"""Schemi Pydantic dell'API dell'agente (contratto HTTP di /chat)."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class ToolCall(BaseModel):
    tool: str
    args: Dict[str, Any]
    result: Any


class ChatResponse(BaseModel):
    session_id: str
    turn_id: str
    answer: str
    active_machine: Optional[str] = None
    confirmation_required: bool = False
    pending_action: Optional[Dict[str, Any]] = None
    gathered_data: List[ToolCall] = []
    trace: List[Dict[str, Any]] = []
    observability: Dict[str, Any] = {}
