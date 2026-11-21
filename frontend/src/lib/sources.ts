export interface SourceEntry {
  name: string;
  what: string;
  url: string;
  retrievedAt: Date | null;
  resolution: string;
  license: string;
}
export const STATIC_SOURCES = {
  ortho: { name: "GUGiK, ortofotomapa (WMTS i WMS)", what: "podkład mapy", url: "https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMTS/StandardResolution", resolution: "do 0,25 m", license: "otwarte dane PZGiK" },
  kieg: { name: "GUGiK, Krajowa Integracja Ewidencji Gruntów (WMS)", what: "granice i numery działek na mapie", url: "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow", resolution: "działka ewidencyjna", license: "otwarte dane PZGiK" },
} as const;
