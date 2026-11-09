import { createRoot } from "react-dom/client";
import {
  AgStudioLicenseManager,
  AgStudioModuleRegistry,
  AgStudioAiModule,
} from "ag-studio";
import App from "./App";
import { startPolling } from "./logs";
import "./studio.css";

AgStudioModuleRegistry.registerModules([AgStudioAiModule]);

async function boot() {
  try {
    const cfg = await (
      await fetch("/api/config", { cache: "no-store" })
    ).json();
    if (cfg.license) AgStudioLicenseManager.setLicenseKey(cfg.license);
  } catch {}
  startPolling();
  createRoot(document.getElementById("root")!).render(<App />);
}
void boot();
