import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.jsx";
import { initializeDemoMode } from "./demo-mode";
import "./styles.css";
import "./themes.css";
import "./jade-theme.css";
import "./glass-theme.css";
import "./civic-theme.css";
import "./workspace-layout.css";
import "./ambient-background.css";
import "./glass-refinement.css";
import "./edge-light.css";
import "./brand.css";
import "./theme-layout.css";
import "./document-cards.css";
import "./reader-layout.css";
import "./document-tags.css";
import "./civic-card-selection.css";
import "./article-card-materials.css";
import "./demo-theme.css";

initializeDemoMode();
createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
