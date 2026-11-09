import { useCallback, useMemo, useRef, useState } from "react";
import { AgStudio, type AgStudioRef } from "ag-studio-react";
import type { AgStudioMode } from "ag-studio";
import { buildData } from "./data";
import { useSnapshot, tablesFor } from "./hooks";
import { selectFile, getChosen } from "./logs";
import { initialState } from "./state";
import { theme } from "./theme";
import { widgets } from "./widgets";
import { ai } from "./ai";

const PAGES = [
  { id: "overview", label: "Overview" },
  { id: "products", label: "Products" },
];

export default function App() {
  const ref = useRef<AgStudioRef>(null);
  const snap = useSnapshot();
  const tables = tablesFor(snap.events);
  const data = useMemo(() => buildData(tables), [tables]);
  const [mode, setMode] = useState<AgStudioMode>("view");
  const [page, setPage] = useState("overview");

  const selectPage = useCallback((id: string) => {
    const api = ref.current?.api;
    if (!api) return;
    api.setState({ ...api.getState(), selectedPageId: id });
    setPage(id);
  }, []);

  return (
    <div className="app">
      <header className="bar">
        <div>
          <h1>AgentGuard Studio</h1>
          <p>
            Everything your agent tries to do with money, built on AG Studio
          </p>
        </div>
        <nav aria-label="Pages">
          {PAGES.map((p) => (
            <button
              key={p.id}
              type="button"
              aria-pressed={page === p.id}
              onClick={() => selectPage(p.id)}
            >
              {p.label}
            </button>
          ))}
        </nav>
        <div className="bar-right">
          <label className="session">
            <span className="sr">Session</span>
            <select
              value={getChosen()}
              onChange={(e) => selectFile(e.target.value)}
            >
              <option value="auto">Newest session</option>
              {snap.files.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </label>
          <span
            className={`badge ${snap.chainOk === true ? "ok" : snap.chainOk === false ? "bad" : ""}`}
          >
            {snap.chainOk === true
              ? "Log verified"
              : snap.chainOk === false
                ? "Log was altered"
                : "Checking log"}
          </span>
          <button
            type="button"
            onClick={() => setMode(mode === "view" ? "edit" : "view")}
          >
            {mode === "view" ? "Edit layout" : "Done editing"}
          </button>
          <span className={`live ${snap.online ? "on" : "off"}`} role="status">
            <i />
            {snap.online
              ? `Live, updated ${new Date(snap.updated).toLocaleTimeString()}`
              : "Cannot reach the dashboard server"}
          </span>
        </div>
      </header>
      <main>
        <AgStudio
          ref={ref as any}
          style={{ height: "100%", width: "100%" }}
          data={data}
          initialState={initialState}
          mode={mode}
          theme={theme}
          widgets={widgets as any}
          ai={ai as any}
        />
      </main>
    </div>
  );
}
