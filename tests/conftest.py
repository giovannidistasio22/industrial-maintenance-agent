"""
Fixture condivise dei test.

Principi:
  - Nessuna dipendenza da servizi esterni: niente PostgreSQL, niente Ollama,
    niente porte aperte.
  - Il CMMS gira su SQLite in-memory (cmms/database/db.py legge DATABASE_URL
    all'import) e l'API CMMS è raggiunta via HTTP reale (uvicorn in un thread
    su una porta locale): la catena Agent -> Tool -> CMMS è reale, senza
    PostgreSQL e senza porte note.
  - L'LLM dell'agente è "scriptato" (ScriptedLLM): i test verificano
    l'orchestrazione (quali tool, in che ordine, con che argomenti),
    non la qualità del modello.
"""

import os
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Deve essere impostato PRIMA dell'import di db (che legge la variabile all'import).
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

# backend/rag/pipeline.py usa il percorso relativo "chroma_db": se esiste
# l'indice in radice, puntalo lì anche quando pytest parte da tests/.
# CHROMA_DB_OVERRIDE permette di indicare un altro indice (es. nei test CI).
_chroma_override = os.environ.get("CHROMA_DB_OVERRIDE")
if _chroma_override or (ROOT / "chroma_db").exists():
    from backend.rag import pipeline
    pipeline.PERSIST_DIR = _chroma_override or str(ROOT / "chroma_db")


# ---------------------------------------------------------------------------
# Isolamento osservabilità (Fase 10): nessun test sporca logs/ del progetto
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _hermetic_observability(monkeypatch, tmp_path):
    """Log e export trace in una cartella temporanea per ogni test."""
    from backend.services import observability

    monkeypatch.setattr(observability, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(observability, "LOG_FILE", tmp_path / "logs" / "agent.jsonl")
    monkeypatch.setattr(observability, "TRACE_DIR", tmp_path / "logs" / "traces")


# ---------------------------------------------------------------------------
# CMMS (API + DB)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def cmms_app():
    """L'API CMMS con il DB SQLite in-memory creato e popolato (seed)."""
    from cmms.main import app
    from cmms.database import seed
    seed.seed()
    return app


@pytest.fixture(scope="session")
def cmms_port(cmms_app):
    """Avvia l'API CMMS su una porta locale (uvicorn in un thread): la catena
    Agent -> Tool -> CMMS usa HTTP reale, senza PostgreSQL e senza porte note."""
    import socket
    import threading
    import time

    import httpx
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = uvicorn.Server(uvicorn.Config(cmms_app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # attendi il readiness
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=1) as probe:
        for _ in range(100):
            try:
                if probe.get("/health").status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("L'API CMMS non è partita in tempo")

    yield port

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def cmms_http(monkeypatch, cmms_port):
    """Punta cmms_client verso l'API CMMS avviata da cmms_port."""
    import httpx
    from backend.services import cmms_client

    client = httpx.Client(
        base_url=f"http://127.0.0.1:{cmms_port}",
        headers={"X-API-Key": cmms_client.API_KEY},
        timeout=cmms_client.TIMEOUT,
    )
    monkeypatch.setattr(cmms_client, "_client", client)
    return client


@pytest.fixture()
def clean_work_orders():
    """Fa partire il test dallo stato seed: elimina i work order creati dai test
    precedenti (i seed hanno id espliciti <= 1042; i nuovi partono da 1043)."""
    from cmms.database import db
    from cmms.models.models import WorkOrder
    from sqlalchemy import delete

    with db.SessionLocal() as s:
        s.execute(delete(WorkOrder).where(WorkOrder.id > 1042))
        s.commit()
    yield


# ---------------------------------------------------------------------------
# App dell'agente con CMMS in-process e RAG stub
# ---------------------------------------------------------------------------

class _FakeRag:
    """RAG stub: i test non dipendono dall'indice vettoriale né da Ollama."""

    def answer(self, query):
        return (
            "Il manuale indica di controllare la lubrificazione e la linea di raffreddamento.",
            [{"file": "pump_manual.pdf", "page": 0}],
        )


@pytest.fixture()
def agent_client(monkeypatch, cmms_http):
    """L'app dell'agente (/chat) con il CMMS in-process e il RAG stub.

    L'LLM va scriptato per test con make_scripted_llm(case).
    """
    from backend.main import app
    from backend.tools import registry
    from fastapi.testclient import TestClient

    monkeypatch.setattr(registry, "_rag_pipeline", _FakeRag())
    return TestClient(app)

# ---------------------------------------------------------------------------
# LLM scriptato (per i test dell'agente, senza Ollama)
# ---------------------------------------------------------------------------

class ScriptedLLM:
    """Sostituto deterministico di nodes._llm.

    Risponde alle richieste strutturate del grafo in base al caso del dataset:
      - analyze_query  -> il primo tool di expected_tools (o "niente" se vuota)
      - evaluate_data  -> il tool successivo, finché expected_tools non si esaurisce
      - generate_answer -> la risposta preconfezionata del caso (canned_answer)
      - propose_write_action -> propone solo se expected_behavior lo richiede

    I messaggi di conferma espliciti ("Sì, confermo." / "No, non ora.") sono
    gestiti dai filtri deterministici di nodes.interpret_confirmation: il ramo
    ConfirmationInterpretation qui sotto viene toccato solo da messaggi ambigui.
    """

    def __init__(self, case: dict):
        self.case = case
        self.tools = list(case.get("expected_tools", []))
        self.machine = case.get("machine")
        self._assigned = 0
        self.text_answers = [case.get("canned_answer") or "Risposta di test."]
        self.structured_calls = []
        self.text_calls = []

    def _tool_args(self, tool: str) -> dict:
        if tool == "search_manual":
            return {"query": self.case["question"]}
        return {"machine_id": self.machine} if self.machine else {}

    def with_structured_output(self, schema):
        name = getattr(schema, "__name__", str(schema))

        def _call(prompt):
            self.structured_calls.append(name)
            from backend.agent import nodes  # import qui dentro: non serve ai test che non usano l'agente

            if name == "AnalysisDecision":
                if self.tools:
                    return nodes.AnalysisDecision(
                        needs_information=True,
                        tool_name=self.tools[0],
                        tool_args=self._tool_args(self.tools[0]),
                        reasoning="scripted",
                    )
                return nodes.AnalysisDecision(needs_information=False, reasoning="scripted")

            if name == "EvaluationDecision":
                self._assigned += 1
                if self._assigned < len(self.tools):
                    nxt = self.tools[self._assigned]
                    return nodes.EvaluationDecision(
                        sufficient=False,
                        next_tool_name=nxt,
                        next_tool_args=self._tool_args(nxt),
                        reasoning="scripted",
                    )
                return nodes.EvaluationDecision(sufficient=True, reasoning="scripted")

            if name == "WriteProposal":
                propose = self.case.get("expected_behavior") in ("propose_work_order", "propose_and_deny")
                return nodes.WriteProposal(
                    should_propose=propose,
                    description="Anomalia rilevata su macchina da verificare.",
                    priority="medium",
                    reasoning="scripted",
                )

            if name == "ContextResolution":
                return nodes.ContextResolution(
                    standalone_query="Domanda autonoma di test.",
                    machine_id=self.machine,
                )

            if name == "ConfirmationInterpretation":
                # Messaggio ambiguo: fail-safe, non si esegue nulla.
                return nodes.ConfirmationInterpretation(
                    intent="unrelated", reasoning="scripted: messaggio ambiguo"
                )

            raise AssertionError(f"Schema strutturato inatteso nei test: {name}")

        class _Structured:
            """I nodi chiamano _llm.with_structured_output(X).invoke(prompt)."""

            def invoke(self, prompt):
                return _call(prompt)

        return _Structured()

    def invoke(self, prompt):
        self.text_calls.append(prompt)
        content = self.text_answers.pop(0) if self.text_answers else "Risposta generica di test."
        return AIMessage(content=content)


@pytest.fixture()
def make_scripted_llm(monkeypatch):
    """Crea uno ScriptedLLM per un caso del dataset e lo applica a nodes._llm."""
    from backend.agent import nodes

    def _make(case: dict) -> ScriptedLLM:
        llm = ScriptedLLM(case)
        monkeypatch.setattr(nodes, "_llm", llm)
        return llm

    return _make
