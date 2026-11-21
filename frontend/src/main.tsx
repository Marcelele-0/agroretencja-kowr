import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles/tokens.css";
import "./styles/rusalka.css";
import "./styles/app.css";

document.documentElement.dataset.theme = "light";

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
