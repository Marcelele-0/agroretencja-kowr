import type { ReportDocument } from "./reportBuilder";

/** Screen width of one A4 sheet in the report window (210 mm + 2 × 16 px gutter); narrower screens zoom the sheets out. */
const SHEET_SCREEN_PX = Math.ceil((210 * 96) / 25.4) + 32;

/**
 * Runs inside the report window, so print() belongs to that window (iOS suspends the opener tab's timers).
 * Sits before the stylesheets, which would otherwise hold it until they load. Fits the sheets to narrow
 * screens and prints once after load (stylesheets in), giving fonts and images up to 3 s more; never closes itself.
 */
const WINDOW_SCRIPT = `(function () {
  var root = document.documentElement;
  var printed = false;
  function printOnce() { if (printed) return; printed = true; setTimeout(function () { window.print(); }, 150); }
  function fit() { root.style.setProperty("--rs-report-fit", String(Math.min(1, root.clientWidth / ${SHEET_SCREEN_PX}))); }
  fit();
  window.addEventListener("resize", fit);
  document.addEventListener("click", function (e) {
    if (e.target && e.target.closest && e.target.closest("#rs-report-print")) { printed = true; window.print(); }
  });
  window.addEventListener("load", function () {
    var waits = [document.fonts && document.fonts.ready ? document.fonts.ready : null];
    Array.prototype.forEach.call(document.images, function (img) {
      if (!img.complete) waits.push(new Promise(function (r) { img.addEventListener("load", r); img.addEventListener("error", r); }));
    });
    var cap = new Promise(function (r) { setTimeout(r, 3000); });
    Promise.race([Promise.all(waits), cap]).then(printOnce, printOnce);
  });
  // A hung stylesheet (e.g. a font server) holds load: print anyway once the app styles apply (sticky toolbar).
  setTimeout(function () {
    var bar = document.querySelector(".rs-report-bar");
    if (bar && getComputedStyle(bar).position === "sticky") printOnce();
  }, 6000);
})();`;

export function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** "1 działka", "3 działki", "5 działek", "22 działki". */
export function parcelsLabel(n: number): string {
  if (n === 1) return "1 działka";
  const few = n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14);
  return `${n} ${few ? "działki" : "działek"}`;
}

/** Window title; the browser also offers it as the PDF file name. */
export function reportWindowTitle(doc: ReportDocument): string {
  const date = doc.sheets[0]?.analysisDateLabel;
  return ["Raport wody", parcelsLabel(doc.sheets.length), date, "Rusałka"].filter(Boolean).join(" – ");
}

export interface ReportWindowParts {
  title: string;
  /** Serialized <link>/<style> tags of the app. */
  styles: string;
  baseHref: string;
  theme?: string;
  /** The .rs-print-doc markup. */
  body: string;
}

/** Standalone document of the report window: app styles, the A4 sheets and a print toolbar (hidden in print). */
export function reportWindowHtml(p: ReportWindowParts): string {
  const title = escapeHtml(p.title);
  return `<!doctype html>
<html lang="pl" data-theme="${escapeHtml(p.theme ?? "light")}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<base href="${escapeHtml(p.baseHref)}">
<title>${title}</title>
<script>${WINDOW_SCRIPT}</script>
${p.styles}
</head>
<body class="rs-report-window">
<header class="rs-report-bar">
<div class="rs-report-bar__text"><b>${title}</b><span>W oknie drukowania wybierz „Zapisz jako PDF”, aby zapisać plik.</span></div>
<button type="button" class="ar-btn ar-btn--lime" id="rs-report-print"><span>Drukuj lub zapisz PDF</span></button>
</header>
${p.body}
</body>
</html>`;
}

/** Copies of the app's stylesheets: Vite dev injects <style> tags, the build links CSS files. */
function styleTags(doc: Document): string {
  const out: string[] = [];
  for (const el of doc.querySelectorAll<HTMLLinkElement | HTMLStyleElement>('link[rel~="stylesheet"], style')) {
    if (el.tagName === "LINK") {
      const link = el as HTMLLinkElement;
      if (!link.href) continue;
      const media = link.media ? ` media="${escapeHtml(link.media)}"` : "";
      out.push(`<link rel="stylesheet" href="${escapeHtml(link.href)}"${media}>`);
      continue;
    }
    let css = el.textContent ?? "";
    // Rules added through CSSOM leave the tag empty.
    if (!css.trim()) {
      try { css = Array.from(el.sheet?.cssRules ?? [], (r) => r.cssText).join("\n"); } catch { css = ""; }
    }
    if (css.trim()) out.push(`<style>${css.replace(/<\/style/gi, "<\\/style")}</style>`);
  }
  return out.join("\n");
}

/**
 * Opens the rendered print report (.rs-print-doc) in a new window that prints itself.
 * Must run synchronously in the click handler so popup blockers allow it; a blocked popup prints the current page.
 */
export function openPrintReport(doc: ReportDocument): "window" | "page" {
  const src = document.querySelector(".rs-print-doc");
  let win: Window | null = null;
  if (src) {
    try { win = window.open("", "_blank"); } catch { win = null; }
  }
  if (!src || !win) {
    window.print();
    return "page";
  }
  const sheets = src.cloneNode(true) as Element;
  sheets.removeAttribute("aria-hidden");
  try {
    win.document.open();
    win.document.write(reportWindowHtml({
      title: reportWindowTitle(doc),
      styles: styleTags(document),
      baseHref: document.baseURI,
      theme: document.documentElement.dataset.theme,
      body: sheets.outerHTML,
    }));
    win.document.close();
    return "window";
  } catch {
    win.close();
    window.print();
    return "page";
  }
}
