# Design dell'agente

L'agente è un **graph LangGraph** (`backend/agent/graph.py`) che combina:

- **LLM strutturato** (Ollama / `llama3.1`) per le decisioni (analisi,
  valutazione, proposta, interpretazione conferma);
- **tool CMMS** (lettura e scrittura) e **RAG** sui manuali;
- **Human-in-the-loop (HITL)** con `interrupt()` + checkpointer per le
  azioni di scrittura;
- **fail-safe deterministici** per non eseguire mai un'azione WRITE senza
  consenso esplicito.

## Graph

```
START
  │
  ▼
resolve_context ──► analyze_query ──► (serve un tool?)
  │                    │                    │
  │                    │ sì                no
  │                    ▼                   │
  │                 call_tool ──► evaluate_data ──► (sufficiente?)
  │                    ▲                     │          │ no
  │                    └──────────────────────┘          ▼
  │                                              generate_answer
  │                                                     │
  │                                                     ▼
  │                                          propose_write_action
  │                                                     │
  │                                          (azione proposta?)
  │                                          │ sì            │ no
  │                                          ▼               ▼
  │                                   request_confirmation  END
  │                                          │ interrupt()
  │                                          ▼
  └──────────────────► handle_confirmation ──► END
```

Nodi:

| Nodo | Ruolo |
|---|---|
| `resolve_context` | Riscrive la domanda in forma autonoma usando la cronologia (`standalone_query`), estraggge la macchina attiva (`active_machine`). |
| `analyze_query` | LLM strutturato (`AnalysisDecision`): servono dati? Quale tool chiamare per primo? |
| `call_tool` | Esegue il tool (CMMS o RAG) e aggiunge il risultato a `gathered_data`. |
| `evaluate_data` | LLM strutturato (`EvaluationDecision`): i dati raccolti bastano? Se no, quale tool chiamare dopo. Ciclo con limite `MAX_ITERATIONS`. |
| `generate_answer` | Genera la risposta finale usando i dati raccolti (e le fonti RAG). |
| `propose_write_action` | LLM strutturato (`WriteProposal`): se i dati mostrano un problema concreto, **propone** (non esegue) l'apertura di un work order e prepara la domanda di conferma. |
| `request_confirmation` | **Sospende il graph** con `interrupt(pending_action)`. Non contiene effetti collaterali: al resume viene rieseguito da capo, e l'unico codice prima di `interrupt()` è la lettura di `pending_action`. |
| `handle_confirmation` | **Unico punto del graph** in cui `create_work_order` può essere chiamato. |

## State

`AgentState` (`backend/agent/state.py`, TypedDict):

- **Persistiti** (grazie al checkpointer, indicizzati per `thread_id`/`session_id`):
  - `messages` — cronologia Tecnico/Assistente (reducer `add_messages`);
  - `active_machine` — ultima macchina di cui si stava parlando.
- **Per-turno** (azzerati ad ogni `run_graph`): `query`, `standalone_query`,
  `gathered_data`, `next_tool`, `next_tool_args`, `sufficient`,
  `iterations`, `trace`, `final_answer`, `pending_action`, `confirmed`.

## Checkpointer e memoria

- `CHECKPOINT_DB` (variabile d'ambiente) → **SqliteSaver** persistente
  (`langgraph-checkpoint-sqlite`).
- Non impostato → **MemorySaver** (RAM): la memoria e le conferme pendenti
  si perdono al riavvio.
- Il checkpointer è **obbligatorio** per l'HITL: `interrupt()` salva lo stato
  a metà graph e `resume_confirmation` lo ricarica.

API di livello (`graph.py`):

- `run_graph(query, session_id)` — esegue un turno; se il graph si ferma in
  `request_confirmation`, restituisce `pending_action`.
- `get_pending_action(session_id)` — azione in attesa di conferma.
- `resume_confirmation(confirmed, session_id, user_message)` — riprende il
  graph con `Command(resume=...)`.
- `cancel_pending_confirmation(session_id)` — scarta la proposta.
- `get_history`, `get_active_machine`, `reset_session` — ispezione/reset.

## HITL e fail-safe delle conferme

La catena di sicurezza per le azioni WRITE:

1. **Separazione READ/WRITE**: il loop autonomo
   (`analyze_query → call_tool → evaluate_data`) usa solo tool di lettura.
   `create_work_order` **non è nel registry dei tool**: il loop non può
   raggiungerlo per nessuna via, a prescindere da cosa decida il modello.
2. **Proposta, non esecuzione**: `propose_write_action` produce solo
   `pending_action` (machine, descrizione, priorità) e una domanda di
   conferma; l'esecuzione avviene solo in `handle_confirmation`.
3. **Interpretazione deterministica** (`interpret_confirmation`):
   - rifiuto esplicito ("no", "annulla", "stop", …) → `deny` deciso da
     regex, **il modello non entra in gioco** (un modello locale debole non
     può mai trasformare un rifiuto in una conferma);
   - conferma esplicita ("sì", "confermo", "procedi", …) → `confirm`
     deterministico;
   - messaggio ambiguo → LLM strutturato (`ConfirmationInterpretation`);
     in caso di dubbio si classifica come `unrelated` e **non si esegue mai**:
     è sempre meglio chiedere di nuovo.

## Tracing

Ogni turno genera span (`graph.run`, per-nodo, `tool_call`, `rag_retrieval`,
`llm_call`), metriche (turni, latenze, token, errori, work order) e log
JSONL; la trace completa è exportata in `logs/traces/{session_id}.json`
(vedi `docs/deployment.md` → Monitoring).
