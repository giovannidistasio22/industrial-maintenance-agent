import type { ToolCall } from "../types";

// I nomi dei tool (src/tools.py) -> etichette leggibili per il tecnico.
const LABELS: Record<string, string> = {
  get_machine_status: "Checked machine status",
  get_sensor_data: "Retrieved sensor data",
  get_maintenance_history: "Reviewed maintenance history",
  get_open_work_orders: "Checked open work orders",
  search_manual: "Searched maintenance manual",
  create_work_order: "Created work order",
};

// L'argomento "significativo" da mostrare accanto al tool.
function argHint(args: Record<string, unknown>): string {
  const machine = args.machine_id;
  if (machine !== undefined) return String(machine);
  const query = args.query;
  if (query !== undefined) return `"${String(query)}"`;
  return "";
}

export function ToolSteps({ steps }: { steps: ToolCall[] }) {
  if (steps.length === 0) return null;
  return (
    <ul className="steps">
      {steps.map((s, i) => (
        <li key={i}>
          <span className="check">✓</span>
          <span>{LABELS[s.tool] ?? s.tool}</span>
          {argHint(s.args) && <span className="args"> · {argHint(s.args)}</span>}
        </li>
      ))}
    </ul>
  );
}
