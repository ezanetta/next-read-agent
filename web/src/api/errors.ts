/** An API error with the server's typed body when it sent one (D-043's ErrorBody). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string | undefined;
  readonly retryAfterSeconds: number | undefined;

  constructor(status: number, message: string, code?: string, retryAfterSeconds?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
  }

  /** The demo's limit was reached (HTTP 429): the UI shows the demo-limit screen. */
  get isDemoLimit(): boolean {
    return this.status === 429;
  }
}

function retryAfter(response: Response, body: unknown): number | undefined {
  const fromBody =
    typeof body === "object" && body !== null && "retry_after_s" in body
      ? Number((body as { retry_after_s: unknown }).retry_after_s)
      : Number.NaN;
  if (Number.isFinite(fromBody)) return fromBody;
  const header = Number(response.headers.get("Retry-After"));
  return Number.isFinite(header) && header > 0 ? header : undefined;
}

export function toApiError(response: Response, body: unknown): ApiError {
  const parsed = typeof body === "object" && body !== null ? (body as Record<string, unknown>) : {};
  const message =
    typeof parsed.message === "string"
      ? parsed.message
      : `The server answered ${response.status}${response.statusText ? ` ${response.statusText}` : ""}`;
  const code = typeof parsed.error === "string" ? parsed.error : undefined;
  return new ApiError(response.status, message, code, retryAfter(response, body));
}

/** The /limit URL for an error, carrying the retry time when known. */
export function limitPath(retryAfterSeconds?: number): string {
  return retryAfterSeconds ? `/limit?retry=${Math.round(retryAfterSeconds)}` : "/limit";
}
