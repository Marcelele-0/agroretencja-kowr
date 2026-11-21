import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Analysis } from "./pages/Analysis";
import { MapHome } from "./pages/MapHome";
import { Method } from "./pages/Method";

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<MapHome />} />
        <Route path="/analiza" element={<Analysis />} />
        <Route path="/metoda" element={<Method />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
