"""Schemi di output strutturato dell'agente.

Ogni schema è quello che i nodi del grafo passano a
`llm.with_structured_output(...)`: il modello deve restituire esattamente
queste forme (i campi `Literal` vincolano i valori ammessi, es. il nome del
tool o l'intento della conferma).
"""

from typing import Dict, Literal, Optional

from pydantic import BaseModel, Field

# Nomi validi dei tool, come Literal: forza lo schema strutturato a restituire
# uno di questi valori esatti nel campo dedicato (invece di lasciare che il
# modello lo scriva solo a parole dentro il "reasoning", dove verrebbe ignorato).
ToolName = Literal[
    "get_machine_status", "get_sensor_data", "get_maintenance_history",
    "get_open_work_orders", "search_manual",
]


class ContextResolution(BaseModel):
    standalone_query: str = Field(
        description="La domanda del tecnico riscritta in forma AUTONOMA, comprensibile senza "
                    "la cronologia: sostituisci riferimenti impliciti (es. 'e le vibrazioni?', "
                    "'e quella pompa?') con la macchina e l'argomento espliciti."
    )
    machine_id: Optional[str] = Field(
        None,
        description="ID della macchina a cui si riferisce la domanda (es. 'P-102'). "
                    "None se la domanda non riguarda nessuna macchina specifica.",
    )


class AnalysisDecision(BaseModel):
    needs_information: bool = Field(
        description="True se servono dati da CMMS o manuali per rispondere bene, "
                    "False se si può rispondere subito con conoscenza generale."
    )
    tool_name: Optional[ToolName] = Field(
        None, description="Nome ESATTO del tool da chiamare per primo, tra quelli elencati sopra."
    )
    tool_args: Optional[Dict[str, str]] = Field(
        None, description="Argomenti del tool, es. {'machine_id': 'P-102'} oppure {'query': '...'}."
    )
    reasoning: str = Field(description="Breve motivazione della decisione.")


class EvaluationDecision(BaseModel):
    sufficient: bool = Field(description="True se i dati raccolti bastano per rispondere bene.")
    next_tool_name: Optional[ToolName] = Field(None, description="Se sufficient è False, prossimo tool.")
    next_tool_args: Optional[Dict[str, str]] = Field(None, description="Argomenti per il prossimo tool.")
    reasoning: str = Field(description="Breve motivazione della valutazione.")


class WriteProposal(BaseModel):
    """Decide se, in base alla diagnosi appena fatta, ha senso PROPORRE
    (mai eseguire direttamente) l'apertura di un work order di manutenzione."""
    should_propose: bool = Field(
        description="True SOLO se i dati raccolti mostrano un problema concreto che "
                    "giustifica un intervento di manutenzione (es. valori fuori soglia, "
                    "trend anomalo). False per domande puramente informative (es. 'quali "
                    "sono le soglie di allarme', 'come funziona la pompa', 'cosa dice il "
                    "manuale sulla lubrificazione') anche se riguardano la stessa macchina."
    )
    description: Optional[str] = Field(
        None, description="Descrizione sintetica e concreta del problema, da usare come "
                          "descrizione del work order (se should_propose è True)."
    )
    priority: Optional[Literal["low", "medium", "high"]] = Field(
        "medium", description="Urgenza del problema riscontrato."
    )
    reasoning: str = Field(description="Perché proporre (o non proporre) un work order.")


class ConfirmationInterpretation(BaseModel):
    """Interpreta la risposta dell'utente a una richiesta di conferma.

    Il default è sempre prudente: in caso di dubbio, 'unrelated' (non è una
    risposta sì/no) è preferibile a interpretare erroneamente un'esitazione
    come un consenso — non si esegue MAI un'azione di scrittura per errore
    di interpretazione."""
    intent: Literal["confirm", "deny", "unrelated"] = Field(
        description="'confirm' solo se il messaggio è un chiaro assenso (es. 'sì', 'vai "
                    "pure', 'confermo', 'ok procedi'). 'deny' solo se è un chiaro diniego "
                    "(es. 'no', 'non ora', 'annulla'). 'unrelated' in OGNI ALTRO caso: "
                    "domande, richieste di chiarimento, cambi di argomento, risposte ambigue."
    )
    reasoning: str = Field(description="Breve motivazione dell'interpretazione.")
