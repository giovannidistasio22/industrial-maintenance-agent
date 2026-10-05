// FASE 12 - Tipi che specchiano il contratto dell'API dell'agente
// (src/main9.py: ChatResponse e amici).

export interface ToolCall {
  tool: string;
  args: Record<string, unknown>;
  result: unknown;
}

export interface ObservabilitySummary {
  turn_id?: string;
  duration_ms?: number;
  llm_calls?: number;
  tokens?: { prompt: number; completion: number; total: number };
  tools_called?: string[];
  docs_retrieved?: Array<{ file: string; page?: number }>;
  errors?: string[];
  trace_file?: string;
}

export interface PendingAction {
  machine_id?: string;
  description?: string;
  priority?: string;
}

export interface ChatResponse {
  session_id: string;
  turn_id: string;
  answer: string;
  active_machine: string | null;
  confirmation_required: boolean;
  pending_action: PendingAction | null;
  gathered_data: ToolCall[];
  trace: Record<string, unknown>[];
  observability: ObservabilitySummary;
}

// Un messaggio della chat (stato locale del frontend).
export interface ChatMessage {
  id: string;
  role: "user" | "agent";
  content: string;
  steps?: ToolCall[];
  observability?: ObservabilitySummary;
  pending_action?: PendingAction | null;
  confirmation_required?: boolean;
  machine?: string | null;
}
