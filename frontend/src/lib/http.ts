export class SourceError extends Error {
  constructor(public readonly source: string, message: string, public readonly status?: number) { super(message); }
}
export function isAbort(e: unknown): boolean {
  return e instanceof DOMException && e.name === "AbortError";
}

/** GET/POST with one hard timeout that also covers reading the body. */
export async function request(url: string, ms: number, signal?: AbortSignal, init?: RequestInit): Promise<{ status: number; ok: boolean; text: string }> {
  if (signal?.aborted) throw signal.reason ?? new DOMException("aborted", "AbortError");
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(new DOMException("timeout", "TimeoutError")), ms);
  const onAbort = () => ctl.abort(signal?.reason ?? new DOMException("aborted", "AbortError"));
  signal?.addEventListener("abort", onAbort, { once: true });
  try {
    const res = await fetch(url, { ...init, signal: ctl.signal });
    const text = await res.text();
    return { status: res.status, ok: res.ok, text };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
}

const deadPrefixes = new Set<string>();

/** Calls the same-origin proxy first, remembering missing endpoints and falling back to the direct URL on static hosting. */
export async function requestViaProxy(
  prefix: string,
  proxyUrl: string,
  directUrl: string,
  ms: number,
  signal?: AbortSignal,
): Promise<{ status: number; ok: boolean; text: string }> {
  if (typeof window === "undefined" || deadPrefixes.has(prefix)) {
    return request(directUrl, ms, signal);
  }
  try {
    const res = await request(proxyUrl, ms, signal);
    const head = res.text.trimStart().toLowerCase();
    if (res.status !== 404 && !head.startsWith("<!") && !head.startsWith("<html")) return res;
    deadPrefixes.add(prefix);
  } catch (e) {
    if (isAbort(e)) throw e;
  }
  return request(directUrl, ms, signal);
}

/** Polish message for a failed call to a named source. */
export function sourceFailure(source: string, e: unknown, status?: number): SourceError {
  if (status === 429) return new SourceError(source, `${source} chwilowo odrzuca zapytania: limit zapytań z tego adresu sieci jest wyczerpany. Spróbuj za minutę.`, 429);
  if (status) return new SourceError(source, `${source} zwróciło błąd HTTP ${status}.`, status);
  const timeout = e instanceof DOMException && e.name === "TimeoutError";
  return new SourceError(source, timeout ? `${source} nie odpowiada w wyznaczonym czasie.` : `${source} jest niedostępne.`);
}
