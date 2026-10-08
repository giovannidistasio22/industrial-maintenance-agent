"""
LLM Evaluation: harness di evaluation quantitativa dell'agente.

Misura, per ogni domanda del dataset:
  - retrieval quality   (hit@k, MRR, context relevance del giudice)
  - answer relevance     (giudice LLM, 0-1)
  - faithfulness         (decomposizione in claim: claim supportati / totali)
  - tool selection       (precision/recall/F1 dei tool chiamati vs attesi)
  - hallucination        (presenza di claim non supportati dal contesto)
  - latency              (ms per domanda, p50/p95)
  - token usage          (prompt + completion token di tutte le chiamate LLM)

Architettura: la catena è REALE (agente -> tool -> CMMS in-process -> SQLite,
RAG -> Chroma, LLM -> Ollama). Solo il DB è in-memory, per essere
deterministici senza PostgreSQL.

Uso:
  # RAG v1 (pipeline attuale: k=4, indice chroma_db, prompt standard)
  python tests/evaluation/evaluator.py --variant v1

  # RAG v2 (config custom: k, prompt, indice, modello)
  python tests/evaluation/evaluator.py --variant v2 --rag-config data/evaluation/rag_v2.json

  # Confronto v1 vs v2 (report quantitativo dell'effetto delle modifiche)
  python tests/evaluation/evaluator.py --compare data/evaluation/results/v1.json data/evaluation/results/v2.json

Opzioni:
  --dataset PATH            dataset JSON (default data/evaluation/eval_dataset.json)
  --index PATH              indice Chroma (default chroma_db)
  --limit N                 solo i primi N casi (run veloce)
  --categories a,b,c        solo queste categorie
  --no-judge                solo metriche deterministiche (veloce, senza giudice)
  --judge-model NAME        modello del giudice (default llama3.1)
  --judge-provider ollama|openai
  --resume                  riprende da un report esistente (salta i casi già fatti)
  --workers N               casi in parallelo (default 1; >1 è sperimentale)
"""

import argparse
import json
import math
import os
import socket
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "data" / "evaluation" / "results"
sys.path.insert(0, str(ROOT))

# Il CMMS gira su SQLite in-memory: va impostato PRIMA dell'import di db.py.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

# Prompt standard della pipeline RAG attuale (rag.py): è il baseline "v1".
DEFAULT_RAG_PROMPT = """Sei un assistente tecnico per manutenzione industriale.
Rispondi alla domanda dell'utente usando SOLO le informazioni contenute nel contesto qui sotto.
Se il contesto non contiene informazioni sufficienti, dillo chiaramente invece di inventare.

Contesto:
{context}

Domanda: {question}

Risposta:"""


# ---------------------------------------------------------------------------
# Token counting (a livello client Ollama: copre TUTTE le chiamate, anche
# l'output strutturato, che perde l'usage_metadata sul risultato Pydantic)
# ---------------------------------------------------------------------------

class TokenCounter:
    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0

    def add(self, prompt: int, completion: int):
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.calls += 1

    def reset(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0

    def snapshot(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "n_llm_calls": self.calls,
        }


class CountingOllamaClient:
    """Proxy di ollama.Client: conta i token di ogni chiamata chat.

    Ollama restituisce prompt_eval_count/eval_count in ogni risposta: è la
    fonte più affidabile (funziona anche per with_structured_output).
    """

    def __init__(self, real_client, counter: TokenCounter):
        self._real = real_client
        self._counter = counter

    def chat(self, *args, **kwargs):
        response = self._real.chat(*args, **kwargs)
        self._counter.add(
            int(getattr(response, "prompt_eval_count", 0) or 0),
            int(getattr(response, "eval_count", 0) or 0),
        )
        return response

    def __getattr__(self, name):
        return getattr(self._real, name)


# ---------------------------------------------------------------------------
# RAG configurabile (per il confronto v1/v2) + spy di retrieval
# ---------------------------------------------------------------------------

class SpyRetriever:
    """Avvolge il retriever reale e registra i documenti recuperati
    (per le metriche di retrieval quality) senza cambiare il comportamento."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = []

    def invoke(self, query):
        docs = self._inner.invoke(query)
        self.calls.append({
            "query": query,
            "docs": [
                {
                    "content": d.page_content,
                    "source": d.metadata.get("source"),
                    "page": d.metadata.get("page"),
                }
                for d in docs
            ],
        })
        return docs


class ConfigurableRag:
    """Pipeline RAG con parametri configurabili (k, prompt, modello, indice).

    Stesso contratto di rag.RagPipeline.answer(): restituisce (answer, sources).
    """

    def __init__(self, cfg: dict, index_dir: str, counter: TokenCounter):
        import ollama
        from langchain_ollama import ChatOllama, OllamaEmbeddings
        from langchain_chroma import Chroma
        from langchain_core.prompts import ChatPromptTemplate

        self.k = int(cfg.get("k", 4))
        persist = str(Path(cfg.get("persist_dir") or index_dir).resolve())
        self.persist_dir = persist
        embeddings = OllamaEmbeddings(model=cfg.get("embedding_model", "nomic-embed-text"))
        self.vectorstore = Chroma(persist_directory=persist, embedding_function=embeddings)
        self.retriever = SpyRetriever(self.vectorstore.as_retriever(search_kwargs={"k": self.k}))
        self.llm = ChatOllama(
            model=cfg.get("llm_model", "llama3.1"),
            temperature=0,
            client=CountingOllamaClient(ollama.Client(), counter),
        )
        self.prompt = ChatPromptTemplate.from_template(cfg.get("prompt", DEFAULT_RAG_PROMPT))

    def answer(self, question: str):
        docs = self.retriever.invoke(question)
        context = "\n\n---\n\n".join(d.page_content for d in docs)
        response = (self.prompt | self.llm).invoke({"context": context, "question": question})
        sources, seen = [], set()
        for d in docs:
            src = d.metadata.get("source", "unknown")
            page = d.metadata.get("page")
            key = (src, page)
            if key not in seen:
                seen.add(key)
                sources.append({"file": src, "page": page})
        return response.content, sources


# ---------------------------------------------------------------------------
# Setup: CMMS in-process + agente + LLM con contatore
# ---------------------------------------------------------------------------

def _start_cmms(workers: int):
    """Avvia l'API CMMS su una porta locale in un thread.

    Con workers>1 usa un DB SQLite su file (più connessioni possibili),
    altrimenti in-memory (una connessione, come nei test).
    """
    if workers > 1:
        import tempfile
        db_file = os.path.join(tempfile.gettempdir(), f"cmms_eval_{int(time.time())}.db")
        os.environ["DATABASE_URL"] = f"sqlite:///{db_file}"

    from cmms.main import app as cmms_app
    from cmms.database import seed

    seed.seed()

    import httpx
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = uvicorn.Server(uvicorn.Config(cmms_app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

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

    return port, server


def _point_cmms_client(port: int):
    import httpx
    from backend.services import cmms_client

    cmms_client._client = httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        headers={"X-API-Key": cmms_client.API_KEY},
        timeout=cmms_client.TIMEOUT,
    )


def _check_ollama(required_models):
    import httpx

    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=5)
        models = [m.get("name", "") for m in r.json().get("models", [])]
    except Exception as exc:
        raise SystemExit(
            f"Ollama non raggiungibile su http://localhost:11434: {exc}\n"
            "Avvia Ollama e scarica i modelli: ollama pull llama3.1 && ollama pull nomic-embed-text"
        )
    missing = [m for m in required_models if not any(m in name for name in models)]
    if missing:
        raise SystemExit(
            f"Modelli Ollama mancanti: {missing}. Installa con: "
            + " && ".join(f"ollama pull {m}" for m in missing)
        )


def _build_agent(index_dir: str, rag_cfg: dict, counter: TokenCounter, agent_model: str = "llama3.1"):
    """Importa la catena agente (backend.main) e la prepara per l'evaluation:
    LLM con contatore di token, RAG configurabile con spy di retrieval."""
    import ollama
    from langchain_ollama import ChatOllama

    from backend.rag import pipeline as rag
    rag.PERSIST_DIR = index_dir  # il RagPipeline di default (instantiato dal registry) non serve, ma non deve fallire

    from backend.agent import nodes
    from backend.tools import registry as tools2
    from backend.main import app as main8

    # LLM dell'agente (analyze/evaluate/generate/resolve/propose) con contatore
    nodes._llm = ChatOllama(
        model=agent_model,
        temperature=0,
        client=CountingOllamaClient(ollama.Client(), counter),
    )

    # RAG configurabile iniettato nel tool search_manual
    pipeline = ConfigurableRag(rag_cfg, index_dir, counter)
    tools2._rag_pipeline = pipeline

    from fastapi.testclient import TestClient
    return TestClient(main8.app), pipeline


# ---------------------------------------------------------------------------
# Metriche deterministiche
# ---------------------------------------------------------------------------

def _set_metrics(called: list, expected: list):
    """Precision/recall/F1 dei tool chiamati vs attesi (base a insiemi)."""
    called_set, expected_set = set(called), set(expected)
    if not called_set and not expected_set:
        return 1.0, 1.0, 1.0  # non servivano tool e non ne sono stati chiamati
    if not called_set:
        precision, recall = 1.0, 0.0  # nessun tool chiamato: recall zero
    elif not expected_set:
        precision, recall = 0.0, 1.0  # tool chiamati ma nessuno era atteso
    else:
        tp = len(called_set & expected_set)
        precision = tp / len(called_set)
        recall = tp / len(expected_set)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return round(precision, 4), round(recall, 4), round(f1, 4)


def _retrieval_metrics(retrieved_docs: list, expected_sources: list):
    """hit@k e MRR rispetto ai file attesi (expected_sources)."""
    if not expected_sources:
        return None, None, []
    files = []
    for call in retrieved_docs:
        for doc in call["docs"]:
            src = Path(doc["source"] or "").name
            if src and src not in files:
                files.append(src)
    hit = 1.0 if any(s in files for s in expected_sources) else 0.0
    mrr = 0.0
    for i, f in enumerate(files, start=1):
        if f in expected_sources:
            mrr = 1.0 / i
            break
    return hit, round(mrr, 4), files


def _info_coverage(answer: str, expected_information: list) -> float:
    if not expected_information:
        return 1.0
    answer_l = (answer or "").lower()
    found = sum(1 for tok in expected_information if tok.lower() in answer_l)
    return round(found / len(expected_information), 4)


def _count_agent_wos(machine: str) -> int:
    from backend.services import cmms_client

    result = cmms_client.get_open_work_orders(machine)
    wos = result.get("open_work_orders", []) if isinstance(result, dict) else []
    return sum(1 for w in wos if w.get("created_by") == "maintenance-agent")


# ---------------------------------------------------------------------------
# Esecuzione di un caso
# ---------------------------------------------------------------------------

def run_case(client, case: dict, pipeline: ConfigurableRag, counter: TokenCounter,
             judge, use_judge: bool) -> dict:
    counter.reset()
    n_retrievals_before = len(pipeline.retriever.calls)
    expected_behavior = case.get("expected_behavior", "answer_only")
    wos_before = _count_agent_wos(case["machine"]) if (
        expected_behavior == "propose_work_order" and case.get("machine")
    ) else 0

    t0 = time.perf_counter()
    r = client.post("/chat", json={"message": case["question"]})
    body = r.json() if r.status_code == 200 else {}
    latency_ms = (time.perf_counter() - t0) * 1000

    # Turno di conferma (solo se l'agente ha proposto un work order)
    wo_created = None
    if expected_behavior == "propose_work_order" and body.get("confirmation_required"):
        t1 = time.perf_counter()
        client.post("/chat", json={"message": "Sì, confermo.", "session_id": body["session_id"]})
        latency_ms += (time.perf_counter() - t1) * 1000
        if case.get("machine"):
            wo_created = _count_agent_wos(case["machine"]) > wos_before

    # --- metriche deterministiche -------------------------------------------
    tools_called = [d["tool"] for d in body.get("gathered_data", [])]
    expected_tools = case.get("expected_tools", [])
    precision, recall, f1 = _set_metrics(tools_called, expected_tools)

    retrieved = pipeline.retriever.calls[n_retrievals_before:]
    hit, mrr, retrieved_files = _retrieval_metrics(retrieved, case.get("expected_sources", []))

    answer = body.get("answer", "")
    behavior_ok = (
        bool(body.get("confirmation_required")) == (expected_behavior == "propose_work_order")
    )

    result = {
        "id": case["id"],
        "question": case["question"],
        "category": case.get("category", ""),
        "answer": answer,
        "latency_ms": round(latency_ms, 1),
        "tools_called": tools_called,
        "expected_tools": expected_tools,
        "tool_precision": precision,
        "tool_recall": recall,
        "tool_f1": f1,
        "retrieval_hit": hit,
        "retrieval_mrr": mrr,
        "retrieved_files": retrieved_files,
        "expected_sources": case.get("expected_sources", []),
        "info_coverage": _info_coverage(answer, case.get("expected_information", [])),
        "behavior_ok": behavior_ok,
        "work_order_created": wo_created,
        "context_relevance": None,
        "answer_relevance": None,
        "faithfulness": None,
        "hallucination": None,
        "unsupported_claims": [],
        "refusal_ok": None,
        **counter.snapshot(),
    }

    # --- metriche del giudice (LLM-as-judge) ---------------------------------
    if use_judge and judge is not None:
        # Contesto = chunk recuperati + dati CMMS raccolti nel turno
        context_parts = [doc["content"] for call in retrieved for doc in call["docs"]]
        cmms_data = json.dumps(body.get("gathered_data", []), ensure_ascii=False, default=str)
        context = "\n\n".join(context_parts)
        if cmms_data and cmms_data != "[]":
            context += f"\n\n--- Dati CMMS ---\n{cmms_data}"

        result["answer_relevance"] = judge.answer_relevance(case["question"], answer)
        if retrieved or (cmms_data and cmms_data != "[]"):
            result["context_relevance"] = judge.context_relevance(case["question"], context)
            faith = judge.faithfulness(case["question"], answer, context)
            if faith is not None:
                result["faithfulness"] = faith["score"]
                result["unsupported_claims"] = [
                    {"claim": c["claim"], "evidence": c["evidence"]}
                    for c in faith["claims"] if not c["supported"]
                ]
                result["hallucination"] = len(result["unsupported_claims"]) > 0
        if case.get("should_refuse"):
            result["refusal_ok"] = judge.refused_honestly(case["question"], answer)

    return result


# ---------------------------------------------------------------------------
# Aggregazione e report
# ---------------------------------------------------------------------------

def _mean(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def _rate(values):
    """Frazione di True tra i valori non None."""
    values = [v for v in values if v is not None]
    return round(sum(1 for v in values if v) / len(values), 4) if values else None


def _pctile(values, p):
    values = [v for v in values if v is not None]
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    f, c = math.floor(k), math.ceil(k)
    return round(s[int(k)] if f == c else s[f] + (s[c] - s[f]) * (k - f), 1)


def aggregate(cases: list) -> dict:
    lat = [c["latency_ms"] for c in cases]
    tokens = [c.get("total_tokens", 0) for c in cases]
    return {
        "n_cases": len(cases),
        "retrieval_hit": _mean([c.get("retrieval_hit") for c in cases]),
        "retrieval_mrr": _mean([c.get("retrieval_mrr") for c in cases]),
        "context_relevance": _mean([c.get("context_relevance") for c in cases]),
        "answer_relevance": _mean([c.get("answer_relevance") for c in cases]),
        "faithfulness": _mean([c.get("faithfulness") for c in cases]),
        "hallucination_rate": _rate([c.get("hallucination") for c in cases]),
        "tool_precision": _mean([c.get("tool_precision") for c in cases]),
        "tool_recall": _mean([c.get("tool_recall") for c in cases]),
        "tool_f1": _mean([c.get("tool_f1") for c in cases]),
        "info_coverage": _mean([c.get("info_coverage") for c in cases]),
        "behavior_ok_rate": _rate([c.get("behavior_ok") for c in cases]),
        "refusal_ok_rate": _rate([c.get("refusal_ok") for c in cases]),
        "latency_avg_ms": round(statistics.mean(lat), 1) if lat else None,
        "latency_p50_ms": _pctile(lat, 50),
        "latency_p95_ms": _pctile(lat, 95),
        "total_prompt_tokens": sum(c.get("prompt_tokens", 0) for c in cases),
        "total_completion_tokens": sum(c.get("completion_tokens", 0) for c in cases),
        "avg_tokens_per_case": round(sum(tokens) / len(cases), 1) if cases else None,
        "avg_llm_calls": _mean([c.get("n_llm_calls") for c in cases]),
    }


def _fmt(v, pct=False):
    if v is None:
        return "n/d"
    return f"{v*100:.1f}%" if pct else f"{v}"


def write_markdown_report(report: dict, path: Path):
    m = report["metrics"]
    lines = [
        f"# Evaluation report — variant: {report['variant']}",
        "",
        f"- **Dataset:** {report['dataset']} ({m['n_cases']} casi)",
        f"- **Modello agente:** {report.get('model', 'llama3.1')} · **Giudice:** {report.get('judge_model', 'n/d')}",
        f"- **Config RAG:** {json.dumps(report.get('rag_config', {}), ensure_ascii=False)}",
        f"- **Data:** {report['timestamp']}",
        "",
        "## Metriche complessive",
        "",
        "| Metrica | Valore |",
        "|---|---|",
        f"| Retrieval hit@k | {_fmt(m['retrieval_hit'], True)} |",
        f"| Retrieval MRR | {_fmt(m['retrieval_mrr'])} |",
        f"| Context relevance | {_fmt(m['context_relevance'])} |",
        f"| Answer relevance | {_fmt(m['answer_relevance'])} |",
        f"| Faithfulness | {_fmt(m['faithfulness'])} |",
        f"| Hallucination rate | {_fmt(m['hallucination_rate'], True)} |",
        f"| Tool selection F1 | {_fmt(m['tool_f1'])} (P {_fmt(m['tool_precision'])} / R {_fmt(m['tool_recall'])}) |",
        f"| Info coverage | {_fmt(m['info_coverage'], True)} |",
        f"| Behavior OK (HITL) | {_fmt(m['behavior_ok_rate'], True)} |",
        f"| Refusal OK (out-of-scope) | {_fmt(m['refusal_ok_rate'], True)} |",
        f"| Latency p50 / p95 | {m['latency_p50_ms']} ms / {m['latency_p95_ms']} ms |",
        f"| Token per caso (media) | {m['avg_tokens_per_case']} |",
        f"| Token totali (prompt+completion) | {m['total_prompt_tokens'] + m['total_completion_tokens']} |",
        f"| Chiamate LLM (media/caso) | {m['avg_llm_calls']} |",
        "",
        "## Per categoria",
        "",
        "| Categoria | n | hit@k | MRR | ctx rel | ans rel | faith | halluc% | tool F1 | info cov | behavior | lat p50 | tok/caso |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for cat, cm in report.get("by_category", {}).items():
        lines.append(
            f"| {cat} | {cm['n_cases']} | {_fmt(cm['retrieval_hit'], True)} | {_fmt(cm['retrieval_mrr'])} "
            f"| {_fmt(cm['context_relevance'])} | {_fmt(cm['answer_relevance'])} | {_fmt(cm['faithfulness'])} "
            f"| {_fmt(cm['hallucination_rate'], True)} | {_fmt(cm['tool_f1'])} | {_fmt(cm['info_coverage'], True)} "
            f"| {_fmt(cm['behavior_ok_rate'], True)} | {cm['latency_p50_ms']} | {cm['avg_tokens_per_case']} |"
        )

    lines += ["", "## Allucinazioni (claim non supportati)", ""]
    any_hall = False
    for c in report["cases"]:
        if c.get("unsupported_claims"):
            any_hall = True
            lines.append(f"- **{c['id']}** ({c['category']}):")
            for claim in c["unsupported_claims"]:
                lines.append(f"  - \"{claim['claim']}\" — {claim.get('evidence', 'nessuna evidenza nel contesto')}")
    if not any_hall:
        lines.append("- Nessuna allucinazione rilevata.")

    lines += ["", "## Casi con tool selection F1 = 0", ""]
    zero_f1 = [c for c in report["cases"] if c.get("tool_f1") == 0.0]
    if zero_f1:
        for c in zero_f1:
            lines.append(f"- **{c['id']}**: chiamati {c['tools_called']}, attesi {c['expected_tools']}")
    else:
        lines.append("- Nessuno.")

    lines += ["", "## Casi più lenti", ""]
    for c in sorted(report["cases"], key=lambda x: -(x.get("latency_ms") or 0))[:5]:
        lines.append(f"- {c['id']}: {c['latency_ms']} ms")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Confronto v1 vs v2
# ---------------------------------------------------------------------------

COMPARE_METRICS = [
    ("retrieval_hit", "Retrieval hit@k", True),
    ("retrieval_mrr", "Retrieval MRR", True),
    ("context_relevance", "Context relevance", True),
    ("answer_relevance", "Answer relevance", True),
    ("faithfulness", "Faithfulness", True),
    ("hallucination_rate", "Hallucination rate", False),
    ("tool_f1", "Tool selection F1", True),
    ("info_coverage", "Info coverage", True),
    ("behavior_ok_rate", "Behavior OK (HITL)", True),
    ("refusal_ok_rate", "Refusal OK", True),
    ("latency_p50_ms", "Latency p50 (ms)", False),
    ("avg_tokens_per_case", "Token/caso", False),
]


def compare_reports(path_a: str, path_b: str) -> Path:
    a = json.loads(Path(path_a).read_text(encoding="utf-8"))
    b = json.loads(Path(path_b).read_text(encoding="utf-8"))
    name_a, name_b = a["variant"], b["variant"]
    ma, mb = a["metrics"], b["metrics"]

    lines = [
        f"# Confronto {name_a} vs {name_b}",
        "",
        f"- **{name_a}:** {json.dumps(a.get('rag_config', {}), ensure_ascii=False)} · {a['timestamp']}",
        f"- **{name_b}:** {json.dumps(b.get('rag_config', {}), ensure_ascii=False)} · {b['timestamp']}",
        "",
        "## Effetto complessivo",
        "",
        f"| Metrica | {name_a} | {name_b} | Δ | Direzione |",
        "|---|---|---|---|---|",
    ]
    for key, label, higher_better in COMPARE_METRICS:
        va, vb = ma.get(key), mb.get(key)
        if va is None or vb is None:
            lines.append(f"| {label} | {_fmt(va, key in ('retrieval_hit', 'hallucination_rate', 'info_coverage', 'behavior_ok_rate', 'refusal_ok_rate'))} | {_fmt(vb, key in ('retrieval_hit', 'hallucination_rate', 'info_coverage', 'behavior_ok_rate', 'refusal_ok_rate'))} | n/d | – |")
            continue
        delta = round(vb - va, 4)
        if abs(delta) < 1e-9:
            direction = "–"
        else:
            improved = (delta > 0) if higher_better else (delta < 0)
            direction = "✓ migliorato" if improved else "✗ peggiorato"
        lines.append(f"| {label} | {_fmt(va, key in ('retrieval_hit', 'hallucination_rate', 'info_coverage', 'behavior_ok_rate', 'refusal_ok_rate'))} | {_fmt(vb, key in ('retrieval_hit', 'hallucination_rate', 'info_coverage', 'behavior_ok_rate', 'refusal_ok_rate'))} | {delta:+.4f} | {direction} |")

    # Delta per categoria (metriche principali)
    lines += ["", "## Delta per categoria", ""]
    cats = sorted(set(a.get("by_category", {})) | set(b.get("by_category", {})))
    for cat in cats:
        ca, cb = a.get("by_category", {}).get(cat), b.get("by_category", {}).get(cat)
        if ca is None or cb is None:
            continue
        lines.append(
            f"- **{cat}** (n={ca['n_cases']}): faith {_fmt(ca['faithfulness'])} → {_fmt(cb['faithfulness'])} · "
            f"ans rel {_fmt(ca['answer_relevance'])} → {_fmt(cb['answer_relevance'])} · "
            f"hit@k {_fmt(ca['retrieval_hit'], True)} → {_fmt(cb['retrieval_hit'], True)} · "
            f"lat p50 {ca['latency_p50_ms']} → {cb['latency_p50_ms']} ms · "
            f"tok/caso {ca['avg_tokens_per_case']} → {cb['avg_tokens_per_case']}"
        )

    # Casi migliorati/peggiorati (faithfulness)
    cases_a = {c["id"]: c for c in a["cases"]}
    cases_b = {c["id"]: c for c in b["cases"]}
    common = [i for i in cases_a if i in cases_b]

    def _delta_cases(metric):
        deltas = []
        for cid in common:
            va, vb = cases_a[cid].get(metric), cases_b[cid].get(metric)
            if va is not None and vb is not None:
                deltas.append((round(vb - va, 4), cid))
        return deltas

    lines += ["", "## Casi migliorati (faithfulness)", ""]
    improved = [d for d in _delta_cases("faithfulness") if d[0] > 0]
    improved.sort(reverse=True)
    for delta, cid in improved[:10]:
        lines.append(f"- {cid}: {cases_a[cid]['faithfulness']} → {cases_b[cid]['faithfulness']} ({delta:+.2f})")
    if not improved:
        lines.append("- Nessuno.")

    lines += ["", "## Casi peggiorati (faithfulness)", ""]
    regressed = [d for d in _delta_cases("faithfulness") if d[0] < 0]
    regressed.sort()
    for delta, cid in regressed[:10]:
        lines.append(f"- {cid}: {cases_a[cid]['faithfulness']} → {cases_b[cid]['faithfulness']} ({delta:+.2f})")
    if not regressed:
        lines.append("- Nessuno.")

    out = RESULTS_DIR / f"comparison_{name_a}_vs_{name_b}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nReport salvato in: {out}")
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_evaluation(args):
    dataset_path = Path(args.dataset)
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))

    if args.categories:
        wanted = {c.strip() for c in args.categories.split(",")}
        dataset = [c for c in dataset if c.get("category") in wanted]
    if args.limit:
        dataset = dataset[: args.limit]
    if not dataset:
        raise SystemExit("Nessun caso da eseguire (controlla --categories / --limit).")

    rag_cfg = {}
    if args.rag_config:
        rag_cfg = json.loads(Path(args.rag_config).read_text(encoding="utf-8"))

    index_dir = str(Path(args.index).resolve())
    if not Path(index_dir).exists():
        raise SystemExit(
            f"Indice Chroma '{index_dir}' non trovato.\n"
            "Per l'indice v1: python ingest.py (o CHROMA_DB_OVERRIDE) · "
            "Per un indice v2: python tests/evaluation/build_index.py --out <dir> --chunk-size 700 --chunk-overlap 100"
        )

    required_models = {rag_cfg.get("llm_model", "llama3.1"), rag_cfg.get("embedding_model", "nomic-embed-text"), args.agent_model}
    if not args.no_judge:
        required_models.add(args.judge_model)
    _check_ollama(required_models)

    persist_dir = rag_cfg.get("persist_dir")
    if persist_dir and not Path(persist_dir).exists():
        raise SystemExit(
            f"Indice RAG '{persist_dir}' (rag_config) non trovato. "
            "Costruiscilo prima: python tests/evaluation/build_index.py --out " + persist_dir
        )

    print(f"Avvio CMMS in-process (workers={args.workers})...")
    port, server = _start_cmms(args.workers)
    _point_cmms_client(port)

    counter = TokenCounter()
    client, pipeline = _build_agent(index_dir, rag_cfg, counter, args.agent_model)
    judge = None
    if not args.no_judge:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from judge import Judge
        judge = Judge(model=args.judge_model, provider=args.judge_provider)
        print(f"Giudice: {args.judge_model} ({args.judge_provider})")
    else:
        print("Giudice disattivato (--no-judge): solo metriche deterministiche.")

    # Resume: riprendi dai risultati già salvati per questa variant
    out_path = RESULTS_DIR / f"{args.variant}.json"
    all_cases = dataset  # ordine completo (per il salvataggio)
    results = {}
    if args.resume and out_path.exists():
        prev = json.loads(out_path.read_text(encoding="utf-8"))
        results = {c["id"]: c for c in prev.get("cases", [])}
        dataset = [c for c in dataset if c["id"] not in results]
        print(f"Resume: {len(results)} casi già presenti, {len(dataset)} da eseguire.")

    def _process(case):
        print(f"  [{case['category']}] {case['id']}...", end=" ", flush=True)
        t0 = time.perf_counter()
        result = run_case(client, case, pipeline, counter, judge, use_judge=not args.no_judge)
        print(f"done in {time.perf_counter() - t0:.0f}s")
        return result

    pending = list(dataset)
    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for result in pool.map(_process, pending):
                results[result["id"]] = result
    else:
        for case in pending:
            results[case["id"]] = _process(case)
            # Salvataggio incrementale: crash-safe + abilita --resume
            _save(out_path, args, all_cases, results, rag_cfg, index_dir)

    _save(out_path, args, all_cases, results, rag_cfg, index_dir)
    server.should_exit = True

    print(f"\nReport salvato in: {out_path}")
    print(f"Report Markdown: {out_path.with_suffix('.md')}")


def _save(out_path: Path, args, dataset, results: dict, rag_cfg: dict, index_dir: str):
    cases = [results[c["id"]] for c in dataset if c["id"] in results]
    by_category = {}
    for case in dataset:
        if case["id"] in results:
            by_category.setdefault(case.get("category", "uncategorized"), []).append(results[case["id"]])
    report = {
        "variant": args.variant,
        "dataset": str(args.dataset),
        "model": args.agent_model,
        "judge_model": None if args.no_judge else args.judge_model,
        "rag_config": rag_cfg,
        "index": index_dir,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "metrics": aggregate(cases),
        "by_category": {cat: aggregate(cs) for cat, cs in by_category.items()},
        "cases": cases,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown_report(report, out_path.with_suffix(".md"))


def main():
    parser = argparse.ArgumentParser(description="FASE 9 - LLM Evaluation harness")
    parser.add_argument("--variant", default="run", help="Nome della run (default: run)")
    parser.add_argument("--dataset", default=str(ROOT / "data" / "evaluation" / "eval_dataset.json"))
    parser.add_argument("--index", default=str(ROOT / "chroma_db"), help="Indice Chroma (default chroma_db)")
    parser.add_argument("--rag-config", default=None, help="JSON con parametri RAG (k, prompt, modello, indice)")
    parser.add_argument("--limit", type=int, default=None, help="Solo i primi N casi")
    parser.add_argument("--categories", default=None, help="Categorie separate da virgola")
    parser.add_argument("--no-judge", action="store_true", help="Solo metriche deterministiche (veloce)")
    parser.add_argument("--agent-model", default="llama3.1", help="Modello LLM dell'agente (default llama3.1)")
    parser.add_argument("--judge-model", default="llama3.1")
    parser.add_argument("--judge-provider", default="ollama", choices=["ollama", "openai"])
    parser.add_argument("--resume", action="store_true", help="Riprendi da un report esistente della stessa variant")
    parser.add_argument("--workers", type=int, default=1, help="Casi in parallelo (default 1; >1 sperimentale)")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"), help="Confronta due report JSON e genera il delta")
    args = parser.parse_args()

    if args.compare:
        compare_reports(args.compare[0], args.compare[1])
        return

    run_evaluation(args)


if __name__ == "__main__":
    main()
