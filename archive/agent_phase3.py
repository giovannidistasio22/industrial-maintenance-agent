"""
FASE 3 - Agent
User -> LLM -> decide se chiamare un tool -> Tool result -> LLM -> ... -> Answer

Usiamo langgraph.prebuilt.create_react_agent, che implementa esattamente
il ciclo ReAct (Reason + Act): il modello riceve i tool disponibili, decide
quali chiamare (anche più di uno, in sequenza), osserva i risultati e
continua finché non è pronto a dare una risposta finale in linguaggio naturale.
"""

from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent
from tools import ALL_TOOLS

LLM_MODEL = "llama3.1"  # deve supportare tool calling (llama3.1 lo supporta)

SYSTEM_PROMPT = """Sei un assistente esperto di manutenzione industriale.
Hai accesso a tool che interrogano il CMMS (dati macchina, sensori, storico
manutenzione, work order) e la documentazione tecnica (manuali, procedure).

Quando ti viene chiesto di diagnosticare un problema su una macchina, segui
questo approccio:
1. Controlla lo stato generale della macchina (get_machine_status).
2. Controlla i dati sensori recenti per identificare anomalie o trend (get_sensor_data).
3. Controlla lo storico manutenzione per capire se il problema è ricorrente (get_maintenance_history).
4. Controlla se ci sono già work order aperti per non duplicare segnalazioni (get_open_work_orders).
5. Cerca nella documentazione tecnica le possibili cause note (search_manual).
6. Sintetizza tutto in una diagnosi chiara, citando i dati concreti raccolti
   (valori di temperatura, date, fonti documentali) a supporto delle tue conclusioni.

Crea un nuovo work order (create_work_order) solo se l'utente lo chiede
esplicitamente o conferma di volerlo dopo che gli hai proposto un'azione.
Se non hai abbastanza informazioni anche dopo aver usato i tool disponibili,
dillo chiaramente invece di inventare dati.
"""

_llm = ChatOllama(model=LLM_MODEL, temperature=0)

# create_react_agent costruisce il grafo (nodo LLM <-> nodo tool) già pronto
agent = create_react_agent(
    model=_llm,
    tools=ALL_TOOLS,
    prompt=SYSTEM_PROMPT,
)


def run_agent(user_message: str):
    """Esegue l'agente su un singolo messaggio utente e restituisce
    la risposta finale + la sequenza di tool chiamati (per trasparenza/debug).
    """
    result = agent.invoke({"messages": [("user", user_message)]})
    messages = result["messages"]

    # Ultima risposta testuale del modello
    final_answer = messages[-1].content

    # Ricostruiamo la sequenza di tool chiamati, nell'ordine
    steps = []
    for msg in messages:
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            for call in tool_calls:
                steps.append({"tool": call["name"], "args": call["args"]})

    return final_answer, steps
