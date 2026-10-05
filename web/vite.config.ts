import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// FASE 12 - Frontend TypeScript (React + Vite)
//
// In dev (npm run dev, porta 5173) il server Vite fa da proxy verso
// l'agente FastAPI (src/main9.py, porta 8003): NIENTE CORS da gestire,
// le chiamate partono "dalla stessa origine" del browser.
//
// In produzione (npm run build) l'app usa VITE_API_BASE (es.
// http://localhost:8003): serve che l'agente abiliti CORS (già fatto in
// main9.py) o che il frontend venga servito dalla stessa origine.
// Dove gira l'agente: default http://localhost:8003 (src/main9.py).
// Si puo' sovrascrivere senza toccare il file: AGENT_URL=http://host:porta npm run dev
const AGENT_URL = process.env.AGENT_URL ?? "http://localhost:8003";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // I path dell'API dell'agente (src/main9.py) -> AGENT_URL
      "/chat": AGENT_URL,
      "/health": AGENT_URL,
      "/sessions": AGENT_URL,
      "/traces": AGENT_URL,
      "/metrics": AGENT_URL,
    },
  },
});
