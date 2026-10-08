"""
Registro dei tool dell'agente.

Ogni tool è una chiamata all'API REST del CMMS (via cmms_client): l'agente
NON conosce il database, parla solo con l'API (Agent -> Tool -> CMMS API
-> PostgreSQL). search_manual usa la pipeline RAG (Chroma + Ollama).
TOOL_DESCRIPTIONS è il testo che i nodi inseriscono nei prompt perché l'LLM
sappia cosa ha a disposizione.
"""

from backend.rag.pipeline import RagPipeline
from backend.services import cmms_client

_rag_pipeline = RagPipeline()


def _search_manual(query: str) -> dict:
    answer, sources = _rag_pipeline.answer(query)
    return {"answer": answer, "sources": sources}


TOOL_REGISTRY = {
    "get_machine_status": cmms_client.get_machine_status,
    "get_sensor_data": cmms_client.get_sensor_data,
    "get_maintenance_history": cmms_client.get_maintenance_history,
    "get_open_work_orders": cmms_client.get_open_work_orders,
    "search_manual": _search_manual,
}

TOOL_DESCRIPTIONS = """\
- get_machine_status(machine_id): stato generale e anagrafica della macchina (dal CMMS).
- get_sensor_data(machine_id): ultime letture sensori (temperature, vibrazioni, portata...) dal CMMS.
- get_maintenance_history(machine_id): storico interventi di manutenzione (con tecnico) dal CMMS.
- get_open_work_orders(machine_id): work order attivi (aperti o in corso) per la macchina, dal CMMS.
- search_manual(query): cerca cause note, soglie di allarme e procedure nei manuali tecnici.
"""
