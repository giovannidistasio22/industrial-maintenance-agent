# FASE 9 - LLM Evaluation

Framework di evaluation quantitativa per l'agente di manutenzione industriale.
Serve a **misurare l'effetto delle modifiche** (RAG v1 → RAG v2) con numeri,
non con impressioni:

```
RAG v1  →  Evaluation  →  [numeri]
RAG v2  →  Evaluation  →  [numeri]  →  confronto quantitativo
```

## File

| File | Ruolo |
|---|---|
| `eval_dataset.json` | 66 domande ground-truth, organizzate per categoria |
| `evaluator.py` | Harness: esegue il dataset contro l'agente reale e calcola le metriche |
| `judge.py` | LLM-as-judge (relevance, faithfulness, hallucination, refusal) |
| `build_index.py` | Costruisce un indice Chroma con chunking configurabile (per v2) |
| `rag_v2.json` | Esempio di config RAG v2 (k, prompt, indice, modello) |
| `results/` | Report JSON + Markdown delle run e dei confronti |

## Metriche misurate

| Metrica | Come si calcola |
|---|---|
| **Retrieval quality** | `hit@k`: il file atteso (`expected_sources`) è tra i chunk recuperati? `MRR`: posizione del primo chunk utile (1/pos). `context relevance`: giudizio LLM (0-1) sull'utilità del contesto recuperato. |
| **Answer relevance** | Giudice LLM: quanto la risposta è rilevante per la domanda (0-1). |
| **Faithfulness** | Decomposizione in claim: il giudice estrae le affermazioni fattuali dalla risposta e verifica ciascuna contro il contesto (chunk + dati CMMS). Faithfulness = claim supportati / totali. |
| **Tool selection** | Precision / recall / F1 (base a insiemi) dei tool chiamati vs `expected_tools`. |
| **Hallucination** | Presenza di claim **non** supportati dal contesto (rate = frazione di casi con almeno un claim non supportato). |
| **Latency** | Tempo wall-clock per domanda (p50/p95), incluso il turno di conferma HITL. |
| **Token usage** | Prompt + completion token di **tutte** le chiamate LLM del turno (agente + RAG), letti da Ollama (`prompt_eval_count`/`eval_count`). |
| **Info coverage** | Frazione di `expected_information` presente nella risposta. |
| **Behavior OK** | L'agente propone un work order solo quando `expected_behavior` lo richiede (HITL). |
| **Refusal OK** | Per le domande out-of-scope: l'agente riconosce onestamente la mancanza di informazioni. |

## Setup

Prerequisiti: Ollama acceso con i modelli, indice Chroma v1 già costruito,
ambiente `myenv` attivo.

```bash
ollama pull llama3.1 && ollama pull nomic-embed-text
python ingest.py                      # indice v1 (una volta)
conda activate myenv
```

Il CMMS **non** serve avviato: l'evaluator lo fa partire in-process
(SQLite in-memory + seed, stesso approccio dei test della Fase 8), così i dati
sono sempre deterministici.

## Esecuzione

```bash
# RAG v1 (pipeline attuale: k=4, indice chroma_db, prompt standard)
python tests/evaluation/evaluator.py --variant v1

# Run veloce / parziale
python tests/evaluation/evaluator.py --variant v1 --limit 10          # primi 10 casi
python tests/evaluation/evaluator.py --variant v1 --categories diagnosis_p102,safety
python tests/evaluation/evaluator.py --variant v1 --no-judge          # solo metriche deterministiche (veloce)
python tests/evaluation/evaluator.py --variant v1 --resume            # riprende da dove si era fermata

# RAG v2 (es. k=6, prompt migliorato, indice con chunk 700/100)
python tests/evaluation/build_index.py --out data/evaluation/indices/chroma_v2 --chunk-size 700 --chunk-overlap 100
python tests/evaluation/evaluator.py --variant v2 --rag-config data/evaluation/rag_v2.json

# Confronto: genera il report quantitativo dell'effetto delle modifiche
python tests/evaluation/evaluator.py --compare data/evaluation/results/v1.json data/evaluation/results/v2.json
```

Ogni run scrive in `data/evaluation/results/`:
- `<variant>.json` — risultati per caso + aggregati (crash-safe: salvataggio incrementale)
- `<variant>.md` — report leggibile (metriche complessive, per categoria, allucinazioni, casi lenti)
- `comparison_<a>_vs_<b>.md` — per `--compare`

## Il dataset

`eval_dataset.json` contiene **66 domande** ground-truth, derivate dai 5 manuali
in `data/manuals/` e dai dati seed del CMMS (P-101 sana, P-102 con trend 68→93 °C,
C-201 con mandata in lieve aumento, WO-1042 aperto). Categorie:

| Categoria | N | Cosa misura |
|---|---|---|
| `diagnosis_p102` | 10 | Diagnosi con anomalie reali (sensore + manuali + HITL) |
| `diagnosis_p101` | 6 | Macchina sana: non deve inventare problemi |
| `diagnosis_c201` | 8 | Compressore, interdipendenza col circuito di raffreddamento |
| `procedure` | 8 | Procedure di manutenzione (maintenance_procedures.pdf) |
| `safety` | 7 | Sicurezza: LOTO, ESD, PPE, PTW (safety_manual.pdf) |
| `pump_manual` | 9 | Specifiche e procedure della pompa (pump_manual.pdf) |
| `compressor_manual` | 5 | Specifiche del compressore (compressor_manual.pdf) |
| `troubleshooting` | 5 | Matrice sintomi/cause (troubleshooting.pdf) |
| `general_knowledge` | 4 | Domande generali: non servono tool |
| `out_of_scope` | 4 | Trappole: informazioni **assenti** dai documenti → l'agente deve rifiutare onestamente |

Ogni caso dichiara:
- `expected_tools` — i tool che l'agente dovrebbe chiamare (per la tool selection)
- `expected_information` — token che devono comparire nella risposta (info coverage)
- `expected_sources` — i file che il retrieval dovrebbe recuperare (retrieval quality)
- `expected_behavior` — `answer_only` o `propose_work_order` (HITL)
- `should_refuse` — per le trappole out-of-scope

Per aggiungere un caso: una riga JSON nel dataset, nessun codice da scrivere.

## Come funziona il confronto v1 → v2

1. **RAG v1** è il baseline: `rag.py` attuale (k=4, chunk 1000/150, prompt standard,
   indice `chroma_db`). Si esegue con `--variant v1` senza config.
2. **RAG v2** è la modifica che vuoi misurare. La config (`rag_v2.json`) può
   cambiare: `k`, `prompt`, `llm_model`, `embedding_model`, `persist_dir`
   (un indice costruito con `build_index.py`, es. chunk 700/100).
3. `--compare` allinea i due report per caso e produce:
   - tabella metrica per metrica con Δ e direzione (migliorato/peggiorato)
   - delta per categoria
   - casi migliorati e peggiorati (faithfulness)

Così si dimostra **quantitativamente** l'effetto di ogni modifica:
"con k=6 e chunk 700 la retrieval hit@k passa da 0.62 a 0.78, la faithfulness
da 0.71 a 0.84, a costo di +12% di token e +300 ms di latenza".

## Note

- **Giudice**: di default usa lo stesso modello locale dell'agente (`llama3.1`).
  Per ridurre il bias di auto-preferenza si può indicare un modello più forte
  (`--judge-model qwen2.5:14b`) o un modello API (`--judge-provider openai`).
- **Determinismo**: il CMMS è in-memory e ripopolato a ogni run; le date dei
  sensori sono relative a "oggi", quindi i valori assoluti cambiano ma i trend
  no. I risultati sono comparabili tra run.
- **Tempi**: 65 casi × (chiamate LLM agente + chiamate giudice) su modello
  locale possono richiedere molto. Usa `--limit`, `--categories`, `--no-judge`
  e `--resume` per lavorare in incrementi.
- **`--workers > 1`** è sperimentale: i casi girano in parallelo, ma il CMMS
  in-process usa un solo DB SQLite e Ollama è un collo di bottiglia.
