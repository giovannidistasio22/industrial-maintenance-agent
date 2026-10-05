"""
FASE 8 - Unit test: pipeline RAG (retriever -> contesto -> LLM -> fonti).

Chroma e Ollama NON vengono usati: il retriever e l'LLM sono stub, e si
verifica la LOGICA della pipeline:
  - il contesto è assemblato dai documenti recuperati
  - le fonti sono deduplicate (stesso file+pagina -> una sola voce)
  - il comportamento con contesto vuoto
"""

from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

from rag import PROMPT_TEMPLATE, RagPipeline


class _FakeRetriever:
    def __init__(self, docs):
        self.docs = docs

    def invoke(self, query):
        return self.docs


def _make_pipeline(docs, llm_content="Risposta basata sul contesto."):
    """Costruisce una RagPipeline SENZA il costruttore (niente Chroma/Ollama)."""
    seen = []

    def _fake_llm(prompt):
        seen.append(prompt)
        return AIMessage(content=llm_content)

    pipeline = RagPipeline.__new__(RagPipeline)
    pipeline.retriever = _FakeRetriever(docs)
    pipeline.llm = RunnableLambda(_fake_llm)
    pipeline.prompt = ChatPromptTemplate.from_template(PROMPT_TEMPLATE)
    return pipeline, seen


def test_answer_returns_llm_content():
    docs = [Document(page_content="La temperatura del bearing non deve superare 90°C.",
                     metadata={"source": "pump_manual.pdf", "page": 3})]
    pipeline, _ = _make_pipeline(docs, llm_content="Soglia: 90°C.")
    answer, sources = pipeline.answer("Qual è la soglia di temperatura?")
    assert answer == "Soglia: 90°C."
    assert sources == [{"file": "pump_manual.pdf", "page": 3}]


def test_sources_deduplicated():
    docs = [
        Document(page_content="Contesto A.", metadata={"source": "pump_manual.pdf", "page": 1}),
        Document(page_content="Contesto B.", metadata={"source": "pump_manual.pdf", "page": 1}),
        Document(page_content="Contesto C.", metadata={"source": "safety_manual.pdf", "page": 0}),
    ]
    pipeline, _ = _make_pipeline(docs)
    _, sources = pipeline.answer("domanda")
    assert sources == [
        {"file": "pump_manual.pdf", "page": 1},
        {"file": "safety_manual.pdf", "page": 0},
    ]


def _prompt_text(prompt_value) -> str:
    """Il prompt generato è un ChatPromptValue: ne estraiamo il testo."""
    if hasattr(prompt_value, "messages"):
        return prompt_value.messages[0].content
    return str(prompt_value)


def test_context_contains_all_documents():
    docs = [
        Document(page_content="PRIMO DOCUMENTO", metadata={"source": "a.pdf", "page": 0}),
        Document(page_content="SECONDO DOCUMENTO", metadata={"source": "b.pdf", "page": 0}),
    ]
    pipeline, seen = _make_pipeline(docs)
    pipeline.answer("domanda")
    prompt = _prompt_text(seen[0])
    assert "PRIMO DOCUMENTO" in prompt
    assert "SECONDO DOCUMENTO" in prompt
    assert "---" in prompt  # i documenti sono separati


def test_empty_context_still_answers():
    pipeline, seen = _make_pipeline([])
    answer, sources = pipeline.answer("domanda")
    assert answer == "Risposta basata sul contesto."
    assert sources == []
    # il prompt è comunque generato, con contesto vuoto
    assert "Domanda:" in _prompt_text(seen[0])
