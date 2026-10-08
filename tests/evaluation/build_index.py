"""
Costruzione di un indice Chroma con chunking configurabile.

Serve per gli esperimenti RAG v2: cambiando chunking/embedding si crea un
indice SEPARATO (senza toccare quello v1), che poi si indica nella config
RAG dell'evaluator:

  # 1) Costruisci l'indice v2 (es. chunk più piccoli, overlap ridotto)
  python tests/evaluation/build_index.py --out data/evaluation/indices/chroma_v2 --chunk-size 700 --chunk-overlap 100

  # 2) Puntaci la config RAG
  python tests/evaluation/evaluator.py --variant v2 --rag-config data/evaluation/rag_v2.json
  (rag_v2.json deve avere "persist_dir": "data/evaluation/indices/chroma_v2")

  # 3) Confronta
  python tests/evaluation/evaluator.py --compare data/evaluation/results/v1.json data/evaluation/results/v2.json
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description="Costruisce un indice Chroma con chunking configurabile")
    parser.add_argument("--docs-dir", default=str(ROOT / "data" / "manuals"), help="Cartella con i PDF (default data/manuals/)")
    parser.add_argument("--out", required=True, help="Cartella di output dell'indice (es. data/evaluation/indices/chroma_v2)")
    parser.add_argument("--chunk-size", type=int, default=1000, help="Dimensione chunk in caratteri (default 1000, come ingest.py)")
    parser.add_argument("--chunk-overlap", type=int, default=150, help="Overlap tra chunk (default 150, come ingest.py)")
    parser.add_argument("--embedding-model", default="nomic-embed-text")
    args = parser.parse_args()

    from langchain_community.document_loaders import PyPDFLoader
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_ollama import OllamaEmbeddings
    from langchain_chroma import Chroma

    docs_dir = Path(args.docs_dir)
    if not docs_dir.is_dir():
        raise SystemExit(f"Cartella documenti '{docs_dir}' non trovata.")

    print("1/3 - Caricamento documenti...")
    documents = []
    for filename in sorted(os.listdir(docs_dir)):
        if filename.lower().endswith(".pdf"):
            print(f"   Carico: {filename}")
            documents.extend(PyPDFLoader(str(docs_dir / filename)).load())
    print(f"   -> {len(documents)} pagine caricate")

    print(f"2/3 - Chunking (size={args.chunk_size}, overlap={args.chunk_overlap})...")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(documents)
    print(f"   -> {len(chunks)} chunk creati")

    print(f"3/3 - Embeddings ({args.embedding_model}) e salvataggio in '{args.out}'...")
    embeddings = OllamaEmbeddings(model=args.embedding_model)
    Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=str(Path(args.out).resolve()),
    )
    print(f"Fatto. Indice salvato in '{args.out}'")
    print("Usalo così: python tests/evaluation/evaluator.py --variant v2 --rag-config data/evaluation/rag_v2.json")


if __name__ == "__main__":
    main()
