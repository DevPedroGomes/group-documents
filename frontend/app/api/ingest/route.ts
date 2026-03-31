import { proxyToBackend } from "@/lib/api-proxy";

export async function POST(req: Request) {
  return proxyToBackend(req, "/ingest");
}
