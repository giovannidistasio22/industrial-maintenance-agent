"""
Nodi del grafo dell'agente (con Human in the Loop)

    START
      |
      v
 resolve_context
      |
      v
 analyze_query ---------(no info needed)---------+
      |                                           |
 (serve info)                                     |
      v                                           |
   call_tool -> evaluate_data --(non basta)--> call_tool (loop)
                   |
             (basta / max iter)
                   v
            generate_answer          <-- diagnosi (READ, autonoma)
                   |
                   v
         propose_write_action        <-- NUOVO: propone un'azione WRITE?
                   |
         serve conferma? ---NO---+
                   |             |
                  YES            |
                   v             |
         request_confirmation    |   <-- interrupt(): il grafo si FERMA qui
                   v             |   finché non arriva una conferma esplicita
         handle_confirmation     |   <-- SOLO qui può scattare create_work_order
                   |             |
                   +-------------+
                   v
                  END

Principio guida: READ è autonomo (analyze_query/call_tool/evaluate_data possono
girare da soli), WRITE richiede sempre una conferma esplicita dell'utente prima
di essere eseguita (propose_write_action non esegue nulla, prepara solo la
richiesta; solo handle_confirmation, dopo un resume con confirmed=True, chiama
davvero create_work_order).

FASE 10: il LLM è tracciato (span 'llm_call' + token automatici) e i tool
vengono eseguiti dentro uno span 'tool_call' con metriche e log degli errori.
"""

import re
import time
from typing import Optional

from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from backend.agent.state import AgentState
from backend.models.schemas import (
    AnalysisDecision, ConfirmationInterpretation, ContextResolution,
    EvaluationDecision, WriteProposal,
)
from backend.services import cmms_client
from backend.services.observability import make_tracked_llm, tracer, metrics, log_event
from backend.tools.registry import TOOL_REGISTRY, TOOL_DESCRIPTIONS

LLM_MODEL = "llama3.1"
MAX_ITERATIONS = 5
MAX_HISTORY_MESSAGES = 10  # finestra di cronologia passata ai prompt (ultimi N messaggi)

# FASE 10: LLM tracciato — ogni invocazione (testo o output strutturato) crea
# uno span 'llm_call' e i token vengono contati dal client Ollama condiviso.
_llm = make_tracked_llm(LLM_MODEL)

# Riconosce ID macchina tipo P-102, C-201, PMP-1234 (case-insensitive)
MACHINE_ID_PATTERN = re.compile(r"\b([A-Za-z]{1,3}-\d{2,4})\b")


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _extract_machine_id(text: str) -> Optional[str]:
    match = MACHINE_ID_PATTERN.search(text or "")
    return match.group(1).upper() if match else None


def _format_history(messages: list, limit: int = MAX_HISTORY_MESSAGES) -> str:
    """Formatta gli ultimi `limit` messaggi come testo leggibile per i prompt."""
    lines = []
    for m in messages[-limit:]:
        role = "Tecnico" if m.type == "human" else "Assistente"
        lines.append(f"{role}: {m.content}")
    return "\n".join(lines)


def _already_succeeded(gathered: list, tool_name: str, tool_args: dict) -> bool:
    """True se questo tool con questi argomenti è già stato chiamato con successo
    (evita di rifare la stessa identica chiamata più volte nello stesso turno)."""
    return any(
        d["tool"] == tool_name and d["args"] == tool_args and "error" not in (d.get("result") or {})
        for d in gathered
    )


def _autofill_args(tool_name: str, tool_args: dict, machine: Optional[str], query: str) -> dict:
    """Completa gli argomenti mancanti usando il contesto noto (macchina attiva /
    domanda corrente), invece di far fallire il tool con un TypeError quando il
    modello ha scelto il tool giusto ma ha lasciato gli argomenti vuoti."""
    tool_args = dict(tool_args or {})
    if tool_name == "search_manual":
        tool_args.setdefault("query", query)
    elif tool_name in TOOL_REGISTRY and machine:
        tool_args.setdefault("machine_id", machine)
    return tool_args


def _fallback_tool(machine: Optional[str], query: str, reasoning: str = "", gathered: Optional[list] = None) -> tuple[str, dict]:
    """Sceglie un tool quando il modello dice 'servono dati' (o 'non bastano') ma
    non ha compilato correttamente il campo strutturato. Prova prima a capire dal
    `reasoning` (testo libero) quale tool il modello intendeva davvero — capita
    spesso che lo nomini a parole invece di compilare il campo dedicato — e salta
    i tool già chiamati con successo con gli stessi argomenti, per non sprecare
    iterazioni ripetendo la stessa identica chiamata."""
    gathered = gathered or []
    reasoning = reasoning or ""

    # 1) Il modello nomina un tool specifico nel reasoning? Usa quello.
    mentioned = [name for name in TOOL_REGISTRY if name in reasoning]

    # 2) Altrimenti, ordine di default ragionevole (prima i dati macchina, poi i manuali)
    default_order = ["get_sensor_data", "get_maintenance_history", "get_open_work_orders",
                     "get_machine_status", "search_manual"]

    for name in mentioned + default_order:
        args = _autofill_args(name, {}, machine, query)
        if name != "search_manual" and not machine:
            continue  # non ha senso interrogare il CMMS senza sapere di quale macchina
        if not _already_succeeded(gathered, name, args):
            return name, args

    # 3) Tutto già provato: ripiego finale sui manuali
    return "search_manual", {"query": query}


# ---------------------------------------------------------------------------
# Nodo 0 - Resolve Context (NUOVO)
# ---------------------------------------------------------------------------

def resolve_context(state: AgentState) -> dict:
    """Trasforma la domanda dell'utente in una domanda autonoma e aggiorna la
    macchina attiva, usando la cronologia della conversazione.

    Esempio:
      Turno 1: "P-102 ha problemi di temperatura."      -> active_machine = P-102
      Turno 2: "E per quanto riguarda le vibrazioni?"   -> standalone_query =
               "Com'è la situazione delle vibrazioni di P-102?", active_machine = P-102
    """
    query = state["query"]
    previous_machine = state.get("active_machine")
    # L'ultimo messaggio è la domanda corrente: la cronologia utile è tutto il resto
    history_messages = state.get("messages", [])[:-1]

    # Se l'utente nomina esplicitamente una macchina, quella vince sempre
    explicit_machine = _extract_machine_id(query)

    # Prima domanda della conversazione: niente da risolvere
    if not history_messages:
        machine = explicit_machine
        trace_entry = {
            "node": "resolve_context",
            "note": "Primo turno della conversazione, nessuna cronologia da usare.",
            "active_machine": machine,
        }
        return {
            "standalone_query": query,
            "active_machine": machine,
            "trace": state.get("trace", []) + [trace_entry],
        }

    prompt = f"""Sei un assistente di manutenzione industriale. Stai riscrivendo l'ultima
domanda di un tecnico in forma autonoma, usando la cronologia della conversazione.

Macchina di cui si stava parlando finora: {previous_machine or "nessuna"}

Cronologia:
{_format_history(history_messages)}

Ultima domanda del tecnico: "{query}"

Riscrivi l'ultima domanda in modo che sia comprensibile SENZA la cronologia.
Se la domanda è ellittica (es. "e le vibrazioni?", "e quando è stata fatta l'ultima
manutenzione?") e riguarda la stessa macchina della conversazione, includi
esplicitamente l'ID macchina. Se il tecnico passa a parlare di un'altra macchina,
usa quella nuova. Se la domanda è già autonoma, lasciala praticamente invariata."""

    resolution = _llm.with_structured_output(ContextResolution).invoke(prompt)

    # Priorità: macchina esplicita nella domanda > macchina scelta dall'LLM > macchina precedente
    llm_machine = _extract_machine_id(resolution.machine_id or "")
    machine = explicit_machine or llm_machine or previous_machine

    standalone = resolution.standalone_query.strip() or query
    # Rete di sicurezza: se abbiamo una macchina ma la domanda riscritta non la cita, la aggiungiamo
    if machine and machine.lower() not in standalone.lower():
        standalone = f"{standalone} (macchina {machine})"

    trace_entry = {
        "node": "resolve_context",
        "original_query": query,
        "standalone_query": standalone,
        "active_machine": machine,
    }

    return {
        "standalone_query": standalone,
        "active_machine": machine,
        "trace": state.get("trace", []) + [trace_entry],
    }


# ---------------------------------------------------------------------------
# Nodo 1 - Analyze Query
# ---------------------------------------------------------------------------

def analyze_query(state: AgentState) -> dict:
    query = state["standalone_query"]
    machine = state.get("active_machine")

    prompt = f"""Sei un assistente di manutenzione industriale. Hai a disposizione questi tool:
{TOOL_DESCRIPTIONS}
Macchina attiva nella conversazione: {machine or "nessuna"}
Domanda dell'utente: "{query}"

Decidi se, per rispondere in modo accurato e concreto, ti servono dati reali
(dal CMMS o dai manuali) oppure se puoi rispondere subito con conoscenza generale.
Se servono dati, indica il PRIMO tool da chiamare (uno solo, il più utile per iniziare).
Se la domanda riguarda la macchina attiva, usa il suo ID negli argomenti del tool."""

    decision = _llm.with_structured_output(AnalysisDecision).invoke(prompt)

    next_tool, next_tool_args, used_fallback = None, None, False
    if decision.needs_information:
        if decision.tool_name:
            next_tool, next_tool_args = decision.tool_name, decision.tool_args
        else:
            # Il modello ha detto "servono dati" ma non ha compilato tool_name
            # (es. lo ha scritto solo a parole nel reasoning): non lasciamo che
            # la richiesta di dati "sparisca", chiamiamo un tool di ripiego.
            next_tool, next_tool_args = _fallback_tool(
                machine, query, reasoning=decision.reasoning, gathered=state.get("gathered_data", [])
            )
            used_fallback = True

    trace_entry = {
        "node": "analyze_query",
        "needs_information": decision.needs_information,
        "reasoning": decision.reasoning,
        **({"tool_name_fallback": next_tool} if used_fallback else {}),
    }

    return {
        "next_tool": next_tool,
        "next_tool_args": next_tool_args,
        "trace": state.get("trace", []) + [trace_entry],
    }


def route_after_analyze(state: AgentState) -> str:
    return "call_tool" if state.get("next_tool") else "generate_answer"


# ---------------------------------------------------------------------------
# Nodo 2 - Tool / RAG
# ---------------------------------------------------------------------------

def call_tool(state: AgentState) -> dict:
    tool_name = state.get("next_tool")
    machine = state.get("active_machine")
    query = state.get("standalone_query", "")
    gathered = state.get("gathered_data", [])

    # Completa gli argomenti mancanti dal contesto noto (es. tool_name corretto
    # ma tool_args={} perché il modello non li ha compilati: capitava spesso).
    raw_args = state.get("next_tool_args") or {}
    tool_args = _autofill_args(tool_name, raw_args, machine, query)
    autofilled = tool_args != dict(raw_args)

    # Se questa identica chiamata ha già dato un risultato valido in questo turno,
    # non ripeterla: sprecherebbe solo un'iterazione (vedi MAX_ITERATIONS).
    if _already_succeeded(gathered, tool_name, tool_args):
        trace_entry = {
            "node": "call_tool", "tool": tool_name, "args": tool_args,
            "note": "risultato già disponibile da una chiamata precedente, non ripetuto",
        }
        return {
            "iterations": state.get("iterations", 0) + 1,
            "trace": state.get("trace", []) + [trace_entry],
        }

    # FASE 10: span 'tool_call' + metriche: quale tool, con quali argomenti,
    # quanto tempo, e se il risultato è un errore (il CMMS restituisce
    # {"error": ...} invece di sollevare: qui lo marichiamo sullo span).
    with tracer.span("tool_call", {"tool": tool_name, "args": tool_args}) as span:
        t0 = time.perf_counter()
        fn = TOOL_REGISTRY.get(tool_name)
        if fn is None:
            result = {"error": f"Tool '{tool_name}' non esiste nel registry."}
        else:
            try:
                result = fn(**tool_args)
            except TypeError as e:
                result = {"error": f"Argomenti non validi per '{tool_name}': {e}"}
        duration_ms = (time.perf_counter() - t0) * 1000
        has_error = isinstance(result, dict) and "error" in result
        if span is not None and has_error:
            span.set_error(str(result["error"]))
        metrics.record_tool(tool_name, duration_ms, error=has_error)
        log_event("tool_call", tool=tool_name, args=tool_args, duration_ms=round(duration_ms, 1),
                  error=has_error, error_message=result.get("error") if has_error else None)

    gathered = gathered + [{"tool": tool_name, "args": tool_args, "result": result}]

    trace_entry = {"node": "call_tool", "tool": tool_name, "args": tool_args}
    if autofilled:
        trace_entry["args_autofilled"] = True
    if isinstance(result, dict) and "error" in result:
        trace_entry["error"] = result["error"]

    return {
        "gathered_data": gathered,
        "iterations": state.get("iterations", 0) + 1,
        "trace": state.get("trace", []) + [trace_entry],
    }


# ---------------------------------------------------------------------------
# Nodo 3 - Evaluate Data
# ---------------------------------------------------------------------------

def evaluate_data(state: AgentState) -> dict:
    iterations = state.get("iterations", 0)

    if iterations >= MAX_ITERATIONS:
        trace_entry = {
            "node": "evaluate_data",
            "sufficient": True,
            "reasoning": f"Raggiunto il limite di {MAX_ITERATIONS} chiamate tool, procedo con i dati disponibili.",
        }
        return {
            "sufficient": True,
            "next_tool": None,
            "next_tool_args": None,
            "trace": state.get("trace", []) + [trace_entry],
        }

    query = state["standalone_query"]
    machine = state.get("active_machine")
    gathered = state.get("gathered_data", [])
    data_summary = "\n\n".join(
        f"- Tool: {d['tool']}({d['args']}) -> Risultato: {d['result']}" for d in gathered
    )

    prompt = f"""Sei un assistente di manutenzione industriale. Tool disponibili:
{TOOL_DESCRIPTIONS}
Macchina attiva nella conversazione: {machine or "nessuna"}
Domanda dell'utente: "{query}"

Dati raccolti finora:
{data_summary}

Valuta se questi dati sono sufficienti per dare una risposta accurata e concreta.
Se manca ancora qualcosa di importante, indica il PROSSIMO tool da chiamare.
Non richiamare un tool già usato con gli stessi argomenti."""

    decision = _llm.with_structured_output(EvaluationDecision).invoke(prompt)

    next_tool, next_tool_args, used_fallback = None, None, False
    if not decision.sufficient:
        if decision.next_tool_name:
            next_tool, next_tool_args = decision.next_tool_name, decision.next_tool_args
        else:
            next_tool, next_tool_args = _fallback_tool(
                machine, query, reasoning=decision.reasoning, gathered=gathered
            )
            used_fallback = True

    trace_entry = {
        "node": "evaluate_data",
        "sufficient": decision.sufficient,
        "reasoning": decision.reasoning,
        **({"tool_name_fallback": next_tool} if used_fallback else {}),
    }

    return {
        "sufficient": decision.sufficient,
        "next_tool": next_tool,
        "next_tool_args": next_tool_args,
        "trace": state.get("trace", []) + [trace_entry],
    }


def route_after_evaluate(state: AgentState) -> str:
    if state.get("sufficient"):
        return "generate_answer"
    return "call_tool" if state.get("next_tool") else "generate_answer"


# ---------------------------------------------------------------------------
# Nodo 4 - Generate Answer
# ---------------------------------------------------------------------------

def generate_answer(state: AgentState) -> dict:
    query = state["standalone_query"]
    machine = state.get("active_machine")
    gathered = state.get("gathered_data", [])
    # Cronologia precedente (escludendo la domanda corrente, già inclusa in `query`)
    history = _format_history(state.get("messages", [])[:-1])
    history_block = f"\nCronologia recente della conversazione:\n{history}\n" if history else ""

    if gathered:
        data_summary = "\n\n".join(
            f"- Da {d['tool']}({d['args']}): {d['result']}" for d in gathered
        )
        prompt = f"""Sei un assistente di manutenzione industriale che dialoga con un tecnico.
Macchina di cui si sta parlando: {machine or "nessuna"}
{history_block}
Domanda attuale: "{query}"

Dati raccolti dal CMMS e dai manuali:
{data_summary}

Rispondi in modo chiaro e concreto, citando i dati rilevanti (valori, date, fonti
documentali). Mantieni coerenza con quanto già detto nella conversazione, senza
ripetere inutilmente informazioni già date. Se i dati non bastano per rispondere
con certezza, dillo esplicitamente."""
    else:
        prompt = f"""Sei un assistente di manutenzione industriale che dialoga con un tecnico.
Macchina di cui si sta parlando: {machine or "nessuna"}
{history_block}
Rispondi alla domanda con le tue conoscenze generali: "{query}\""""

    response = _llm.invoke(prompt)

    trace_entry = {"node": "generate_answer"}

    return {
        "final_answer": response.content,
        # Aggiunge la risposta alla cronologia persistente (reducer add_messages = append)
        "messages": [AIMessage(content=response.content)],
        "trace": state.get("trace", []) + [trace_entry],
    }


# ---------------------------------------------------------------------------
# Nodo 5 - Propose Write Action (NUOVO, Fase 7)
# ---------------------------------------------------------------------------

def propose_write_action(state: AgentState) -> dict:
    """Dopo la diagnosi (READ, autonoma), decide se PROPORRE l'apertura di un
    work order (WRITE). Non esegue nulla: prepara solo la proposta e la domanda
    di conferma da mostrare all'utente. L'esecuzione vera avviene solo in
    handle_confirmation, dopo un consenso esplicito."""
    machine = state.get("active_machine")
    gathered = state.get("gathered_data", [])
    final_answer = state.get("final_answer", "")

    # Senza una macchina nota o senza dati raccolti non c'è nulla su cui basare
    # una proposta concreta: si evita di proporre "a vuoto".
    if not machine or not gathered:
        trace_entry = {"node": "propose_write_action", "should_propose": False,
                       "reasoning": "Nessuna macchina attiva o nessun dato raccolto in questo turno."}
        return {"pending_action": None, "trace": state.get("trace", []) + [trace_entry]}

    data_summary = "\n\n".join(
        f"- Da {d['tool']}({d['args']}): {d['result']}" for d in gathered
    )

    prompt = f"""Sei un assistente di manutenzione industriale. Hai appena fornito questa
diagnosi per la macchina {machine}:

\"\"\"{final_answer}\"\"\"

Basata sui seguenti dati raccolti dal CMMS e dai manuali:
{data_summary}

Decidi se ha senso PROPORRE (non eseguire: solo proporre) l'apertura di un work
order di manutenzione per {machine}. Proponi SOLO se i dati mostrano un problema
concreto (valori fuori soglia, trend anomalo, guasto). NON proporre per domande
puramente informative, anche se riguardano la stessa macchina."""

    decision = _llm.with_structured_output(WriteProposal).invoke(prompt)

    if not decision.should_propose:
        trace_entry = {"node": "propose_write_action", "should_propose": False, "reasoning": decision.reasoning}
        return {"pending_action": None, "trace": state.get("trace", []) + [trace_entry]}

    description = (decision.description or f"Anomalia rilevata su {machine} da verificare.").strip()
    priority = decision.priority or "medium"

    pending_action = {"machine_id": machine, "description": description, "priority": priority}
    question = (
        f"\n\n---\nHo rilevato una possibile anomalia su **{machine}**. "
        f"Vuoi che apra un work order?\n"
        f"- Descrizione: {description}\n"
        f"- Priorità: {priority}\n\n"
        f"Rispondi 'sì' per confermare, oppure dimmi se preferisci non procedere."
    )

    trace_entry = {"node": "propose_write_action", "should_propose": True, "reasoning": decision.reasoning,
                   "pending_action": pending_action}

    return {
        "pending_action": pending_action,
        "final_answer": final_answer + question,
        "trace": state.get("trace", []) + [trace_entry],
    }


def route_after_propose(state: AgentState) -> str:
    return "request_confirmation" if state.get("pending_action") else "END"


# ---------------------------------------------------------------------------
# Nodo 6 - Request Confirmation (NUOVO, Fase 7) — qui il grafo si FERMA
# ---------------------------------------------------------------------------

def request_confirmation(state: AgentState) -> dict:
    """Sospende l'esecuzione del grafo con interrupt(), in attesa di un resume
    esplicito (Command(resume=True/False)) da parte del livello API.

    IMPORTANTE: questo nodo viene RIESEGUITO DA CAPO al resume (è così che
    funziona interrupt() in LangGraph): qualsiasi codice PRIMA di interrupt()
    gira due volte. Per questo il nodo non fa nient'altro che leggere
    pending_action (già calcolato da propose_write_action) e chiamare
    interrupt(): nessun effetto collaterale da rieseguire per sbaglio."""
    answer = interrupt(state.get("pending_action") or {})
    return {"confirmed": bool(answer)}


# ---------------------------------------------------------------------------
# Nodo 7 - Handle Confirmation (NUOVO, Fase 7) — unico punto che può scrivere
# ---------------------------------------------------------------------------

def handle_confirmation(state: AgentState) -> dict:
    """Unico punto di tutto il grafo in cui può essere chiamato create_work_order.
    Non è nemmeno registrato in TOOL_REGISTRY (vedi agent/tools.py): il loop
    autonomo READ (analyze_query/call_tool/evaluate_data) non può raggiungerlo
    per nessuna via, a prescindere da cosa decida il modello."""
    pending = state.get("pending_action") or {}
    confirmed = state.get("confirmed", False)

    if confirmed:
        # FASE 10: anche l'azione WRITE è tracciata (span + metriche + log).
        with tracer.span("tool_call", {"tool": "create_work_order", "args": pending}) as span:
            t0 = time.perf_counter()
            result = cmms_client.create_work_order(
                pending.get("machine_id", ""), pending.get("description", ""), pending.get("priority", "medium")
            )
            duration_ms = (time.perf_counter() - t0) * 1000
            has_error = "error" in result
            if span is not None and has_error:
                span.set_error(str(result["error"]))
            metrics.record_tool("create_work_order", duration_ms, error=has_error)
            if not has_error:
                metrics.record_work_order()
            log_event("tool_call", tool="create_work_order", args=pending,
                      duration_ms=round(duration_ms, 1), error=has_error,
                      work_order_id=None if has_error else result.get("id"))

        if "error" in result:
            message = f"Non sono riuscito a creare il work order: {result['error']}"
        else:
            message = (f"Fatto. Ho creato il work order **{result['id']}** per "
                       f"{pending.get('machine_id')} (priorità: {result.get('priority')}).")
    else:
        message = "Ok, non ho creato nessun work order. Fammi sapere se cambi idea."

    trace_entry = {"node": "handle_confirmation", "confirmed": confirmed, "outcome": message}

    return {
        "final_answer": message,
        "pending_action": None,          # la proposta è risolta, non resta pendente
        "messages": [AIMessage(content=message)],
        "trace": state.get("trace", []) + [trace_entry],
    }


# ---------------------------------------------------------------------------
# Interpretazione della risposta dell'utente a una conferma pendente
# (usata dal livello API in agent/main.py, non è un nodo del grafo)
# ---------------------------------------------------------------------------

# Filtri deterministici per il sì/no esplicito (fail-safe): un modello locale
# debole (llama3.1) può classificare un RIFIUTO come conferma, e significherebbe
# eseguire un'azione WRITE senza consenso. Il rifiuto esplicito è quindi deciso
# senza passare dal modello, e il modello può "confermare" solo se il messaggio
# contiene parole di assenso esplicite. In caso di dubbio NON si esegue mai.

_DENY_RE = re.compile(
    r"\b(no|non|non ora|non adesso|non serve|non farlo|non procedere|annull\w*|"
    r"rinunci\w*|stop|lascia stare|lascia|meglio no|rimanda|rimandiamo|non ci pensare)\b",
    re.IGNORECASE,
)

_CONFIRM_RE = re.compile(
    r"\b(s[ìi]|s[ìi] certo|s[ìi] va bene|conferm\w*|procedi|procediamo|vai pure|"
    r"va bene|ok(ay)?|perfetto|assolutamente|apri\w*|crea\w*|fai pure|d'accordo)\b",
    re.IGNORECASE,
)


def interpret_confirmation(user_message: str, pending_action: dict) -> ConfirmationInterpretation:
    text = (user_message or "").strip()
    lowered = text.lower()

    # 1) Rifiuto esplicito: deciso in modo deterministico, il modello non entra
    #    in gioco. Un "no" non deve MAI diventare un work order.
    if _DENY_RE.search(lowered):
        return ConfirmationInterpretation(
            intent="deny",
            reasoning="Rifiuto esplicito riconosciuto dal filtro deterministico (non passa dal modello).",
        )

    # 1b) Conferma esplicita: anch'essa deterministica (sì, confermo, ok, ...).
    #     Il modello serve solo per i messaggi davvero ambigui.
    if _CONFIRM_RE.search(lowered):
        return ConfirmationInterpretation(
            intent="confirm",
            reasoning="Conferma esplicita riconosciuta dal filtro deterministico (non passa dal modello).",
        )

    # 2) Caso ambiguo: fa interpretare al modello.
    prompt = f"""Avevi proposto questa azione, in attesa di conferma dell'utente:
- Azione: apertura work order per {pending_action.get('machine_id')}
- Descrizione: {pending_action.get('description')}
- Priorità: {pending_action.get('priority')}

L'utente ha risposto: "{user_message}"

Interpreta questa risposta. Se non è chiaramente un sì o un no (es. è una nuova
domanda, una richiesta di chiarimento, un cambio di argomento), classificala
come 'unrelated': è sempre meglio chiedere di nuovo che eseguire un'azione
per un'interpretazione sbagliata."""
    interpretation = _llm.with_structured_output(ConfirmationInterpretation).invoke(prompt)

    # 3) Fail-safe: il modello può "confermare" SOLO se il messaggio contiene
    #    una parola di assenso esplicita. Altrimenti si degrada a 'unrelated'
    #    (la proposta resta in sospeso) invece di eseguire l'azione di scrittura.
    if interpretation.intent == "confirm" and not _CONFIRM_RE.search(lowered):
        return ConfirmationInterpretation(
            intent="unrelated",
            reasoning="Il modello ha risposto 'confirm' ma il messaggio non contiene parole "
                      "di assenso esplicite: fail-safe, la proposta resta in sospeso.",
        )

    return interpretation
