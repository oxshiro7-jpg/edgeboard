// Liveness check for the host. Does not touch the backend or the football API.
export function GET() {
  return Response.json({ status: "ok" });
}
