// Small fetch wrapper. All requests go to /api/* on the same origin; Next.js
// rewrites them to the FastAPI backend (see next.config.ts). The session is an
// HttpOnly cookie, so no token is ever handled in JavaScript.

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

type Options = { method?: string; body?: unknown; form?: FormData };

export async function api<T = unknown>(path: string, options: Options = {}): Promise<T> {
  const init: RequestInit = { method: options.method ?? "GET", credentials: "include" };
  if (options.form) {
    init.body = options.form;
    init.method = options.method ?? "POST";
  } else if (options.body !== undefined) {
    init.body = JSON.stringify(options.body);
    init.headers = { "Content-Type": "application/json" };
    init.method = options.method ?? "POST";
  }
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError(0, "Cannot reach the ReqPilot backend. Is it running on port 8000?");
  }
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    let detail: unknown;
    try {
      const data = await response.json();
      detail = data.detail;
      if (typeof data.detail === "string") message = data.detail;
      else if (Array.isArray(data.detail)) message = data.detail.map((d: { msg: string }) => d.msg).join("; ");
      else if (data.detail && typeof data.detail.message === "string") message = data.detail.message;
    } catch {
      /* not JSON */
    }
    if (response.status === 401 && typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
    throw new ApiError(response.status, message, detail);
  }
  return response.json() as Promise<T>;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unexpected error";
}
