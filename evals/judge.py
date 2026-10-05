"""
FASE 9 - LLM-as-judge per l'evaluation.

Il giudice è un LLM separato (default: lo stesso modello locale dell'agente,
configurabile con --judge-model / --judge-provider) che valuta:

  - answer relevance   (0-1): quanto la risposta è rilevante per la domanda
  - context relevance  (0-1): quanto il contesto recuperato è utile
  - faithfulness       (0-1): frazione di claim fattuali supportati dal contesto
  - hallucination      (bool): presenza di claim non supportati
  - refusal            (bool): per le domande out-of-scope, la risposta
                             riconosce onestamente la mancanza di informazioni

Tutte le valutazioni usano structured output (Pydantic) e sono parse in modo
difensivo: se il giudice non produce output valido, la metrica resta None
(non 0), così un fallimento del giudice non contamina i risultati.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Schemi di output strutturato del giudice
# ---------------------------------------------------------------------------

class RelevanceScore(BaseModel):
    score: float = Field(description="Punteggio 0.0-1.0")
    reasoning: str = Field(description="Breve motivazione")


class ClaimList(BaseModel):
    claims: List[str] = Field(description="Affermazioni fattuali atomiche estratte dalla risposta")


class ClaimSupport(BaseModel):
    claims: List[dict] = Field(
        description="Per ogni affermazione: {'claim': ..., 'supported': true/false, 'evidence': ...}"
    )


class RefusalCheck(BaseModel):
    refused_honestly: bool = Field(
        description="True se la risposta riconosce onestamente che l'informazione non è disponibile"
    )
    reasoning: str = Field(description="Breve motivazione")


# ---------------------------------------------------------------------------
# Giudice
# ---------------------------------------------------------------------------

class Judge:
    def __init__(self, model: str = "llama3.1", provider: str = "ollama"):
        if provider == "ollama":
            from langchain_ollama import ChatOllama
            self._llm = ChatOllama(model=model, temperature=0)
        elif provider == "openai":
            from langchain_openai import ChatOpenAI
            self._llm = ChatOpenAI(model=model, temperature=0)
        else:
            raise ValueError(f"Provider giudice non supportato: {provider}")
        self.model = model

    # -- helper -------------------------------------------------------------

    def _structured(self, schema, prompt: str):
        """with_structured_output difensivo: restituisce l'oggetto o None."""
        try:
            return self._llm.with_structured_output(schema).invoke(prompt)
        except Exception:
            return None

    # -- metriche -----------------------------------------------------------

    def answer_relevance(self, question: str, answer: str) -> Optional[float]:
        """Quanto la risposta è rilevante e utile per la domanda (0-1)."""
        if not (answer or "").strip():
            return 0.0
        prompt = f"""Sei un giudice di qualità per un assistente di manutenzione industriale.
Valuta quanto la RISPOSTA è rilevante e utile per rispondere alla DOMANDA.
Punteggio: 1.0 = perfettamente rilevante e risponde alla domanda; 0.5 = parzialmente
rilevante; 0.0 = irrilevante o fuori tema. Non penalizza la lunghezza.

Domanda: {question}

Risposta:
{answer}"""
        result = self._structured(RelevanceScore, prompt)
        return round(float(result.score), 4) if result is not None else None

    def context_relevance(self, question: str, context: str) -> Optional[float]:
        """Quanto il contesto recuperato (manuali + dati CMMS) è utile per la domanda (0-1)."""
        if not (context or "").strip():
            return 0.0
        prompt = f"""Sei un giudice di qualità per un sistema RAG di manutenzione industriale.
Valuta quanto il CONTESTO recuperato è utile per rispondere alla DOMANDA.
Punteggio: 1.0 = contiene tutto il necessario per rispondere; 0.5 = parzialmente utile;
0.0 = nessuna informazione utile.

Domanda: {question}

Contesto recuperato:
{context[:12000]}"""
        result = self._structured(RelevanceScore, prompt)
        return round(float(result.score), 4) if result is not None else None

    def faithfulness(self, question: str, answer: str, context: str) -> Optional[dict]:
        """Decomposizione in claim: frazione di claim fattuali supportati dal contesto.

        Restituisce {"score": 0-1, "claims": [{"claim", "supported", "evidence"}]}
        oppure None se il giudice fallisce.
        """
        if not (answer or "").strip():
            return {"score": 1.0, "claims": []}

        # 1) Estrazione dei claim fattuali atomici
        extract_prompt = f"""Estrai dalla RISPOSTA tutte le affermazioni fattuali atomiche:
valori numerici, cause, procedure, date, nomi di macchine o documenti.
Ignora frasi di cortesia, opinioni e meta-dichiarazioni ("come ho detto...", "in sintesi...").
Se la risposta non contiene affermazioni fattuali, restituisci una lista vuota.

Domanda di riferimento: {question}

Risposta:
{answer}"""
        extracted = self._structured(ClaimList, extract_prompt)
        if extracted is None:
            return None
        claims = [c.strip() for c in (extracted.claims or []) if c and c.strip()]
        if not claims:
            return {"score": 1.0, "claims": []}

        # 2) Verifica di supporto, claim per claim (batch unico)
        claims_block = "\n".join(f"{i+1}. {c}" for i, c in enumerate(claims))
        support_prompt = f"""Sei un giudice di faithfulness per un assistente di manutenzione industriale.
Per ciascuna affermazione numerata, stabilisci se è SUPPORTATA dal CONTESTO
(chunk dei manuali + dati del CMMS). Cita brevemente l'evidenza nel contesto.
Se il contesto non la supporta (o non la menziona), supported=false.
Attenzione: un valore numerico diverso da quello del contesto NON è supportato.

Contesto:
{context[:12000] if context else "(nessun contesto: nessuna fonte recuperata)"}

Affermazioni:
{claims_block}"""
        support = self._structured(ClaimSupport, support_prompt)
        if support is None:
            return None

        # Allinea le risposte del giudice ai claim originali (per indice)
        aligned = []
        for i, claim in enumerate(claims):
            entry = support.claims[i] if i < len(support.claims) and isinstance(support.claims[i], dict) else {}
            aligned.append({
                "claim": claim,
                "supported": bool(entry.get("supported", False)),
                "evidence": str(entry.get("evidence", ""))[:300],
            })
        supported = sum(1 for c in aligned if c["supported"])
        return {"score": round(supported / len(aligned), 4), "claims": aligned}

    def refused_honestly(self, question: str, answer: str) -> Optional[bool]:
        """Per domande out-of-scope: la risposta riconosce la mancanza di informazioni
        invece di inventare dati? (True = rifiuto onesto, False = allucinazione)."""
        prompt = f"""La DOMANDA riguarda informazioni che NON sono disponibili nei manuali
né nel CMMS (macchina inesistente, dato non documentato).
Valuta la RISPOSTA: riconosce onestamente che l'informazione non è disponibile
(invece di inventare valori, nomi o procedure)?

Domanda: {question}

Risposta:
{answer}"""
        result = self._structured(RefusalCheck, prompt)
        return bool(result.refused_honestly) if result is not None else None
