import type { PendingAction } from "../types";

// FASE 7 (human-in-the-loop): l'agente ha PROPOSTO un work order e si è
// fermato (interrupt). Qui il tecnico conferma o annulla esplicitamente:
// il frontend invia la frase che l'agente interpreta (interpret_confirmation).
export function ConfirmationCard({
  action,
  disabled,
  onConfirm,
  onDeny,
}: {
  action: PendingAction;
  disabled: boolean;
  onConfirm: () => void;
  onDeny: () => void;
}) {
  return (
    <div className="confirm">
      <div className="confirm-title">⚠ Work order proposed</div>
      <dl>
        <dt>Machine</dt>
        <dd>{action.machine_id ?? "—"}</dd>
        <dt>Description</dt>
        <dd>{action.description ?? "—"}</dd>
        <dt>Priority</dt>
        <dd>{action.priority ?? "medium"}</dd>
      </dl>
      <div className="confirm-btns">
        <button className="primary" onClick={onConfirm} disabled={disabled}>
          Confirm
        </button>
        <button onClick={onDeny} disabled={disabled}>
          Deny
        </button>
      </div>
    </div>
  );
}
