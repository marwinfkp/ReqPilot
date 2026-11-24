// Same-origin proxy: the browser calls /api/* on this Next.js server, and this
// route forwards the call to the FastAPI backend. Keeping one origin means the
// HttpOnly session cookie just works and the OpenAI key never leaves the backend.
//
// (Next's built-in `rewrites` proxy was replaced by this route because on Windows
// it intermittently truncated large JSON responses at 128 KiB with ECONNRESET.
// Here the whole upstream body is read before answering, and an idempotent GET
// is retried once if the backend connection drops.)

const BACKEND = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
const REQUEST_HEADERS = ["cookie", "authorization", "content-type", "accept", "user-agent"];
const RESPONSE_HEADERS = ["content-type", "content-disposition", "cache-control"];

export const dynamic = "force-dynamic";

async function forward(request: Request, path: string[]): Promise<Response> {
  const search = new URL(request.url).search;
  const target = `${BACKEND}/api/${path.map(encodeURIComponent).join("/")}${search}`;
  const headers = new Headers();
  for (const name of REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const idempotent = request.method === "GET" || request.method === "HEAD";
  const body = idempotent ? undefined : await request.arrayBuffer();

  for (let attempt = 1; ; attempt++) {
    try {
      const upstream = await fetch(target, { method: request.method, headers, body, redirect: "manual", cache: "no-store" });
      const content = await upstream.arrayBuffer(); // throws if the connection drops mid-body
      const out = new Headers();
      for (const name of RESPONSE_HEADERS) {
        const value = upstream.headers.get(name);
        if (value) out.set(name, value);
      }
      for (const cookie of upstream.headers.getSetCookie()) out.append("set-cookie", cookie);
      return new Response(content, { status: upstream.status, headers: out });
    } catch (error) {
      if (idempotent && attempt < 2) continue; // safe to repeat a read-only request once
      const reason = error instanceof Error ? error.message : "unknown error";
      return Response.json({ detail: `Cannot reach the ReqPilot backend (${reason}). Is it running on port 8000?` }, { status: 502 });
    }
  }
}

type Context = { params: Promise<{ path: string[] }> };

async function handler(request: Request, context: Context) {
  const { path } = await context.params;
  return forward(request, path);
}

export { handler as DELETE, handler as GET, handler as PATCH, handler as POST, handler as PUT };
