import type { AnalysisResponse } from "./contract";

/**
 * Development-only sample of the backend answer, to build the server-fed sections before the backend exists.
 * Loaded only when import.meta.env.DEV is true and the URL has ?fixtures. Never part of a production build.
 */
export const BACKEND_FIXTURE: AnalysisResponse = {
  causes: {
    climate_pct: 45, soil_pct: 30, runoff_pct: 20, crop_pct: 5, method: "TreeSHAP (próbka deweloperska)",
    explanations: { klimat: "Brak opadu od 19 dni przy parowaniu 4,8 mm/dobę.", gleba: "Gleba lekka kat. I IUNG, pojemność wodna 68 mm.", splyw: "Spadek 4,2° wzdłuż pasa: ucieczka ok. 28% opadu nawalnego.", uprawa: "Kukurydza w fazie kwitnienia, Kc = 1,20." },
  },
  retention: {
    recommendations: [
      { id: "miedzyplon_mulcz", category: "agrotechnika", title: "Międzyplon i mulcz", rationale: "", delta_taw_mm: 18, runoff_reduction_pct: 10, priority_rank: 1 },
      { id: "pas_buforowy", category: "nwrm", title: "Pas buforowy 6 m", rationale: "", delta_taw_mm: 9, runoff_reduction_pct: 70, priority_rank: 2 },
      { id: "zastawka", category: "urzadzenie_wodne", title: "Zastawka na rowie", rationale: "", delta_taw_mm: 22, runoff_reduction_pct: 0, priority_rank: 3 },
    ],
    what_if_default: { baseline_yield_loss_pct: 31, scenario_yield_loss_pct: 18, delta_reserve_days: 9, saved_water_mm: 49, reference_dry_year: 2018 },
  },
};
