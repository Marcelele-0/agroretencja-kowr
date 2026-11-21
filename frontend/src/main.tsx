import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles/tokens.css";
import "./styles/rusalka.css";
import "./styles/app.css";

// Follow the system theme; ?motyw=jasny|ciemny overrides it.
const forced = new URLSearchParams(location.search).get("motyw");
const dark = window.matchMedia("(prefers-color-scheme: dark)");
const apply = () => { document.documentElement.dataset.theme = forced === "ciemny" || (forced !== "jasny" && dark.matches) ? "dark" : "light"; };
apply();
dark.addEventListener("change", apply);

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
