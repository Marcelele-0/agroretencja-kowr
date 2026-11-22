import { describe, expect, it } from "vitest";
import { escapeHtml, parcelsLabel, reportWindowHtml, reportWindowTitle } from "./openReport";
import type { ParcelReportSheet, ReportDocument } from "./reportBuilder";

const docOf = (n: number): ReportDocument => ({
  title: "Rusałka",
  analysisDate: "2019-07-15",
  generatedAt: "12:00",
  sheets: Array.from({ length: n }, () => ({ analysisDateLabel: "15 lipca 2019" }) as ParcelReportSheet),
});

describe("report window", () => {
  it("names the parcel count in Polish", () => {
    expect([1, 2, 4, 5, 12, 13, 22, 25].map(parcelsLabel)).toEqual([
      "1 działka", "2 działki", "4 działki", "5 działek", "12 działek", "13 działek", "22 działki", "25 działek",
    ]);
  });

  it("titles the window with the parcel count and date", () => {
    expect(reportWindowTitle(docOf(3))).toBe("Raport wody – 3 działki – 15 lipca 2019 – Rusałka");
    expect(reportWindowTitle(docOf(0))).toBe("Raport wody – 0 działek – Rusałka");
  });

  it("builds a standalone Polish document with the app styles, the sheets and a self-printing script", () => {
    const html = reportWindowHtml({
      title: "Raport <b>&",
      styles: '<link rel="stylesheet" href="http://localhost:5173/assets/app.css"><style>.x{}</style>',
      baseHref: "http://localhost:5173/analiza?dzialki=a",
      body: '<div class="rs-print-doc"><article class="rs-pdf-page">A</article></div>',
    });
    expect(html.startsWith("<!doctype html>")).toBe(true);
    expect(html).toContain('<html lang="pl" data-theme="light">');
    expect(html).toContain('<meta charset="utf-8">');
    expect(html).toContain('<meta name="viewport" content="width=device-width, initial-scale=1">');
    expect(html).toContain("<title>Raport &lt;b&gt;&amp;</title>");
    expect(html).toContain('<base href="http://localhost:5173/analiza?dzialki=a">');
    expect(html.indexOf("<base")).toBeLessThan(html.indexOf("assets/app.css"));
    expect(html).toContain("<style>.x{}</style>");
    expect(html).toContain('<body class="rs-report-window">');
    expect(html).toContain('<article class="rs-pdf-page">A</article>');
    expect(html).toContain('id="rs-report-print"');
    expect(html).toMatch(/<script>[\s\S]*window\.print\(\)[\s\S]*<\/script>/);
    // A pending stylesheet would hold a script placed after it.
    expect(html.indexOf("<script>")).toBeLessThan(html.indexOf("<link"));
    expect(html).not.toMatch(/window\.close|afterprint/);
  });

  it("escapes attribute and text markup", () => {
    expect(escapeHtml(`a"<>&`)).toBe("a&quot;&lt;&gt;&amp;");
  });
});
