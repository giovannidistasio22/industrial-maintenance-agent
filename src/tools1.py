"""
FASE 4 - Tool registry
A differenza della Fase 3 (dove i tool erano legati all'LLM via bind_tools),
qui i nodi del grafo chiamano i tool direttamente come funzioni Python,
in base al nome deciso dai nodi di analisi/valutazione. TOOL_DESCRIPTIONS
è il testo che inseriamo nel prompt perché l'LLM sappia cosa ha a disposizione.
"""

import mock_cmms
from rag import RagPipeline

_rag_pipeline = RagPipeline()


def _search_manual(query: str) -> dict:
    answer, sources = _rag_pipeline.answer(query)
    return {"answer": answer, "sources": sources}


TOOL_REGISTRY = {
    "get_machine_status": mock_cmms.get_machine_status,
    "get_sensor_data": mock_cmms.get_sensor_data,
    "get_maintenance_history": mock_cmms.get_maintenance_history,
    "get_open_work_orders": mock_cmms.get_open_work_orders,
    "search_manual": _search_manual,
}

TOOL_DESCRIPTIONS = """\
- get_machine_status(machine_id): stato generale e anagrafica della macchina.
- get_sensor_data(machine_id): ultime letture sensori (temperature, vibrazioni, portata...).
- get_maintenance_history(machine_id): storico interventi di manutenzione già effettuati.
- get_open_work_orders(machine_id): work order attualmente aperti per la macchina.
- search_manual(query): cerca cause note, soglie di allarme e procedure nei manuali tecnici.
"""
