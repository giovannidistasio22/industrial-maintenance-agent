"""
Grafo LangGraph dell'agente (con Human in the Loop)

                 START
                   |
                   v
             resolve_context
                   |
                   v
             analyze_query
                   |
             need info? ------NO------+
                   |                  |
                  YES                 |
                   v                  |
               call_tool              |
                   v                  |
             evaluate_data            |
                   |                  |
        sufficient? --NO--> call_tool (loop)
                   |
                  YES
                   v
           generate_answer                    (READ — autonomo)
                   |
                   v
         propose_write_action
                   |
        serve conferma? --NO--> END
                   |
                  YES
                   v
        request_confirmation   <-- interrupt(): il grafo si FERMA qui
                   |
                   v (resume, in una chiamata SUCCESSIVA)
        handle_confirmation                     (WRITE — solo dopo consenso)
                   |
                  END
"""

import os
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from backend.agent.state import AgentState
from backend.agent.nodes import (
    resolve_context,
    analyze_query,
    call_tool,
    evaluate_data,
    generate_answer,
    propose_write_action,
    request_confirmation,
    handle_confirmation,
    route_after_analyze,
    route_after_evaluate,
    route_after_propose,
)


def _build_checkpointer():
    # Il checkpointer è OBBLIGATORIO per l'human-in-the-loop: interrupt() sospende
    # l'esecuzione e langgraph deve poter salvare/ricaricare lo stato a metà grafo.
    db_path = os.getenv("CHECKPOINT_DB")
    if db_path:
        try:
            import sqlite3
            from langgraph.checkpoint.sqlite import SqliteSaver

            conn = sqlite3.connect(db_path, check_same_thread=False)
            print(f"[memory] Checkpointer SQLite attivo: {db_path}")
            return SqliteSaver(conn)
        except ImportError:
            print("[memory] langgraph-checkpoint-sqlite non installato: uso MemorySaver in RAM.")
    print("[memory] Checkpointer MemorySaver (RAM): la memoria (e le conferme pendenti) si perdono al riavvio.")
    return MemorySaver()


checkpointer = _build_checkpointer()

_builder = StateGraph(AgentState)

_builder.add_node("resolve_context", resolve_context)
_builder.add_node("analyze_query", analyze_query)
_builder.add_node("call_tool", call_tool)
_builder.add_node("evaluate_data", evaluate_data)
_builder.add_node("generate_answer", generate_answer)
_builder.add_node("propose_write_action", propose_write_action)
_builder.add_node("request_confirmation", request_confirmation)
_builder.add_node("handle_confirmation", handle_confirmation)

_builder.add_edge(START, "resolve_context")
_builder.add_edge("resolve_context", "analyze_query")

_builder.add_conditional_edges(
    "analyze_query", route_after_analyze,
    {"call_tool": "call_tool", "generate_answer": "generate_answer"},
)
_builder.add_edge("call_tool", "evaluate_data")
_builder.add_conditional_edges(
    "evaluate_data", route_after_evaluate,
    {"call_tool": "call_tool", "generate_answer": "generate_answer"},
)

_builder.add_edge("generate_answer", "propose_write_action")
_builder.add_conditional_edges(
    "propose_write_action", route_after_propose,
    {"request_confirmation": "request_confirmation", "END": END},
)
_builder.add_edge("request_confirmation", "handle_confirmation")
_builder.add_edge("handle_confirmation", END)

graph = _builder.compile(checkpointer=checkpointer)


def _config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def run_graph(query: str, session_id: str) -> dict:
    """Avvia un turno NORMALE (nessuna conferma pendente). Se in questo turno
    l'agente propone un'azione di scrittura, il grafo si ferma da solo a
    request_confirmation: lo stato restituito conterrà '__interrupt__'."""
    turn_input = {
        "messages": [HumanMessage(content=query)],
        "query": query,
        "standalone_query": query,
        "gathered_data": [],
        "next_tool": None,
        "next_tool_args": None,
        "sufficient": False,
        "iterations": 0,
        "trace": [],
        "final_answer": "",
        "confirmed": False,
        # NB: pending_action NON viene azzerato qui apposta — se ce n'è già uno
        # pendente da un turno precedente, questa funzione non va chiamata
        # direttamente: vedi get_pending_action() / resume_confirmation() sotto.
    }
    return graph.invoke(turn_input, config=_config(session_id))


def get_pending_action(session_id: str) -> dict | None:
    """Restituisce l'azione in attesa di conferma per questa sessione, se c'è
    (cioè se il grafo è fermo su request_confirmation), altrimenti None."""
    snapshot = graph.get_state(_config(session_id))
    if snapshot.next and snapshot.tasks:
        for task in snapshot.tasks:
            if task.interrupts:
                return task.interrupts[0].value
    return None


def resume_confirmation(confirmed: bool, session_id: str, user_message: str) -> dict:
    """Riprende un'esecuzione sospesa a request_confirmation con un sì/no già
    interpretato, aggiungendo anche il messaggio dell'utente alla cronologia."""
    return graph.invoke(
        Command(resume=confirmed, update={"messages": [HumanMessage(content=user_message)]}),
        config=_config(session_id),
    )


def cancel_pending_confirmation(session_id: str) -> None:
    """Chiude in sicurezza una conferma pendente SENZA eseguire l'azione
    (equivale a un resume con confirmed=False), tipicamente perché l'utente ha
    cambiato argomento invece di rispondere sì/no. Fail-safe: in caso di dubbio
    non si esegue mai l'azione di scrittura."""
    graph.invoke(Command(resume=False), config=_config(session_id))


def get_history(session_id: str) -> list:
    snapshot = graph.get_state(_config(session_id))
    messages = (snapshot.values or {}).get("messages", [])
    return [
        {"role": "user" if m.type == "human" else "assistant", "content": m.content}
        for m in messages
    ]


def get_active_machine(session_id: str):
    snapshot = graph.get_state(_config(session_id))
    return (snapshot.values or {}).get("active_machine")


def reset_session(session_id: str) -> bool:
    if hasattr(checkpointer, "delete_thread"):
        checkpointer.delete_thread(session_id)
        return True
    return False
