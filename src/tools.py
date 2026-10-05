"""
FASE 3 - Tool definitions
Ogni tool è una funzione Python decorata con @tool: il docstring e le
annotazioni di tipo sono ciò che l'LLM legge per decidere QUANDO e COME
chiamare il tool. Descrizioni chiare = scelte migliori da parte del modello.
"""

from langchain_core.tools import tool
import mock_cmms
from rag import RagPipeline

# Il retriever RAG viene creato una volta sola e riusato dal tool search_manual
_rag_pipeline = RagPipeline()


@tool
def get_machine_status(machine_id: str) -> dict:
    """Ottieni lo stato attuale e l'anagrafica di una macchina dal CMMS
    (tipo macchina, location, stato operativo, produttore, data installazione).
    Usa questo tool come primo passo quando ti chiedono di una macchina specifica,
    per capire di cosa si tratta.

    Args:
        machine_id: identificativo della macchina, es. "P-102" o "C-201".
    """
    return mock_cmms.get_machine_status(machine_id)


@tool
def get_sensor_data(machine_id: str) -> dict:
    """Ottieni le ultime letture dei sensori della macchina (temperature,
    vibrazioni, portata/pressione a seconda del tipo di macchina), in ordine
    cronologico. Usa questo tool quando devi valutare condizioni operative
    attuali o un trend recente (es. temperatura in aumento).

    Args:
        machine_id: identificativo della macchina, es. "P-102" o "C-201".
    """
    return mock_cmms.get_sensor_data(machine_id)


@tool
def get_maintenance_history(machine_id: str) -> dict:
    """Ottieni lo storico degli interventi di manutenzione già effettuati
    sulla macchina (date, descrizione intervento, tecnico). Utile per capire
    se un problema è ricorrente o se un componente è stato sostituito di recente.

    Args:
        machine_id: identificativo della macchina, es. "P-102" o "C-201".
    """
    return mock_cmms.get_maintenance_history(machine_id)


@tool
def get_open_work_orders(machine_id: str) -> dict:
    """Ottieni i work order (ordini di lavoro) attualmente aperti per la
    macchina, per verificare se un problema è già stato segnalato o è in
    carico a qualcuno prima di duplicarlo.

    Args:
        machine_id: identificativo della macchina, es. "P-102" o "C-201".
    """
    return mock_cmms.get_open_work_orders(machine_id)


@tool
def create_work_order(machine_id: str, description: str) -> dict:
    """Crea un nuovo work order (ordine di lavoro) nel CMMS per una macchina.
    Usa questo tool SOLO quando l'utente chiede esplicitamente di aprire/creare
    un intervento, o quando hai diagnosticato un problema che richiede
    manutenzione e l'utente ha confermato di volerlo segnalare. Non crearlo
    automaticamente solo per aver risposto a una domanda diagnostica.

    Args:
        machine_id: identificativo della macchina, es. "P-102".
        description: descrizione sintetica ma chiara del problema/intervento richiesto.
    """
    return mock_cmms.create_work_order(machine_id, description)


@tool
def search_manual(query: str) -> dict:
    """Cerca informazioni nei manuali tecnici e nelle procedure aziendali
    (manuali pompe/compressori, procedure di manutenzione, manuale di
    sicurezza, guida al troubleshooting). Usa questo tool per capire le
    possibili cause note di un problema, le soglie di allarme, o le
    procedure da seguire.

    Args:
        query: la domanda o i termini da cercare nella documentazione,
            es. "P-102 vibration cause" o "cooling water pressure threshold".
    """
    answer, sources = _rag_pipeline.answer(query)
    return {"answer": answer, "sources": sources}


ALL_TOOLS = [
    get_machine_status,
    get_sensor_data,
    get_maintenance_history,
    get_open_work_orders,
    create_work_order,
    search_manual,
]
