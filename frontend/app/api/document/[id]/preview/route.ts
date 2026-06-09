import { proxyToBackend } from "@/lib/api-proxy";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  return proxyToBackend(req, `/document/${id}/preview`, { method: "GET" });
}
