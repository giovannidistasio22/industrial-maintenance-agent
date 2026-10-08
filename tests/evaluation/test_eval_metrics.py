"""
Test delle metriche dell'evaluation harness (senza Ollama/Chroma).

Verifica le funzioni deterministiche di tests/evaluation/evaluator.py:
  - tool selection (precision/recall/F1)
  - retrieval (hit@k, MRR)
  - info coverage
  - aggregazione e report di confronto
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "evaluation"))

import evaluator  # noqa: E402  (import dopo il sys.path: il modulo è in tests/evaluation/)


# ---------------------------------------------------------------------------
# Tool selection
# ---------------------------------------------------------------------------

def test_tool_selection_no_tools_expected_no_tools_called():
    assert evaluator._set_metrics([], []) == (1.0, 1.0, 1.0)


def test_tool_selection_missing_expected_tool():
    # Nessun tool chiamato ma ne serviva uno: recall zero, F1 zero
    assert evaluator._set_metrics([], ["get_sensor_data"]) == (1.0, 0.0, 0.0)


def test_tool_selection_unexpected_tool():
    # Tool chiamato ma nessuno era atteso: precision zero
    assert evaluator._set_metrics(["search_manual"], []) == (0.0, 1.0, 0.0)


def test_tool_selection_partial():
    p, r, f1 = evaluator._set_metrics(["get_sensor_data", "search_manual"], ["get_sensor_data"])
    assert (p, r, f1) == (0.5, 1.0, 0.6667)  # F1 = 2PR/(P+R), arrotondata a 4 decimali


def test_tool_selection_perfect():
    assert evaluator._set_metrics(["a", "b"], ["a", "b"]) == (1.0, 1.0, 1.0)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def _fake_retrieved():
    return [
        {
            "query": "q",
            "docs": [
                {"content": "x", "source": "/docs/pump_manual.pdf", "page": 0},
                {"content": "y", "source": "/docs/troubleshooting.pdf", "page": 1},
            ],
        }
    ]


def test_retrieval_hit_and_mrr():
    hit, mrr, files = evaluator._retrieval_metrics(_fake_retrieved(), ["troubleshooting.pdf"])
    assert hit == 1.0
    assert mrr == 0.5  # il primo file utile è il secondo recuperato
    assert files == ["pump_manual.pdf", "troubleshooting.pdf"]


def test_retrieval_miss():
    hit, mrr, _ = evaluator._retrieval_metrics(_fake_retrieved(), ["safety_manual.pdf"])
    assert hit == 0.0 and mrr == 0.0


def test_retrieval_not_applicable():
    hit, mrr, files = evaluator._retrieval_metrics(_fake_retrieved(), [])
    assert hit is None and mrr is None and files == []


# ---------------------------------------------------------------------------
# Info coverage
# ---------------------------------------------------------------------------

def test_info_coverage():
    assert evaluator._info_coverage("P-102 è a 93 gradi", ["p-102", "93"]) == 1.0
    assert evaluator._info_coverage("P-102 ok", ["p-102", "93"]) == 0.5
    assert evaluator._info_coverage("qualsiasi", []) == 1.0  # n/d = coperta


# ---------------------------------------------------------------------------
# Aggregazione
# ---------------------------------------------------------------------------

def _fake_cases():
    return [
        {
            "retrieval_hit": 1.0, "retrieval_mrr": 0.5, "context_relevance": 0.8,
            "answer_relevance": 0.9, "faithfulness": 0.5, "hallucination": True,
            "tool_f1": 1.0, "info_coverage": 1.0, "behavior_ok": True, "refusal_ok": None,
            "latency_ms": 100, "prompt_tokens": 10, "completion_tokens": 5,
            "total_tokens": 15, "n_llm_calls": 3,
        },
        {
            "retrieval_hit": 0.0, "retrieval_mrr": 0.0, "context_relevance": None,
            "answer_relevance": 0.7, "faithfulness": 1.0, "hallucination": False,
            "tool_f1": 0.5, "info_coverage": 0.5, "behavior_ok": False, "refusal_ok": True,
            "latency_ms": 300, "prompt_tokens": 20, "completion_tokens": 10,
            "total_tokens": 30, "n_llm_calls": 4,
        },
    ]


def test_aggregate():
    m = evaluator.aggregate(_fake_cases())
    assert m["n_cases"] == 2
    assert m["retrieval_hit"] == 0.5
    assert m["hallucination_rate"] == 0.5
    assert m["behavior_ok_rate"] == 0.5
    assert m["refusal_ok_rate"] == 1.0  # calcolata solo sui casi con refusal_ok non None
    assert m["latency_p50_ms"] == 200.0
    assert m["total_prompt_tokens"] == 30
    assert m["avg_tokens_per_case"] == 22.5


def test_aggregate_empty():
    m = evaluator.aggregate([])
    assert m["n_cases"] == 0
    assert m["retrieval_hit"] is None
    assert m["latency_p50_ms"] is None


# ---------------------------------------------------------------------------
# Confronto v1 vs v2
# ---------------------------------------------------------------------------

def test_compare_reports(tmp_path):
    cases = _fake_cases()
    metrics = evaluator.aggregate(cases)
    report_a = {
        "variant": "v1", "timestamp": "t", "rag_config": {},
        "metrics": metrics, "by_category": {},
        "cases": [{"id": "c1", "faithfulness": 0.5, "answer_relevance": 0.9,
                   "retrieval_hit": 1.0, "latency_ms": 100}],
    }
    report_b = {
        "variant": "v2", "timestamp": "t", "rag_config": {"k": 6},
        "metrics": metrics, "by_category": {},
        "cases": [{"id": "c1", "faithfulness": 1.0, "answer_relevance": 0.9,
                   "retrieval_hit": 1.0, "latency_ms": 80}],
    }
    pa, pb = tmp_path / "v1.json", tmp_path / "v2.json"
    pa.write_text(json.dumps(report_a))
    pb.write_text(json.dumps(report_b))

    out = evaluator.compare_reports(str(pa), str(pb))
    text = out.read_text(encoding="utf-8")
    assert "v1 vs v2" in text
    assert "Faithfulness" in text
    assert "c1: 0.5 → 1.0" in text  # caso migliorato


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

def test_dataset_valid():
    dataset = json.loads(
        (Path(__file__).resolve().parents[2] / "data" / "evaluation" / "eval_dataset.json")
        .read_text(encoding="utf-8")
    )
    assert len(dataset) >= 50, "il dataset deve avere almeno 50 domande"
    ids = [c["id"] for c in dataset]
    assert len(ids) == len(set(ids)), "ID duplicati nel dataset"
    required = {"id", "question", "category", "expected_tools",
                "expected_information", "expected_sources", "expected_behavior"}
    for case in dataset:
        assert required <= set(case), f"{case.get('id')}: campi mancanti {required - set(case)}"
    # I file attesi devono esistere in data/manuals/
    docs = {p.name for p in (ROOT / "data" / "manuals").glob("*.pdf")}
    for case in dataset:
        for src in case["expected_sources"]:
            assert src in docs, f"{case['id']}: fonte inesistente {src}"
