import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@kortex/design-system/styles/tokens.css";
import "./styles/globals.css";
import { App } from "./app/App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
