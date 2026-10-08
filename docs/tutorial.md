# Fase 1 - Chatbot base

## 1. Installa Ollama
https://ollama.com/download

## 2. Scarica un modello
```bash
ollama pull llama3.1
```
(Ollama parte in automatico come servizio su http://localhost:11434)

## 3. Attiva l'ambiente conda già creato e installa le dipendenze mancanti
```bash
conda activate myenv
pip install -r requirements.txt
```
(`myenv` è l'ambiente con Python 3.12 creato in precedenza con `fastapi`, `pydantic`,
`langchain`, `langgraph`, `langsmith` già dentro; qui installiamo solo `langchain-ollama`
che manca.)

## 4. Avvia il server
```bash
uvicorn main:app --reload --port 8000
```

## 5. Testa l'endpoint
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What should I check if pump P-102 is overheating?"}'
```

Risposta attesa:
```json
{"answer": "..."}
```

Puoi anche aprire http://localhost:8000/docs per la UI interattiva di FastAPI (Swagger).

# Fase 2 - RAG su documentazione industriale

## 1. Modelli Ollama necessari
```bash
ollama pull llama3.1
ollama pull nomic-embed-text
```

## 2. Attiva l'ambiente conda già creato e installa le dipendenze mancanti
```bash
conda activate myenv
pip install -r requirements.txt
```
(`myenv` ha già `fastapi`, `pydantic`, `langchain`, `langgraph`, `langsmith`;
qui aggiungiamo solo i pacchetti specifici del RAG: loader PDF, splitter,
integrazione Ollama e Chroma come vector DB.)

## 3. Metti i PDF nella cartella docs/
```
fase2/docs/
 ├── pump_manual.pdf
 ├── maintenance_procedures.pdf
 ├── safety_manual.pdf
 ├── compressor_manual.pdf
 └── troubleshooting.pdf
```

## 4. Costruisci il vector DB (una volta, o quando cambiano i documenti)
```bash
python ingest.py
```
Questo crea una cartella `chroma_db/` con gli embedding salvati su disco.

## 5. Avvia il server
```bash
uvicorn app.main:app --reload --port 8000
```

## 6. Testa
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Why is P-102 overheating?"}'
```

Risposta attesa:
```json
{
  "answer": "...",
  "sources": [
    {"file": "docs/pump_manual.pdf", "page": 4},
    {"file": "docs/troubleshooting.pdf", "page": 2}
  ]
}
```

## Come funziona la pipeline
1. **ingest.py**: legge i PDF (`PyPDFLoader`), li spezza in chunk da ~1000 caratteri
   con overlap (`RecursiveCharacterTextSplitter`), calcola gli embedding con
   `nomic-embed-text` via Ollama, e li salva in un DB vettoriale Chroma persistente.
2. **app/rag.py**: al momento della domanda, cerca i 4 chunk più simili (`retriever`),
   li inserisce come contesto in un prompt, e chiede a `llama3.1` di rispondere
   SOLO in base a quel contesto. Restituisce anche file e pagina di provenienza.
3. **app/main.py**: espone tutto via endpoint `POST /chat`, stesso contratto
   della Fase 1 ma con in più il campo `sources`.

## Note pratiche
- Se un PDF è scansionato (immagine, non testo selezionabile), `PyPDFLoader` non
  estrae nulla: in quel caso serve OCR (es. `pytesseract` o `unstructured`) prima del chunking.
- `k=4` in `rag.py` è il numero di chunk recuperati: aumentalo se le risposte
  risultano incomplete, riducilo se il modello si confonde con troppo contesto.
- Per aggiornare la knowledge base basta rilanciare `ingest.py` dopo aver
  aggiunto/tolto PDF nella cartella `docs/`.

  # Fase 3 - Tool Calling (Agent)

Il chatbot diventa un vero agente: il modello decide da solo QUALI tool
chiamare e in che ordine, in base alla domanda dell'utente.

```
User -> LLM -> decide di chiamare get_sensor_data() -> Tool result -> LLM
     -> decide di chiamare search_manual() -> Tool result -> LLM -> Answer
```

## 1. Modelli Ollama necessari
```bash
ollama pull llama3.1          # supporta tool calling
ollama pull nomic-embed-text  # per il tool search_manual (RAG)
```

## 2. Ambiente
```bash
conda activate myenv
pip install -r requirements.txt
```

## 3. Costruisci il vector DB per il tool search_manual
I PDF sono già in `docs/` (stessi della Fase 2). Se li modifichi, rilancia:
```bash
python ingest.py
```

## 4. Avvia il server
```bash
uvicorn app.main:app --reload --port 8001
```

## 5. Testa
```bash
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Perché P-102 sta dando problemi?"}'
```

Risposta attesa (esempio):
```json
{
  "answer": "P-102 mostra un trend di temperatura cuscinetto in aumento (da 68°C a 93°C negli ultimi 5 giorni)... [diagnosi completa]",
  "steps": [
    {"tool": "get_machine_status", "args": {"machine_id": "P-102"}},
    {"tool": "get_sensor_data", "args": {"machine_id": "P-102"}},
    {"tool": "get_maintenance_history", "args": {"machine_id": "P-102"}},
    {"tool": "get_open_work_orders", "args": {"machine_id": "P-102"}},
    {"tool": "search_manual", "args": {"query": "P-102 bearing temperature high cause"}}
  ]
}
```

Il campo `steps` ti mostra esattamente la sequenza di tool che l'agente ha
deciso di chiamare — utile per debug e per capire il "ragionamento" del modello.

## File del progetto

| File | Ruolo |
|---|---|
| `app/mock_cmms.py` | Dati finti del CMMS (macchine, sensori, manutenzione, work order) |
| `app/tools.py` | Wrapping delle funzioni CMMS + RAG come tool LangChain (`@tool`) |
| `app/agent.py` | Costruzione dell'agente ReAct con LangGraph (`create_react_agent`) |
| `app/rag.py` | Pipeline RAG riusata dal tool `search_manual` (dalla Fase 2) |
| `app/main.py` | Endpoint FastAPI `/chat` |
| `ingest.py` | Costruisce il vector DB dai PDF in `docs/` |

## Come funziona `create_react_agent`

`langgraph.prebuilt.create_react_agent` costruisce un grafo con due nodi:
- **nodo LLM**: riceve la conversazione + la lista dei tool disponibili
  (con nome, parametri e docstring), e decide se rispondere direttamente
  o chiamare uno o più tool.
- **nodo tool**: esegue il/i tool richiesti e rimanda il risultato all'LLM.

Il grafo cicla tra questi due nodi finché il modello non produce una
risposta finale senza ulteriori tool call. Il `SYSTEM_PROMPT` in
`app/agent.py` guida il modello a seguire una sequenza diagnostica
ragionevole (stato -> sensori -> storico -> work order aperti -> manuali),
ma il modello resta libero di adattare l'ordine o saltare passaggi non
necessari in base alla domanda.

## Note pratiche
- **Il modello deve supportare tool calling**: non tutti i modelli Ollama
  lo fanno bene. `llama3.1`, `qwen2.5` e `mistral-nemo` sono scelte solide.
  Se il modello "dimentica" di chiamare i tool o inventa risposte, prova
  a cambiare modello o a rendere il system prompt più esplicito.
- **`mock_cmms.py`** è pensato per essere sostituito facilmente: in un
  sistema reale, le stesse funzioni farebbero chiamate HTTP/SQL verso il
  CMMS aziendale (SAP PM, Maximo, Fiix...), mantenendo identica la firma
  e il tipo di ritorno così i tool in `tools.py` non cambiano.
- **`create_work_order` modifica lo stato in memoria**: riavviando il
  server, i work order creati "a runtime" vengono persi (è tutto in RAM).
  Per la demo va bene; in produzione andrebbe persistito su DB.


  # Fase 4 - Workflow agentico esplicito con LangGraph

Rispetto alla Fase 3 (dove `create_react_agent` gestiva tutto il ciclo
"decidi tool -> chiama -> osserva -> ripeti" internamente, come una scatola
nera), qui il workflow è costruito a mano nodo per nodo: puoi vedere e
modificare esattamente ogni passaggio della decisione.

```
                 START
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
           generate_answer
                   |
                  END
```

## 1. Modelli Ollama necessari
```bash
ollama pull llama3.1
ollama pull nomic-embed-text
```

## 2. Ambiente
```bash
conda activate myenv
pip install -r requirements.txt
```

## 3. Vector DB per il tool search_manual
```bash
python ingest.py
```

## 4. Avvia il server
```bash
uvicorn app.main:app --reload --port 8002
```

## 5. Testa
```bash
curl -X POST http://localhost:8002/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Perché P-102 sta dando problemi?"}'
```

Risposta (esempio semplificato):
```json
{
  "answer": "...",
  "gathered_data": [
    {"tool": "get_sensor_data", "args": {"machine_id": "P-102"}, "result": {...}},
    {"tool": "search_manual", "args": {"query": "P-102 bearing temperature rising cause"}, "result": {...}}
  ],
  "trace": [
    {"node": "analyze_query", "needs_information": true, "reasoning": "..."},
    {"node": "call_tool", "tool": "get_sensor_data", "args": {"machine_id": "P-102"}},
    {"node": "evaluate_data", "sufficient": false, "reasoning": "Ho i dati sensori ma non ho ancora consultato i manuali per le cause note."},
    {"node": "call_tool", "tool": "search_manual", "args": {"query": "..."}},
    {"node": "evaluate_data", "sufficient": true, "reasoning": "..."},
    {"node": "generate_answer"}
  ]
}
```

Il campo `trace` è il valore aggiunto di questa fase rispetto alla Fase 3:
mostra ESATTAMENTE cosa ha deciso ogni nodo e perché, non solo quali tool
sono stati chiamati.

## File del progetto

| File | Ruolo |
|---|---|
| `app/state.py` | `AgentState`: i dati che fluiscono tra i nodi del grafo |
| `app/tools.py` | Registro dei tool (funzioni pure, chiamate direttamente dal nodo `call_tool`) |
| `app/nodes.py` | I 4 nodi: `analyze_query`, `call_tool`, `evaluate_data`, `generate_answer` + le funzioni di routing |
| `app/graph.py` | Assembla i nodi in un grafo con `StateGraph`, gestisce gli edge condizionali |
| `app/main.py` | Endpoint FastAPI `/chat` |

## Come funzionano le decisioni

`analyze_query` ed `evaluate_data` NON usano tool/function calling come in
Fase 3: usano **structured output** (`llm.with_structured_output(...)` con
uno schema Pydantic). L'LLM restituisce un oggetto tipizzato con un booleano
(`needs_information` / `sufficient`) più eventualmente il prossimo tool da
chiamare. È il **grafo**, tramite `route_after_analyze` e
`route_after_evaluate`, a decidere concretamente verso quale nodo andare in
base a quel booleano — la logica di controllo è esplicita in Python, non
nascosta dentro il modello.

## Perché questo approccio invece di create_react_agent (Fase 3)?

| | Fase 3 (`create_react_agent`) | Fase 4 (grafo esplicito) |
|---|---|---|
| Percorso decisionale | Deciso interamente dall'LLM ad ogni step | Definito nel grafo, l'LLM decide solo i booleani/parametri |
| Controllo su loop infiniti | Gestito internamente dalla libreria | `MAX_ITERATIONS` esplicito in `nodes.py`, personalizzabile |
| Aggiungere uno step custom (es. validazione umana, logging, chiamata a un secondo modello) | Difficile, bisogna intervenire nel ciclo ReAct | Basta aggiungere un nodo ed un edge |
| Trasparenza/debug | Solo tool chiamati | Trace completo di ogni decisione con motivazione |

In pratica: Fase 3 è più semplice da scrivere ma meno controllabile; Fase 4
richiede più codice ma dà controllo fine sul workflow — utile quando serve
affidabilità, auditabilità (rilevante in un contesto industriale/safety) o
step che l'agente ReAct puro non gestirebbe bene (es. forzare sempre una
consultazione dei manuali di sicurezza prima di rispondere).

## Note pratiche
- `MAX_ITERATIONS = 5` in `app/nodes.py` è la valvola di sicurezza contro i
  loop infiniti: se il modello continua a chiedere altri dati, dopo 5
  chiamate tool si forza comunque il passaggio a `generate_answer`.
- `evaluate_data` riceve TUTTI i dati raccolti finora (non solo l'ultimo),
  quindi può accorgersi di avere già abbastanza informazioni anche dopo un
  solo giro.
- Se vuoi visualizzare il grafo, LangGraph supporta l'esportazione in
  Mermaid: `graph.get_graph().draw_mermaid()` (da `app/graph.py`) restituisce
  il testo del diagramma che puoi incollare su mermaid.live.


  # Fase 5 - Memoria conversazionale

Il grafo della Fase 4 ora ricorda la conversazione:

```
Tecnico: P-102 ha problemi di temperatura.
Agent:   Ho rilevato un trend in aumento del cuscinetto (68°C -> 93°C)...

Tecnico: E per quanto riguarda le vibrazioni?     <-- non nomina più P-102
Agent:   (capisce che si parla ancora di P-102)
```

## Cosa è stato aggiunto rispetto alla Fase 4

```
                 START
                   |
                   v
             resolve_context      <-- NUOVO
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
           generate_answer   (salva la risposta nella cronologia)
                   |
                  END
```

Tre ingredienti:

1. **Checkpointer LangGraph** (`app/graph.py`): salva lo stato del grafo per ogni
   `thread_id` (qui = `session_id`). Ad ogni nuova invocazione con lo stesso
   `session_id`, LangGraph ricarica `messages` e `active_machine`.
2. **`resolve_context`** (`app/nodes.py`): nuovo primo nodo. Guarda la cronologia
   e riscrive la domanda ellittica in una domanda autonoma
   ("E per quanto riguarda le vibrazioni?" -> "Com'è la situazione delle vibrazioni di P-102?"),
   poi aggiorna `active_machine`.
3. **Stato con memoria** (`app/state.py`): `messages` (con reducer `add_messages`,
   che accumula) e `active_machine` persistono; tutto il resto è "per-turno" e viene
   azzerato in `run_graph()` ad ogni domanda.

### Come viene scelta la macchina attiva (priorità)
1. ID esplicito nella domanda corrente (regex, es. "E C-201 invece?") — vince sempre,
   deterministico, senza passare dall'LLM.
2. ID restituito dall'LLM in `resolve_context`.
3. Macchina del turno precedente.

Se il tecnico cambia macchina, `active_machine` si aggiorna; le domande successive
seguono la nuova macchina.

## 1. Setup
```bash
conda activate myenv
pip install -r requirements.txt
ollama pull llama3.1
ollama pull nomic-embed-text
python ingest.py
```

## 2. Avvio
```bash
# Memoria in RAM (si perde al riavvio del server)
uvicorn app.main:app --reload --port 8003

# Memoria persistente su file SQLite (sopravvive ai riavvii)
CHECKPOINT_DB=memory.sqlite uvicorn app.main:app --reload --port 8003
```
> Con `--reload` il server si riavvia ad ogni modifica del codice: in modalità RAM
> perderai la memoria ogni volta. Per sviluppare, usa `CHECKPOINT_DB`.

## 3. Test della conversazione

**Primo messaggio** (senza `session_id`: il server ne genera uno e lo restituisce):
```bash
curl -X POST http://localhost:8003/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "P-102 ha problemi di temperatura."}'
```
La risposta contiene `"session_id": "3f1c...."` — **copialo**.

**Secondo messaggio** (stesso `session_id`, nessun riferimento a P-102):
```bash
curl -X POST http://localhost:8003/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "E per quanto riguarda le vibrazioni?", "session_id": "3f1c...."}'
```
Nella risposta troverai:
```json
{
  "session_id": "3f1c....",
  "answer": "...",
  "active_machine": "P-102",
  "standalone_query": "Com'è la situazione delle vibrazioni di P-102?",
  "gathered_data": [{"tool": "get_sensor_data", "args": {"machine_id": "P-102"}, "result": {...}}],
  "trace": [{"node": "resolve_context", ...}, ...]
}
```
`standalone_query` e `active_machine` ti mostrano esattamente cosa ha "capito" l'agente.

### Altri endpoint
| Endpoint | Cosa fa |
|---|---|
| `GET /sessions/{session_id}/history` | Cronologia completa + macchina attiva |
| `DELETE /sessions/{session_id}` | Cancella la memoria di quella conversazione |

## Note pratiche
- **`session_id` lo gestisce il client**: in un'interfaccia chat reale, il frontend
  genera/conserva un `session_id` per ogni conversazione e lo rimanda ad ogni messaggio.
  Sessioni diverse hanno memorie completamente separate (due tecnici possono parlare
  di macchine diverse contemporaneamente).
- **Finestra di cronologia**: ai prompt vengono passati solo gli ultimi
  `MAX_HISTORY_MESSAGES = 10` messaggi (in `app/nodes.py`), per non gonfiare il contesto
  del modello locale. La cronologia completa resta comunque salvata nel checkpoint.
- **I dati dei tool non persistono tra i turni**: `gathered_data` viene azzerato ad
  ogni domanda, quindi ogni turno rilegge dal CMMS. È voluto: i dati sensori cambiano
  nel tempo e non vogliamo risposte basate su letture vecchie. Persiste solo
  la conversazione.
- **Qualità di `resolve_context`**: dipende dal modello. Con modelli locali piccoli
  la riscrittura può a volte essere imprecisa; la regex sull'ID macchina e la
  "rete di sicurezza" (aggiunge `(macchina P-102)` se l'LLM se ne dimentica) coprono
  il caso più importante. Se noti riscritture strane, guarda il campo `standalone_query`
  della risposta: è il primo posto dove guardare.


  # Fase 6 - CMMS simulato (PostgreSQL + API REST)

Il CMMS diventa un **servizio separato** con un vero database. L'agente non tocca
mai il database: parla solo con l'API.

```
                          ┌──────────────────────── Agente (porta 8003) ───────────────────────┐
Tecnico ──> /chat ──────> │ grafo LangGraph + memoria ──> tool ──> agent/cmms_client.py (httpx) │
                          └──────────────────────────────────────────────────────┬──────────────┘
                                                                                 │ HTTP + X-API-Key
                          ┌──────────────────────── CMMS (porta 8010) ───────────▼──────────────┐
                          │ FastAPI  ──>  SQLAlchemy  ──>  PostgreSQL (porta 5432)               │
                          └───────────────────────────────────────────────────────────────────────┘
```

Il grafo, i nodi e la memoria (Fasi 4-5) **non sono cambiati**: è cambiato solo
cosa c'è dietro il registro dei tool (`agent/tools.py`): prima funzioni che leggevano
dati in memoria, ora chiamate HTTP al CMMS.

## Struttura

```
fase6/
 ├── docker-compose.yml        PostgreSQL 16
 ├── requirements.txt
 ├── .env.example
 ├── cmms/                     BACKEND (servizio 1)
 │    ├── db.py                connessione SQLAlchemy (DATABASE_URL)
 │    ├── models.py            5 tabelle: machines, sensor_readings, maintenance_records,
 │    │                                   work_orders, technicians
 │    ├── schemas.py           contratto dell'API (Pydantic)
 │    ├── main.py              endpoint REST + autenticazione con API key
 │    └── seed.py              crea le tabelle e inserisce i dati di esempio
 ├── agent/                    AGENTE (servizio 2)
 │    ├── cmms_client.py       NUOVO: client HTTP verso il CMMS
 │    ├── tools.py             registro tool -> ora usa cmms_client
 │    ├── state.py nodes.py graph.py main.py rag.py    (dalla Fase 5)
 ├── docs/  ingest.py          manuali PDF + costruzione vector DB (dalla Fase 2)
 └── tests/
      ├── test_cmms_api.py             18 test del backend (pytest)
      └── test_agent_e2e_offline.py    test agente -> CMMS -> DB (LLM finto)
```

> **Importante:** i comandi `uvicorn` vanno lanciati dalla cartella `fase6/` (quella che
> contiene sia `cmms/` sia `agent/`), perché gli import sono `cmms.…` e `agent.…`.
> Se lanciati da un'altra cartella ottieni `ModuleNotFoundError: No module named 'cmms'`.

## 1. Database PostgreSQL

Con Docker:
```bash
docker compose up -d
docker compose ps        # attendi "healthy"
```

Senza Docker (PostgreSQL già installato sul sistema):
```bash
sudo -u postgres psql -c "CREATE ROLE cmms LOGIN PASSWORD 'cmms';"
sudo -u postgres psql -c "CREATE DATABASE cmms OWNER cmms;"
```

## 2. Ambiente Python
```bash
conda activate myenv
pip install -r requirements.txt
```

## 3. Popola il database
```bash
python -m cmms.seed            # crea tabelle + dati (non fa nulla se il DB è già popolato)
python -m cmms.seed --reset    # cancella tutto e ricrea da zero
```
Dati di esempio: 3 macchine (`P-101` sana, `P-102` con trend di temperatura crescente
68→93 °C, `C-201`), 4 tecnici, letture sensori, storico manutenzione, 3 work order
(`WO-1042` aperto su P-102). Le date dei sensori sono relative a oggi.

## 4. Avvia il CMMS
```bash
uvicorn cmms.main:app --reload --port 8010
```
Documentazione interattiva (Swagger): http://localhost:8010/docs
(clicca "Authorize" e inserisci `dev-cmms-key`).

Prova a mano:
```bash
H="X-API-Key: dev-cmms-key"
curl -H "$H" localhost:8010/machines/P-102
curl -H "$H" "localhost:8010/machines/P-102/sensors?limit=3"
curl -H "$H" localhost:8010/machines/P-102/maintenance
curl -H "$H" "localhost:8010/work-orders?machine_id=P-102&status=active"
curl -X POST -H "$H" -H "Content-Type: application/json" localhost:8010/work-orders \
  -d '{"machine_id":"P-102","description":"Check cooling water pressure","priority":"high","assigned_to":1}'
```

| Endpoint | Descrizione |
|---|---|
| `GET /machines` · `GET /machines/{id}` | elenco / anagrafica e stato |
| `GET /machines/{id}/sensors?limit=5` | ultime N letture (cronologiche) |
| `GET /machines/{id}/maintenance` | storico interventi, con nome del tecnico |
| `GET /work-orders?machine_id=&status=` | `status` = `open`, `in_progress`, `closed` o `active` (open + in_progress) |
| `POST /work-orders` | crea un work order (`201`), ID progressivo `WO-1043`, `WO-1044`… |
| `GET /technicians` | elenco tecnici |
| `GET /health` | liveness + raggiungibilità del DB (senza API key) |

Errori: `401` API key mancante/errata · `404` macchina o tecnico inesistente · `422` dati non validi.
I tag macchina sono case-insensitive (`p-102` = `P-102`).

## 5. Avvia l'agente
```bash
ollama pull llama3.1 && ollama pull nomic-embed-text
python ingest.py                                                   # vector DB dei manuali (una volta)
CHECKPOINT_DB=memory.sqlite uvicorn agent.main:app --reload --port 8003
```
Controlla che i due servizi si vedano:
```bash
curl localhost:8003/health      # {"status":"ok","cmms":"up"}
```

Conversazione (il `session_id` restituito va rimandato nei messaggi successivi):
```bash
curl -X POST localhost:8003/chat -H "Content-Type: application/json" \
  -d '{"message": "P-102 ha problemi di temperatura."}'

curl -X POST localhost:8003/chat -H "Content-Type: application/json" \
  -d '{"message": "E per quanto riguarda le vibrazioni?", "session_id": "<quello di prima>"}'
```
Nel campo `gathered_data` della risposta vedi i dati **arrivati dal CMMS via API**.

## 6. Test
```bash
# Backend (SQLite temporaneo, senza Docker)
pytest tests/test_cmms_api.py -q

# Backend su PostgreSQL vero, in un database di test separato (viene azzerato!)
createdb -h localhost -U cmms cmms_test      # password: cmms
TEST_DATABASE_URL=postgresql+psycopg://cmms:cmms@localhost:5432/cmms_test pytest tests/test_cmms_api.py -q

# Agente -> CMMS -> DB, con LLM e RAG finti (serve il CMMS acceso e il DB popolato)
python tests/test_agent_e2e_offline.py
```

## Variabili d'ambiente

| Variabile | Usata da | Default |
|---|---|---|
| `DATABASE_URL` | CMMS | `postgresql+psycopg://cmms:cmms@localhost:5432/cmms` |
| `CMMS_API_KEY` | CMMS **e** agente (devono coincidere) | `dev-cmms-key` |
| `CMMS_BASE_URL` | agente | `http://localhost:8010` |
| `CMMS_TIMEOUT` | agente | `5` (secondi) |
| `CHECKPOINT_DB` | agente | vuoto = memoria in RAM |

## Scelte di progetto

- **L'agente non ha accesso al database, solo all'API.** Il database può cambiare (schema,
  motore, host) senza toccare l'agente; l'API è l'unico punto dove si applicano
  autenticazione, validazione e regole di business. Per collegare un CMMS vero (SAP PM,
  Maximo…) basta riscrivere `agent/cmms_client.py`: tool e grafo restano identici.
- **Gli errori non fanno crashare l'agente.** Macchina inesistente, CMMS spento, timeout,
  API key errata: `cmms_client` li trasforma in `{"error": "..."}`, che il grafo passa
  all'LLM come qualsiasi altro risultato, così può spiegare il problema al tecnico.
- **Sensori in una colonna JSONB.** Pompe e compressori misurano grandezze diverse:
  un JSONB evita una tabella con decine di colonne quasi sempre vuote. (Nota: PostgreSQL
  non preserva l'ordine delle chiavi nel JSONB, quindi l'ordine dei campi in una lettura
  può variare. Non ha importanza per l'agente.)
- **`create_work_order` esiste nel client ma NON tra i tool che il grafo chiama da solo.**
  Crea dati veri: un agente che apre ordini di lavoro in autonomia, magari ad ogni domanda
  diagnostica, produrrebbe duplicati e rumore. Il passo naturale successivo è un flusso con
  **conferma dell'utente** ("Vuoi che apra un work order per P-102?" → sì → POST). LangGraph
  lo supporta con gli interrupt (human-in-the-loop).
- **API key su tutti gli endpoint tranne `/health`.** È il minimo per un servizio interno;
  in produzione useresti OAuth2/mTLS e segreti gestiti da un vault, non un default nel codice.
- **Sviluppo con `--reload`:** se riavvii il CMMS il database resta (è PostgreSQL, non in RAM);
  se riavvii l'agente la memoria resta solo con `CHECKPOINT_DB`.


  # Fase 7 - Human in the Loop

Regola: **READ è autonomo, WRITE richiede conferma esplicita.**

```
Tecnico: P-102 ha problemi di temperatura.
Agent:   [legge sensori, storico, manuali — da solo]
         Il problema è un trend di temperatura crescente...

         ---
         Ho rilevato una possibile anomalia su P-102. Vuoi che apra un work order?
         - Descrizione: Temperatura cuscinetto in aumento, verificare raffreddamento
         - Priorità: high

Tecnico: Sì, confermo.
Agent:   Fatto. Ho creato il work order WO-1045 per P-102 (priorità: high).
```

## Il grafo

```
generate_answer                    (diagnosi — READ, autonomo)
       |
       v
propose_write_action                <- decide se PROPORRE un work order
       |
serve conferma? --NO--> END
       |
      YES
       v
request_confirmation                <- interrupt(): il grafo si FERMA qui
       |
       v  (in una chiamata HTTP successiva, con la risposta dell'utente)
handle_confirmation                 (WRITE — solo dopo consenso esplicito)
       |
      END
```

`propose_write_action` **non esegue nulla**: prepara solo la proposta. L'unico nodo che
può chiamare `create_work_order` è `handle_confirmation`, e ci arriva solo dopo un
`resume` esplicito con `confirmed=True`.

## Come funziona `interrupt()` (LangGraph)

`request_confirmation` chiama `interrupt(pending_action)`. Questo:
1. **Salva lo stato del grafo nel checkpointer** (per questo la Fase 7 richiede lo stesso
   checkpointer della Fase 5/6 — senza, l'interruzione non potrebbe sopravvivere tra due
   richieste HTTP separate).
2. **Sospende l'esecuzione**: `graph.invoke()` ritorna subito, con un `"__interrupt__"`
   nello stato invece di proseguire.
3. Una chiamata **successiva** con `graph.invoke(Command(resume=valore), config=...)`,
   sullo stesso `thread_id`, fa ripartire il grafo esattamente da lì, con `interrupt()`
   che restituisce `valore` invece di sospendersi di nuovo.

**Attenzione:** al resume, il nodo che contiene `interrupt()` viene **rieseguito da capo**
(non solo la riga dell'`interrupt()`). Per questo `request_confirmation` non fa altro che
leggere `pending_action` (già calcolato da `propose_write_action`, un nodo separato) e
chiamare `interrupt()`: se ci fosse altro codice prima, girerebbe due volte.

## Flusso in `agent/main.py`

Ad ogni messaggio, l'endpoint `/chat` controlla prima se c'è una conferma pendente per
quella sessione (`get_pending_action`):

- **Nessuna conferma pendente** → turno normale (`run_graph`).
- **Conferma pendente** → interpreta il messaggio con `interpret_confirmation` (un'altra
  chiamata LLM, con schema strutturato):
  - `"confirm"` → `resume_confirmation(True, ...)` → esegue `create_work_order`.
  - `"deny"` → `resume_confirmation(False, ...)` → annulla, nessuna azione.
  - `"unrelated"` → l'utente non ha risposto sì/no (ha cambiato argomento, fatto una
    domanda, esitato...). **Fail-safe**: si annulla la proposta in sicurezza
    (`cancel_pending_confirmation`, equivalente a un `resume(False)`) e si processa il
    messaggio come un turno normale nuovo — altrimenti l'agente resterebbe bloccato in
    attesa di una risposta che potrebbe non arrivare mai.

## Perché questo è rilevante per la sicurezza degli agenti

- **Confine architetturale, non solo prompt.** `create_work_order` non è nemmeno
  registrato in `agent/tools.py` (il `TOOL_REGISTRY` che il loop autonomo READ può
  chiamare). Anche se il modello "decidesse" di volerlo chiamare durante
  `analyze_query`/`evaluate_data`, non lo troverebbe: l'unica via che porta a
  `create_work_order` passa per `handle_confirmation`, raggiungibile solo dopo
  `request_confirmation`. È una barriera nel codice, non un'istruzione che il modello
  potrebbe ignorare.
- **Fail-safe by design.** In ogni caso di ambiguità (interpretazione incerta della
  risposta, utente che cambia argomento) il default è **non eseguire** l'azione. Non si
  assume mai un consenso implicito.
- **Human oversight esplicito.** La domanda di conferma mostra sempre cosa verrebbe fatto
  (macchina, descrizione, priorità) prima di farlo — l'utente approva un'azione concreta
  e visibile, non una black box.
- **Audit trail minimo.** Il work order creato riporta `created_by: "maintenance-agent"`
  (vedi `cmms_client.py`): è sempre distinguibile un intervento aperto dall'agente da uno
  aperto manualmente da un operatore sul CMMS.
- **Cosa manca per un sistema enterprise reale** (fuori scope qui, ma da tenere a mente):
  - **Autenticazione dell'utente che conferma.** Qui chiunque sappia il `session_id` può
    rispondere "sì". In produzione la conferma andrebbe legata a un utente autenticato
    (token/sessione), non a un campo di testo libero.
  - **Autorizzazioni granulari.** Non tutti i tecnici dovrebbero poter aprire work order
    ad alta priorità, o su qualunque macchina: servirebbe un controllo di autorizzazione
    lato CMMS (ruoli, scope) oltre alla singola API key condivisa della Fase 6.
  - **Timeout della conferma.** Una proposta pendente non scade mai da sola qui: in un
    sistema reale converrebbe un timeout esplicito, dopo il quale la proposta si annulla
    automaticamente.
  - **Log dedicato delle decisioni umane.** Il `trace` restituito da `/chat` mostra
    conferma/diniego, ma per un vero audit trail servirebbe uno storage persistente
    e immutabile di "chi ha confermato cosa, quando".

## Setup ed esecuzione

Identico alla Fase 6 (stesso CMMS, stesso `docker-compose.yml`, stessi PDF):
```bash
docker compose up -d                 # PostgreSQL
conda activate myenv
pip install -r requirements.txt
python -m cmms.seed
uvicorn cmms.main:app --reload --port 8010          # terminale 1
ollama pull llama3.1 && ollama pull nomic-embed-text
python ingest.py
CHECKPOINT_DB=memory.sqlite uvicorn agent.main:app --reload --port 8003   # terminale 2
```

> **Nota sul checkpointer:** qui `CHECKPOINT_DB` non è più solo "utile per non perdere
> la cronologia" come in Fase 5/6 — è ciò che rende possibile l'`interrupt()` tra due
> chiamate HTTP separate. Con `MemorySaver` (senza `CHECKPOINT_DB`) funziona comunque
> finché il processo resta vivo, ma un riavvio del server a metà conferma perderebbe
> la proposta pendente.

### Prova la conferma
```bash
curl -X POST localhost:8003/chat -H "Content-Type: application/json" \
  -d '{"message": "P-102 ha problemi di temperatura."}'
# -> "confirmation_required": true, "pending_action": {...}, session_id: "..."

curl -X POST localhost:8003/chat -H "Content-Type: application/json" \
  -d '{"message": "Sì, confermo.", "session_id": "<quello sopra>"}'
# -> "confirmation_required": false, answer contiene "WO-...."
```

### Prova il diniego
Uguale, ma con `"message": "No, non ora"` al secondo turno: nessun work order creato.

### Prova il cambio di argomento (fail-safe)
Al secondo turno manda una domanda qualunque invece di sì/no (es. `"Come sta P-101?"`):
la proposta precedente viene annullata automaticamente, senza creare nulla, e ricevi
comunque una risposta alla nuova domanda (con una nota che segnala l'annullamento).


# FASE 8 - Test

La suite è in `tests/` e NON richiede PostgreSQL, Ollama né porte note:
il CMMS gira su SQLite in-memory (variabile `DATABASE_URL`) e viene avviato
su una porta locale da uvicorn in un thread; l'LLM dell'agente è scriptato
(`ScriptedLLM` in `tests/conftest.py`), così i test verificano l'orchestrazione
e non la qualità del modello.

```bash
# Tutta la suite (unit + integration + agent)
pytest

# Solo un livello
pytest tests/test_unit_cmms_client.py   # client HTTP verso il CMMS (MockTransport)
pytest tests/test_unit_cmms_api.py      # API CMMS (main6 + SQLite)
pytest tests/test_unit_rag.py           # pipeline RAG (retriever/LLM stub)
pytest tests/test_integration_agent.py  # Agent -> Tool -> CMMS, livello HTTP
pytest tests/test_agent_dataset.py      # agent test guidati da tests/agent_dataset.json
```

I **agent test** sono dataset-driven: ogni caso di `tests/agent_dataset.json`
dichiara `question`, `expected_tools` (in ordine), `expected_information` e
`expected_behavior` (`answer_only`, `propose_work_order`, `propose_and_deny`);
il test verifica i tool chiamati, i dati davvero provenienti dal CMMS, il
flusso di conferma human-in-the-loop e l'effetto (o l'assenza) sul CMMS.

Per aggiungere un caso: una riga JSON nel dataset, nessun codice da scrivere.

`CHROMA_DB_OVERRIDE=/percorso/chroma_db pytest` permette di indicare un indice
Chroma diverso (utile in CI, dove `src/chroma_db` non esiste).

## File nuovi/modificati rispetto alla Fase 6

| File | Cosa cambia |
|---|---|
| `agent/state.py` | + `pending_action`, `confirmed` |
| `agent/nodes.py` | + `WriteProposal`, `ConfirmationInterpretation` (schemi); + `propose_write_action`, `request_confirmation`, `handle_confirmation` (nodi); + `interpret_confirmation` (usata dall'API, non è un nodo) |
| `agent/graph.py` | + i 3 nodi nel grafo; + `get_pending_action`, `resume_confirmation`, `cancel_pending_confirmation` |
| `agent/main.py` | `/chat` ora controlla `get_pending_action` prima di ogni turno; risposta con `confirmation_required` e `pending_action` |
| `agent/tools.py`, `agent/cmms_client.py`, `cmms/` | invariati dalla Fase 6 |


# Fase 9 - LLM Evaluation

  Da "l'agente sembra rispondere meglio" a "l'agente risponde meglio, e di quanto".
  Un dataset di **66 domande ground-truth** (derivate dai 5 manuali in `docs/` e dai
  dati seed del CMMS) e un harness che misura quantitativamente l'agente, così da
  confrontare **RAG v1 → RAG v2** con numeri:

  ```
  RAG v1  →  Evaluation  →  [numeri]
  RAG v2  →  Evaluation  →  [numeri]  →  confronto (Δ per metrica e per categoria)
  ```

  ## Metriche

  | Metrica | Cosa misura |
  |---|---|
  | Retrieval quality | `hit@k` e `MRR` rispetto ai file attesi + context relevance (giudice LLM) |
  | Answer relevance | Quanto la risposta è rilevante per la domanda (giudice LLM, 0-1) |
  | Faithfulness | Decomposizione in claim: claim supportati dal contesto / totali |
  | Tool selection | Precision/recall/F1 dei tool chiamati vs attesi |
  | Hallucination | Frazione di casi con claim non supportati dal contesto |
  | Latency | Wall-clock per domanda (p50/p95), incluso il turno di conferma HITL |
  | Token usage | Prompt + completion token di tutte le chiamate LLM del turno |

  ## Esecuzione

  ```bash
  # Prerequisiti: Ollama (llama3.1 + nomic-embed-text), indice v1 già costruito
  ollama pull llama3.1 && ollama pull nomic-embed-text
  python ingest.py

  # RAG v1 (baseline: k=4, chunk 1000/150, prompt standard)
  python evals/evaluator.py --variant v1

  # RAG v2 (es. k=6, prompt migliorato, indice con chunk 700/100)
  python evals/build_index.py --out evals/indices/chroma_v2 --chunk-size 700 --chunk-overlap 100
  python evals/evaluator.py --variant v2 --rag-config evals/rag_v2.json

  # Confronto quantitativo v1 vs v2
  python evals/evaluator.py --compare evals/results/v1.json evals/results/v2.json
  ```

  Il CMMS non va avviato: l'evaluator lo fa partire in-process (SQLite in-memory +
  seed, come nei test della Fase 8), così i dati sono deterministici. Opzioni utili:
  `--limit N`, `--categories a,b`, `--no-judge` (solo metriche deterministiche),
  `--resume` (riprende una run interrotta), `--judge-model` (giudice più forte).

  ## File

  | File | Ruolo |
  |---|---|
  | `evals/eval_dataset.json` | 66 domande ground-truth per categoria (diagnosi, procedure, sicurezza, manuali, trappole out-of-scope) |
  | `evals/evaluator.py` | Harness: esegue il dataset contro l'agente reale (CMMS in-process, Chroma, Ollama) e calcola le metriche |
  | `evals/judge.py` | LLM-as-judge: relevance, faithfulness (claim decomposition), hallucination, refusal |
  | `evals/build_index.py` | Indice Chroma con chunking/embedding configurabili (per gli esperimenti v2) |
  | `evals/rag_v2.json` | Esempio di config RAG v2 (k, prompt, indice, modello) |
  | `evals/README.md` | Documentazione completa: metriche, dataset, workflow v1→v2 |

# FASE 10 - Observability

Da "l'agente risponde" a "l'agente risponde, e sappiamo DOVE, QUANDO e QUANTO".
Introduciamo i tre pilastri dell'osservabilità:

```
Logging  - un evento strutturato (JSON) per ogni passo: turn_start, llm_call,
           tool_call, rag_retrieval, error, turn_end
Tracing  - un trace per turno: albero di span
           chat_turn -> llm_call / tool_call -> rag_retrieval -> llm_call
Metrics  - contatori e latenze in-process (turni, LLM, tool, RAG, token, errori)
```

La pipeline osservata è:

```
User query -> Agent -> LLM call -> Tool call -> RAG -> LLM -> Response
```

E per ogni turno sappiamo rispondere a:

- **quanto tempo ha impiegato** — durata del turno e di ogni span, p50/p95/max
- **quanti token sono stati utilizzati** — prompt/completion, per chiamata e totali
- **quale tool è stato chiamato** — span `tool_call` + metriche per tool
- **quali documenti sono stati recuperati** — span `rag_retrieval` (file + pagina)
- **dove si è verificato un errore** — span con `status=error` + evento log

## File

| File | Ruolo |
|---|---|
| `src/observability.py` | Il modulo: `Tracer`/`Span`/`Trace`, `Metrics`, `log_event`, LLM tracciato (span + token), `turn_summary` e `waterfall` |
| `src/main9.py` | App FastAPI della Fase 10: `/chat` con trace + riepilogo `observability`, `GET /traces/{session_id}`, `GET /metrics` |
| `src/nodes.py` | I tool girano dentro span `tool_call` (metriche + log degli errori); il LLM è tracciato (`make_tracked_llm`) |
| `src/rag.py` | Retrieval in span `rag_retrieval` (documenti recuperati) + LLM RAG in span `llm_call` |
| `tests/test_unit_observability.py` | Unit test: span, metriche, client tracciato (token in streaming), log |
| `tests/test_integration_observability.py` | `/chat` -> trace -> riepilogo + `/traces` + `/metrics` (LLM scriptato, senza Ollama) |

## Come funziona

- **Tracing**: ogni `POST /chat` apre un trace (span radice `chat_turn`); ogni
  chiamata LLM, tool e retrieval RAG crea uno span figlio con durata, attributi
  (modello, schema, tool, argomenti, documenti) e token. Senza trace attivo gli
  span sono **no-op**: l'osservabilità non deve mai rompere l'agente.
- **Token**: il client Ollama è condiviso e tracciato (`TrackedOllamaClient`):
  legge `prompt_eval_count`/`eval_count` dalla risposta (anche in streaming,
  dove sono nell'ultimo chunk) e li attribuisce allo span corrente. È la stessa
  fonte di conteggio dell'evaluator della Fase 9.
- **Logging**: ogni passo scrive una riga JSON in `logs/agent.jsonl`
  (con `session_id` + `turn_id` per la correlazione) e una riga umana in console.
- **Export**: a fine turno il trace viene salvato in
  `logs/traces/<session>_<turn>.json` (ispezione a posteriori).
- **Metrics**: contatori e istogrammi in-process (zero dipendenze esterne);
  in produzione si esporterebbero a Prometheus/Grafana, qui `GET /metrics`
  restituisce lo snapshot JSON con gli stessi dati.

## Avvio

Identico alla Fase 7/8 (CMMS su 8010, Ollama con `llama3.1` +
`nomic-embed-text`, indice Chroma già costruito): cambia solo il modulo dell'app:

```bash
uvicorn main9:app --reload --port 8003
```

## Endpoints

| Endpoint | Cosa restituisce |
|---|---|
| `POST /chat` | Risposta della Fase 7 + campo `observability` (riepilogo del turno) + `turn_id` |
| `GET /traces/{session_id}` | I trace della sessione: albero di span JSON + riepilogo + waterfall testuale |
| `GET /metrics` | Snapshot metriche: contatori, token, latenze p50/p95/max |
| `GET /sessions/{id}/history` · `DELETE /sessions/{id}` · `GET /health` | Invariati dalla Fase 7 |

Il campo `observability` della risposta `/chat` risponde alle 5 domande:

```json
{
  "turn_id": "c2b6a50d-284b-4eff-99f5-331cb5a91831",
  "duration_ms": 4821.3,
  "llm_calls": 5,
  "tokens": {"prompt": 1830, "completion": 412, "total": 2242},
  "tools_called": ["get_sensor_data", "search_manual"],
  "docs_retrieved": [{"file": "pump_manual.pdf", "page": 3}],
  "errors": []
}
```

E il waterfall (da `GET /traces/{session_id}`) mostra la sequenza esatta del
turno, con offset, durate, token ed errori:

```
       0ms  chat_turn  [4821.3ms]
       2ms  llm_call model=llama3.1 structured(AnalysisDecision) tokens=210+18 [812ms]
     815ms  tool_call tool=get_sensor_data args={'machine_id': 'P-102'} [4.1ms]
     820ms  llm_call model=llama3.1 structured(EvaluationDecision) tokens=480+22 [655ms]
    1476ms  tool_call tool=search_manual args={'query': 'temperatura cuscinetto P-102'} [1204ms]
    1477ms    rag_retrieval k=4 docs=2 [180ms]
    1658ms    llm_call model=llama3.1 rag_answer tokens=610+95 [1024ms]
    2681ms  llm_call model=llama3.1 text tokens=520+180 [2110ms]
    4792ms  llm_call model=llama3.1 structured(WriteProposal) tokens=310+12 [29ms]
```

## Note

- **L'osservabilità è best-effort**: i fallimenti di I/O (log, export trace)
  sono protetti e non propagati; senza trace attivo gli span sono no-op.
- **In-process**: i trace restano in memoria (tetto di 50 per sessione) più
  l'export JSON su disco. Per un'osservabilità centralizzata (Jaeger/Tempo/
  Grafana) il formato span (nome, offset, durata, attributi, token, errori)
  è direttamente mappabile.
- **`OBSERVABILITY_LOG_DIR`** sposta la cartella `logs/` (es. in un volume).
- **langchain-ollama >= 1.1**: il campo `client` e' stato rimosso (il client
  viene creato internamente in `_client` e il parametro `client=` e' ignorato
  in silenzio). Per questo `make_tracked_llm` e la pipeline RAG avvolgono il
  client interno con il proxy tracciato DOPO la costruzione: e' l'unico modo
  per contare i token anche sulle chiamate con output strutturato.
- **Test**: `pytest tests/test_unit_observability.py tests/test_integration_observability.py`
  (nessun prerequisito: LLM scriptato, CMMS in-process, RAG stub).

# FASE 11 - Docker

Stack completo in Docker: con un solo comando partono l'API CMMS, il
database PostgreSQL, il vector DB (server Chroma) e l'agente (Fase 10).

```
   docker compose up --build
```

```
┌────────────────────────────────────────────────────────────┐
│  agent (main9.py, Fase 10)          porta 8003            │
│  FastAPI + LangGraph + RAG + Observability                │
├────────────────────────────────────────────────────────────┤
│  cmms (main6.py)                    porta 8010            │
│  API REST CMMS + seed automatico all'avvio                │
├────────────────────────────────────────────────────────────┤
│  cmms-db (PostgreSQL 16)            porta 5432            │
│  dati CMMS, volume persistente (cmms_pgdata)              │
├────────────────────────────────────────────────────────────┤
│  chroma (server Chroma 1.5.9)         porta 8004 (host)   │
│  vector DB dei manuali, volume persistente (chroma_data)  │
├────────────────────────────────────────────────────────────┤
│  ingest (one-shot)                                        │
│  costruisce l'indice da docs/*.pdf (idempotente)          │
└────────────────────────────────────────────────────────────┘
        + Ollama (sull'host, o in Docker col profilo 'ollama')
```

### File

| File | Ruolo |
|---|---|
| `Dockerfile` | immagine unica per `cmms` e `agent` (stesso codice in `src/`, comando diverso) |
| `requirements-docker.txt` | dipendenze COMPLETE per l'immagine (requirements.txt è per l'env conda) |
| `docker-compose.yml` | i 5 servizi + i volumi + il profilo opzionale `ollama` |
| `.dockerignore` | cosa non mettere nell'immagine (DB locali, cache, test) |
| `src/rag.py`, `src/ingest.py`, `src/observability.py` | endpoint configurabili: `OLLAMA_BASE_URL`, `CHROMA_HOST`, `CHROMA_PORT`, `DOCS_DIR` |

### Avvio

```bash
# 1) Ollama deve essere raggiungibile dai container (vedi sotto)
# 2) Avvia tutto (la prima volta compie anche l'immagine)
docker compose up --build

# stato dei servizi
docker compose ps

# log in tempo reale
docker compose logs -f agent
```

Ordine di avvio (gestito da `depends_on`):
`cmms-db` (healthy) → `cmms` (seed + API) → `chroma` (healthy) →
`ingest` (one-shot, costruisce l'indice) → `agent` (parte solo dopo
l'ingest completato con successo).

### Ollama: due opzioni

**A. Ollama sull'host (default del compose).** I container lo raggiungono a
`http://host.docker.internal:11434`. Attenzione: `host.docker.internal`
punta all'IP di bridge dell'host, NON a 127.0.0.1 — quindi Ollama deve
ascoltare su tutte le interfacce:

```bash
OLLAMA_HOST=0.0.0.0 ollama serve
```

**B. Ollama in Docker (profilo `ollama`, tutto auto-contenuto).**

```bash
docker compose --profile ollama up --build
docker compose exec ollama ollama pull llama3.1
docker compose exec ollama ollama pull nomic-embed-text
# e imposta OLLAMA_BASE_URL=http://ollama:11434 (file .env o variabile d'ambiente)
```

### Verifica rapida

```bash
curl http://localhost:8010/health                          # CMMS (+ DB)
curl http://localhost:8004/api/v2/heartbeat                 # Chroma
curl http://localhost:8003/health                          # Agente (verifica anche il CMMS)

# turno completo (diagnosi + conferma work order, come nelle fasi precedenti)
curl -X POST http://localhost:8003/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "P-102 ha problemi di temperatura."}'
```

## Note

- **Porte sull'host**: 5432 (PostgreSQL), 8010 (CMMS), 8004 (Chroma;
  evito l'8000 usato dalle fasi precedenti), 8003 (agente), 11434
  (solo col profilo `ollama`). Tutte overridabili via `.env` nella
  cartella del compose (le porte interne dei container non cambiano):

  ```bash
  # es. se le porte standard sono occupate (port forward di VS Code,
  # altri servizi): crea un .env accanto a docker-compose.yml
  POSTGRES_PORT=5433
  CMMS_PORT=8011
  CHROMA_HOST_PORT=8006
  AGENT_PORT=8005
  # e il frontend punta al nuovo AGENT_PORT:
  #   AGENT_URL=http://localhost:8005 npm run dev
  ```
- **Persistenza**: `cmms_pgdata` (PostgreSQL), `chroma_data` (indice),
  `agent_logs` (log JSONL + export trace della Fase 10, leggibili con
  `docker compose cp agent:/data/logs .` o montando il volume in locale).
- **`ingest` è idempotente**: a ogni `docker compose up` ricrea la
  collection da zero (niente duplicati). Se fallisce (es. Ollama spento),
  l'agente non parte: correggi e rilancia `docker compose up`.
- **Memoria conversazionale**: default `MemorySaver` in RAM (le conferme
  pendenti si perdono al riavvio del container). Per persistere:
  `CHECKPOINT_DB=/data/checkpoints/memory.sqlite` + volume.
- **Codice modificato**: dopo i cambiamenti in `src/` serve
  `docker compose up --build` (l'immagine contiene il codice).
- **Reset completo**: `docker compose down -v` (cancella anche i volumi).

# FASE 12 - Frontend TypeScript (React + Vite)

Un frontend piccolo e mirato: una chat che parla con l'API dell'agente
(Fase 10, `src/main9.py`). Niente UI enorme: la chat mostra la risposta,
i tool chiamati (come step ✓), la proposta di work order con conferma
(Fase 7) e il riepilogo di osservabilità (Fase 10).

```
React / TypeScript (web/, Vite, porta 5173 in dev)
        |  POST /chat  (proxy di Vite -> 8003)
        v
FastAPI (src/main9.py, porta 8003)
        v
Agente (LangGraph + CMMS + RAG + Observability)
```

## File

| File | Ruolo |
|---|---|
| `web/package.json` | dipendenze (React 18 + Vite 6 + TypeScript) |
| `web/vite.config.ts` | dev server (5173) + proxy `/chat`, `/health`, `/sessions`, ... -> agente (default `http://localhost:8003`, overridabile con `AGENT_URL`) |
| `web/src/types.ts` | tipi TS che specchiano il contratto di `main9.py` (`ChatResponse`, `ToolCall`, `ObservabilitySummary`, `PendingAction`) |
| `web/src/api.ts` | client HTTP (`sendChat`, `health`, `deleteSession`) |
| `web/src/App.tsx` | la chat: messaggi, sessione, invio, new session |
| `web/src/components/ToolSteps.tsx` | gli step ✓ ("Checked machine status", "Retrieved sensor data", ...) dai `gathered_data` |
| `web/src/components/ConfirmationCard.tsx` | la proposta di work order (Fase 7) con pulsanti Confirm/Deny |
| `web/src/components/Observability.tsx` | riepilogo del turno (Fase 10): durata, token, LLM calls, docs, errori |
| `web/src/App.css` | tema scuro |

Unica modifica al backend: `src/main9.py` abilita il **CORS**
(`CORSMiddleware`). In dev non serve (il proxy di Vite inietta le
chiamate "dalla stessa origine"); serve quando l'app buildata viene
servita da un'origine diversa.

## Avvio (dev)

Prerequisito: l'agente acceso sulla 8003 (es. `docker compose up` della
Fase 11, oppure `uvicorn main9:app --port 8003` con CMMS e Ollama attivi).

```bash
cd web
npm install
npm run dev
```

Apri http://localhost:5173. Le chiamate API partono "dalla stessa
origine" (il proxy di Vite le inietta a `http://localhost:8003`):
nessun CORS da gestire.

Se l'agente non e' sulla 8003:

```bash
AGENT_URL=http://localhost:8099 npm run dev
```

## Cosa mostra la UI

- **Chat**: messaggi del tecnico e dell'agente; il `session_id` e'
  gestito dal frontend (campo "session ..." nell'header) e rimandato a
  ogni messaggio; "New session" cancella la sessione (`DELETE
  /sessions/{id}`) e riparte da zero.
- **Step dei tool**: per ogni risposta, la lista dei tool chiamati
  (da `gathered_data`) con etichetta leggibile e argomento
  significativo (ID macchina o query):
  `✓ Checked machine status · P-102`, `✓ Searched maintenance manual · "P-102 bearing temperature high cause"`.
- **Conferma work order (Fase 7)**: se `confirmation_required` e' true,
  appare la card con macchina/descrizione/priorita' e i pulsanti
  **Confirm** / **Deny**: inviano rispettivamente "Sì, confermo." /
  "No, non ora." (le frasi che `interpret_confirmation` dell'agente
  gestisce). Si puo' anche ignorare la proposta e scrivere un'altra
  domanda: l'agente la annulla in fail-safe (come da Fase 7).
- **Osservabilità (Fase 10)**: sotto ogni risposta, un `<details>` con
  durata, token, chiamate LLM, documenti recuperati ed errori (da
  `observability`).
- **Stato CMMS**: badge nell'header (`GET /health`).

## Build di produzione

```bash
cd web
npm run build
# -> web/dist/ (statico: index.html + asset)
```

`web/dist` si serve con qualsiasi server statico (nginx, `python -m
http.server`, ...). Le chiamate API usano `VITE_API_BASE` (vuoto in
dev): per puntare l'app buildata a un agente su un'altra origine,
ricostruire con:

```bash  
VITE_API_BASE=http://localhost:8003 npm run build
```

(serve il CORS abilitato sull'agente: gia' fatto in `main9.py`).

## Note

- **Niente state server-side nel frontend**: la memoria conversazionale
  vive nell'agente (checkpointer, Fase 5/7); il frontend tiene solo il
  `session_id` e la cronologia dei messaggi visualizzata.
- **Il frontend non parla mai col CMMS**: vede solo l'API dell'agente
  (stesso principio della Fase 6: l'agente e' l'unico punto di
  contatto).
- **Docker**: il frontend NON e' containerizzato in `docker-compose.yml`
  (resta un'app di sviluppo su `npm run dev`). Se serve, e' un servizio
  nginx in più con `web/dist` montato.

# FASE 13 - CI/CD (GitHub Actions)

```
git push
   |
   v
GitHub Actions (.github/workflows/ci.yml)
   |
   v
Lint            ruff (Python) + eslint/tsc (TypeScript)
   |
   v
Unit tests      pytest tests/test_unit_*.py + test_eval_metrics.py
   |
   v
Integration     pytest tests/test_integration_*.py + test_agent_dataset.py
   |
   v
Build Docker    buildx + smoke test (l'app parte dentro l'immagine)
   |
   v
Security        Trivy (CVE immagine) + pip-audit (dipendenze Python)
   |
   v
Deploy          runner self-hosted -> deploy/deploy.sh (solo su main, se tutto e' verde)
```

## Perché ogni fase esiste

Ogni gate blocca una classe diversa di difetto, al costo più basso possibile:

| Fase | Cosa blocca | Perché esiste |
|---|---|---|
| **Lint** | Bug banali (variabili inutilizzate, import morti, typo) + stile incoerente | È la gate più economica: secondi, zero infrastruttura. Cattura una classe di errori PRIMA di far girare i test (che costano di più) e mantiene il codice leggibile. |
| **Unit tests** | Regressioni logiche nei singoli componenti (client CMMS, API CMMS, RAG, osservabilità) | I test sono ermetici (niente PostgreSQL, Ollama né porte: CMMS in-process, LLM scriptato): veloci e deterministici, e localizzano il guasto al componente. |
| **Integration tests** | Le "cuciture": i test unit possono essere tutti verdi mentre il contratto Agent → Tool → CMMS, la serializzazione HTTP o il flusso HITL sono rotti | Verifica la catena COMPLETA (HTTP reale verso il CMMS in-process, LLM scriptato): è il livello che corrisponde a "l'agente funziona davvero". |
| **Build Docker** | Un cambio che rompe l'IMMAGINE (dipendenza mancante in `requirements-docker.txt`, Python incompatibile) pur avendo i test verdi | I test girano in un venv, non nell'immagine: senza questa fase un deploy potrebbe fallire solo sul server. Lo smoke test (l'app parte dentro l'immagine) copre anche i guasti di wiring. |
| **Security check** | Dipendenze con CVE note (le dipendenze "derivano": una versione nuova può portare vulnerabilità note) | Trivy scansiona l'immagine (pacchetti OS + Python), pip-audit le dipendenze Python. HIGH/CRITICAL bloccano il deploy. Le eccezioni sono esplicite e documentate (allowlist `--ignore-vuln` con motivazione nel workflow). |
| **Deploy** | Niente: è l'azione, non un gate | Esegue solo su `main` e solo se `needs: [docker-build, security]` è verde. Sul server: `git pull` + `docker compose up --build` + **health check** (verifica l'ESITO: lo stack deve rispondere, non basta che il comando esca con 0). |

## File

| File | Ruolo |
|---|---|
| `.github/workflows/ci.yml` | La pipeline (6 job, con il "perché" di ciascuno in commento) |
| `ruff.toml` | Config lint Python (set conservativo: E4, E7, E9, F) |
| `web/eslint.config.js` | Config lint TypeScript/React (ESLint 9 flat + rules of hooks) |
| `deploy/deploy.sh` | Script di deploy eseguito SULLO SERVER dalla CI (git pull + compose + health check) |
| `web/package-lock.json` | Lockfile per `npm ci` riproducibile in CI |

## Setup una tantum

1. **Repo GitHub**: crea il repo e collega questo directory:
   ```bash
   git remote add origin git@github.com:<utente>/industrial-maintenance-agent.git
   git push -u origin main
   ```
   La CI parte a ogni push (e su ogni PR).

2. **Runner self-hosted SUL SERVER**: il job `deploy` gira su un runner
   installato sul server (i runner pubblici di GitHub non raggiungono la
   LAN, quindi niente SSH). Installazione e comandi di avvio: sezione
   **Runner self-hosted sul server** qui sotto.

3. **Sul server**: la cartella del progetto deve essere un repo git con
   l'origin GitHub (`git remote -v`), perché `deploy.sh` fa `git pull`.
   L'utente del runner deve poter eseguire `docker compose` (gruppo docker).

## Runner self-hosted sul server (deploy)

Il job `deploy` gira su un **runner self-hosted** installato sul server:
i runner pubblici di GitHub (cloud) non raggiungono la rete LAN
(10.0.40.x), quindi il deploy è locale — il runner esegue
`deploy/deploy.sh` direttamente sul server, senza SSH.

### Installazione (una tantum)

1. **Registra il runner su GitHub**: repo → Settings → Actions → Runners →
   *New self-hosted runner* → Linux / x64. GitHub mostra il comando
   `config.sh` con un **token** (valido solo per la registrazione).
2. **Sul server**, come l'utente che eseguirà il runner (es. `gstasio`):
   ```bash
   cd ~
   curl -O -L https://github.com/actions/runner/releases/download/v2.337.0/actions-runner-linux-x64-2.337.0.tar.gz
   mkdir -p actions-runner
   tar -xzf actions-runner-linux-x64-2.337.0.tar.gz -C actions-runner
   cd actions-runner
   ./config.sh --url https://github.com:<utente>/industrial-maintenance-agent --token <TOKEN>
   sudo ./svc.sh install gstasio    # crea il servizio systemd
   sudo ./svc.sh start
   ```
   Il runner deve comparire come **online** in GitHub → Settings →
   Actions → Runners.

Prerequisiti:

- l'utente del runner deve poter eseguire `docker compose` (gruppo docker)
  e `git pull` nella cartella del progetto
  (`~/Progetti/industrial-maintenance-agent`);
- il server deve avere accesso internet in uscita verso GitHub (il runner
  "polla" i job ogni pochi secondi).

### Avvio / arresto / verifica

Il runner è un servizio systemd chiamato `actions.runner.<nome-runner>`
(il nome è quello dato in `config.sh`):

```bash
sudo systemctl status  actions.runner.<nome-runner>   # è online?
sudo systemctl start   actions.runner.<nome-runner>   # avvia
sudo systemctl stop    actions.runner.<nome-runner>   # arresta
sudo systemctl enable  actions.runner.<nome-runner>   # riparte al boot
sudo journalctl -u actions.runner.<nome-runner> -f    # log in tempo reale
```

**Troubleshooting**: se il job `deploy` resta in coda "Waiting for a
runner", il runner è offline: controlla `systemctl status` (tipico dopo
un reboot del server) e che il server abbia accesso internet.
Per aggiornare il runner: scarica il tarball della nuova versione,
`./svc.sh stop`, estrai nella stessa cartella, `./svc.sh start`.

## Note

- **Deploy**: solo su push a `main` (i PR fanno tutte le verifiche ma non
  deployano). `environment: production` permette di aggiungere un'approvazione
  manuale (GitHub → Settings → Environments → `production` → Required reviewers).
- **Allowlist sicurezza**: `chromadb 1.5.9` (l'ultima disponibile) ha 4 CVE
  lato server senza fix upstream (CVE-2026-45829/45830/45831/45833). Rischio
  accettato e documentato nel workflow: il server Chroma gira solo in rete
  interna Docker, non è esposto verso l'esterno. Quando uscirà una versione
  fixata: si alza la dipendenza e si toglie l'allowlist.
- **Costo**: i job di verifica usano runner GitHub gratuiti (ubuntu-latest);
  il job `deploy` gira sul runner self-hosted (zero minuti GitHub). La
  pipeline completa è ~5-8 minuti (build Docker inclusa).
- **Evoluzione naturale** (fuori scope): registry (GHCR) per build-una-volta/
  deploy-artefatto, image signing, canary deploy, e notifica su canale team a
  deploy fallito.

# FASE 15 - Kubernetes

Ultima parte.

Non perché devi necessariamente usarlo nel progetto finale.

Impariamo gli oggetti Kubernetes e facciamo un deployment locale dello
stack con **kind** (un cluster Kubernetes *vero* dentro un container
Docker): gli stessi manifest, senza modifiche, gireranno poi su EKS.

## Perché Kubernetes (e perché kind)

docker-compose risponde a "come faccio a far girare questo stack su
**una** macchina?". Kubernetes risponde a "come lo faccio girare su
**N** macchine, in modo che si aggiusti da solo, scala e si deploya
senza downtime?".

| | docker-compose | Kubernetes |
|---|---|---|
| Unità di deploy | container | Pod (1+ container) |
| Riavvio su guasto | `restart: always` | Deployment (controller) |
| Scaling | manuale | HPA (automatico) |
| Service discovery | rete fissa di compose | Service (DNS stabile) |
| Multi-nodo | no | sì (è il punto) |

**kind** crea un cluster k8s reale in cui il nodo è un container
Docker: niente VM, niente cloud, gratis, si butta via in un comando.
**minikube** fa la stessa cosa con una VM completa (più pesante, più
vicino a una macchina "vera"). Per imparare: kind.

## Gli oggetti (mappati sul nostro stack)

| Oggetto | Cos'è | Perché esiste | Nel nostro stack |
|---|---|---|---|
| **Pod** | L'unità minima: 1+ container che condividono rete e storage | È il "container" di k8s, ma con vita effimera: muore e risorge | cmms, agent, chroma, postgres girano ciascuno in un pod |
| **Deployment** | Mantiene N repliche di un pod SEMPRE attive: rollout, rollback, riavvio su guasto | Il self-healing di base | cmms (1), agent (2), chroma (1), cmms-db (1) |
| **Service** | DNS stabile + bilanciamento del carico sui pod | I pod muoiono e risalgono con IP diversi: il Service no | `http://cmms:8010`, `http://chroma:8000` |
| **ConfigMap** | Config NON segreta come variabili d'ambiente | Non si "cuce" la config nell'immagine | `CMMS_BASE_URL`, `CHROMA_HOST`, `OLLAMA_BASE_URL`… |
| **Secret** | Dati sensibili (base64, NON cifrati a riposo) | Le password non stanno nell'immagine né nel repo | credenziali DB, `CMMS_API_KEY` |
| **Job** | Un pod che deve **FINIRE** (one-shot) | Non tutto è un servizio eterno | `ingest` (costruisce l'indice Chroma) |
| **Ingress** | Punto d'ingresso HTTP: instrada per host/path sui Service | Una porta sola per tutti i servizi | `cmms.im.local`, `agent.im.local` |
| **Health checks** | readiness (traffico solo quando sei pronto) / liveness (riavvio se sei wedged) | Self-healing senza umani | `/health` su cmms e agent, TCP su chroma, `pg_isready` su postgres |
| **HPA** | Regola le repliche in base alla CPU | Scaling senza cron né script | agent: 2→5 repliche |

## File

| File | Ruolo |
|---|---|
| `k8s/kind.yaml` | config del cluster kind (NodePort → porte dell'host) |
| `k8s/namespace.yaml` | namespace `im-agent` |
| `k8s/configmap.yaml` | variabili d'ambiente non segrete |
| `k8s/secret.yaml` | credenziali DB + API key |
| `k8s/cmms-db.yaml` | PostgreSQL: PVC + Deployment + Service |
| `k8s/chroma.yaml` | Chroma: PVC + Deployment + Service |
| `k8s/cmms.yaml` | CMMS: Deployment + Service (NodePort) |
| `k8s/agent.yaml` | Agente: Deployment + Service (NodePort) |
| `k8s/ingest.yaml` | Job one-shot (l'equivalente k8s del service `ingest` di compose) |
| `k8s/ingress.yaml` | Ingress (host-based, gestito da cloud-provider-kind) |
| `k8s/hpa-agent.yaml` | HPA dell'agente |
| `k8s/scripts/00-install-tools.sh` | installa kubectl + kind (binari, non pip) |
| `k8s/scripts/01…04` | cluster → deploy → test → cleanup |

## Setup: cluster kind

Prerequisiti: `docker` (già c'è: lo usa lo stack), `kind` e `kubectl`
(da installare: **non** sono pacchetti pip, sono binari), e Ollama in
ascolto su tutte le interfacce (`OLLAMA_HOST=0.0.0.0 ollama serve`):
i pod lo raggiungono via IP della LAN (`OLLAMA_BASE_URL` in
`configmap.yaml`).

```bash
cd k8s
./scripts/00-install-tools.sh    # kubectl + kind (binari ufficiali)
./scripts/01-create-cluster.sh
```

Cosa fa, passo per passo (ogni passo è un concetto):

1. `kind create cluster` — il cluster (il nodo è un container)
2. `docker build` + `kind load docker-image` — l'immagine dello stack
   (la stessa della CI) entra nel registry del cluster
3. **cloud-provider-kind** — il "portale" Ingress/LoadBalancer: da kind
   v0.33 l'addon ingress-nginx non esiste più, l'Ingress è nativo e viene
   fornito da questo binario (che lo script avvia in background sull'host)
4. addon **metrics-server** (+ `--kubelet-insecure-tls`, workaround
   documentato per kind) — le metriche CPU che l'HPA legge

## Deploy

```bash
./scripts/02-deploy.sh
```

k8s non ha `depends_on`: è dichiarativo (descrivi LO STATO, non
l'ordine). Quindi l'ordine lo mette lo script, con `kubectl wait` tra
un passo e l'altro:

```
namespace + config + secret
  -> cmms-db + chroma   (i dati)
  -> cmms               (serve il DB)
  -> ingest             (serve chroma + Ollama)
  -> agent              (serve cmms + l'indice)
  -> ingress + HPA
```

## Test

```bash
./scripts/03-test.sh
```

Due modi di raggiungere i servizi:

- **NodePort** (già mappati in `kind.yaml`): `http://localhost:18010/health`
  (CMMS), `http://localhost:18003/health` (agente). Porte 18xxx: 8010/8003
  le tiene lo stack docker-compose, che gira sullo stesso server.
- **Ingress** (gestito da cloud-provider-kind): l'Ingress ha un IP esterno
  (rete kind, `kubectl -n im-agent get ingress`); si instrada per header
  Host: `curl -H "Host: cmms.im.local" http://<IP>/health`

## Scaling (HPA)

```bash
# guarda l'HPA al lavoro (colonna REPLICAS)
kubectl -n im-agent get hpa -w

# genera carico
for i in $(seq 1 50); do curl -s http://localhost:18003/health > /dev/null & done

# guarda le repliche salire (2 -> 3 -> ...)
kubectl -n im-agent get pods -l app=agent
```

Nota: l'HPA scala solo se il metrics-server è attivo (passo 4 dello
script 01); altrimenti resta fermo al minimo.

## Cleanup

```bash
./scripts/04-cleanup.sh    # kind delete cluster: via tutto in un comando
```

## Da kind a EKS

I manifest NON cambiano (k8s è k8s): cambia l'infrastruttura intorno.

| In kind | In EKS |
|---|---|
| `kind load docker-image` | ECR (registry) + `imagePullSecrets` |
| Secret in chiaro nel repo | AWS Secrets Manager + ExternalSecrets |
| PVC con StorageClass standard (disco del nodo) | EBS (gp3) via EBS CSI driver |
| cloud-provider-kind (Ingress nativo) | ALB Ingress Controller |
| PostgreSQL in un pod | RDS (DB gestito) |
| metrics-server da installare | già incluso in EKS |

Il percorso: kind (gratis, throwaway, per imparare) → EKS (pagato,
production-ready) con gli stessi YAML.
