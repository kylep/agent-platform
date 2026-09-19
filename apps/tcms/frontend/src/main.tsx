import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@ap/ui/tokens.css";
import "./app.css";
import App from "./App";

// Follow the platform's theme choice (same localStorage key as the console).
document.documentElement.dataset.theme =
  localStorage.getItem("theme") === "light" ? "light" : "dark";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
