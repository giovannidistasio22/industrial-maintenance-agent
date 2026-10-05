"""
FASE 6 - Stato del grafo (invariato rispetto alla Fase 5)

Campi "persistenti" tra un turno e l'altro della stessa conversazione
(grazie al checkpointer di LangGraph, indicizzato per thread_id/session_id):
  - messages:        cronologia completa Tecnico/Assistente (reducer add_messages = append)
  - active_machine:  ultima macchina di cui si stava parlando

Campi "per-turno", che vengono azzerati ad ogni nuova domanda in run_graph():
  - query, standalone_query, gathered_data, next_tool, next_tool_args,
    sufficient, iterations, trace, final_answer
"""

from typing import TypedDict, List, Dict, Any, Optional, Annotated
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    # --- Memoria (persiste tra i turni) ---
    messages: Annotated[list, add_messages]
    active_machine: Optional[str]

    # --- Turno corrente ---
    query: str               # domanda così come scritta dall'utente
    standalone_query: str    # domanda riscritta in forma autonoma usando la cronologia
    gathered_data: List[Dict[str, Any]]
    next_tool: Optional[str]
    next_tool_args: Optional[Dict[str, str]]
    sufficient: bool
    iterations: int
    trace: List[Dict[str, Any]]
    final_answer: str

    # --- FASE 7: azione di scrittura proposta, in attesa di conferma umana ---
    # Presente SOLO tra il momento in cui viene proposta (propose_write_action)
    # e quello in cui viene risolta (handle_confirmation), che la azzera di nuovo.
    pending_action: Optional[Dict[str, Any]]
    confirmed: bool
