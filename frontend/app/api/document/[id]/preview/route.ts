import { proxyToBackend } from "@/lib/api-proxy";

export async function GET(req: Request, ctx: any) {
  const id = ctx.params.id;
  return proxyToBackend(req, `/document/${id}/preview`, { method: "GET" });
}
