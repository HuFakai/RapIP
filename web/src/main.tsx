import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { applyTheme, readTheme } from "./lib/theme";
import "./style.css";
import "./experience.css";

applyTheme(readTheme());
try {
  document.documentElement.dataset.density =
    localStorage.getItem("rapip.density") === "comfortable"
      ? "comfortable"
      : "compact";
} catch {
  document.documentElement.dataset.density = "compact";
}
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
