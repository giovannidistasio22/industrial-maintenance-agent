import { useEffect, useRef, useState } from "react";
import { sendChat, health, deleteSession } from "./api";
import type { ChatMessage, ChatResponse } from "./types";
import { ToolSteps } from "./components/ToolSteps";
import { ConfirmationCard } from "./components/ConfirmationCard";
import { Observability } from "./components/Observability";

let counter = 0;
const nextId = () => `m${++counter}`;

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cmms, setCmms] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    health()
      .then((h) => setCmms(h.cmms))
      .catch(() => setCmms("down"));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setInput("");
    setError(null);
    setMessages((ms) => [...ms, { id: nextId(), role: "user", content: message }]);
    setBusy(true);
    try {
      const res: ChatResponse = await sendChat(message, sessionId ?? undefined);
      setSessionId(res.session_id);
      setMessages((ms) => [
        ...ms,
        {
          id: nextId(),
          role: "agent",
          content: res.answer,
          steps: res.gathered_data,
          observability: res.observability,
          pending_action: res.pending_action,
          confirmation_required: res.confirmation_required,
          machine: res.active_machine,
        },
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function newSession() {
    if (sessionId) deleteSession(sessionId).catch(() => {});
    setSessionId(null);
    setMessages([]);
    setError(null);
  }

  // La proposta pendente (Fase 7) è quella dell'ULTIMA risposta dell'agente.
  const lastAgent = [...messages].reverse().find((m) => m.role === "agent");
  const hasPending = Boolean(lastAgent?.confirmation_required);

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>Industrial Maintenance Assistant</h1>
          <span className="subtitle">Agent · CMMS · RAG · Observability</span>
        </div>
        <div className="header-right">
          <span className={`badge ${cmms === "up" ? "ok" : "bad"}`}>
            CMMS {cmms ?? "…"}
          </span>
          {sessionId && (
            <span className="badge" title={sessionId}>
              session {sessionId.slice(0, 8)}
            </span>
          )}
          <button className="ghost" onClick={newSession}>
            New session
          </button>
        </div>
      </header>

      <main className="chat">
        {messages.length === 0 && (
          <div className="empty">
            <p>
              Ask about a machine, e.g. <em>“P-102 has abnormal vibrations”</em>.
            </p>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`msg ${m.role}`}>
            <div className="who">
              {m.role === "user" ? "Technician" : "AI Agent"}
              {m.role === "agent" && m.machine && (
                <span className="machine">{m.machine}</span>
              )}
            </div>
            <div className="bubble">
              {m.role === "agent" && m.steps && m.steps.length > 0 && (
                <ToolSteps steps={m.steps} />
              )}
              <p className="answer">{m.content}</p>
              {m.role === "agent" && m.confirmation_required && m.pending_action && (
                <ConfirmationCard
                  action={m.pending_action}
                  disabled={busy}
                  onConfirm={() => send("Sì, confermo.")}
                  onDeny={() => send("No, non ora.")}
                />
              )}
              {m.role === "agent" && m.observability && (
                <Observability obs={m.observability} />
              )}
            </div>
          </div>
        ))}
        {busy && (
          <div className="msg agent">
            <div className="who">AI Agent</div>
            <div className="bubble thinking">Working…</div>
          </div>
        )}
        {error && <div className="error">{error}</div>}
        <div ref={bottomRef} />
      </main>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={
            hasPending
              ? "Respond to the work order proposal (yes/no) or ask something else…"
              : "Ask about a machine, e.g. P-102…"
          }
          disabled={busy}
        />
        <button type="submit" disabled={busy || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
