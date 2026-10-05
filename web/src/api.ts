// FASE 12 - Client HTTP verso l'agente (src/main9.py).
//
// In dev: API_BASE = "" -> le chiamate vanno a /chat, /health, ... sulla
// stessa origine e il proxy di Vite (vite.config.ts) le inolttra a
// http://localhost:8003.
// In produzione: buildare con VITE_API_BASE=http://localhost:8003
// (o l'URL dell'agente) e l'agente deve avere CORS abilitato.

import type { ChatResponse } from "./types";

const API_BASE: string = import.meta.env.VITE_API_BASE ?? "";

export async function sendChat(
  message: string,
  sessionId?: string,
): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId ?? undefined }),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null)) as
      | { detail?: string }
      | null;
    throw new Error(detail?.detail ?? `HTTP ${res.status}`);
  }
  return (await res.json()) as ChatResponse;
}

export async function health(): Promise<{ status: string; cmms: string }> {
  const res = await fetch(`${API_BASE}/health`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as { status: string; cmms: string };
}

export async function deleteSession(sessionId: string): Promise<void> {
  await fetch(`${API_BASE}/sessions/${sessionId}`, { method: "DELETE" });
}
