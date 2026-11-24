import type { NextConfig } from "next";

// /api/* is forwarded to the FastAPI backend by app/api/[...path]/route.ts
// (backend address: BACKEND_URL, default http://127.0.0.1:8000).
const nextConfig: NextConfig = {};

export default nextConfig;
