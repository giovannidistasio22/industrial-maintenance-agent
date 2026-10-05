import type { ObservabilitySummary } from "../types";

// FASE 10 (observability): riepilogo del turno, pieghevole, in fondo alla
// risposta dell'agente.
export function Observability({ obs }: { obs: ObservabilitySummary }) {
  const parts: string[] = [];
  if (obs.duration_ms != null) parts.push(`${(obs.duration_ms / 1000).toFixed(1)}s`);
  if (obs.tokens?.total != null) parts.push(`${obs.tokens.total} tokens`);
  if (obs.llm_calls != null) parts.push(`${obs.llm_calls} LLM calls`);
  if (obs.errors && obs.errors.length > 0) parts.push(`${obs.errors.length} errors`);

  return (
    <details className="obs">
      <summary>{parts.join(" · ") || "details"}</summary>
      <div className="obs-body">
        {obs.docs_retrieved && obs.docs_retrieved.length > 0 && (
          <div>
            Docs:{" "}
            {obs.docs_retrieved
              .map((d) => `${d.file}${d.page != null ? ` p.${d.page}` : ""}`)
              .join(", ")}
          </div>
        )}
        {obs.errors && obs.errors.length > 0 && (
          <div className="obs-errors">{obs.errors.join(" | ")}</div>
        )}
      </div>
    </details>
  );
}
