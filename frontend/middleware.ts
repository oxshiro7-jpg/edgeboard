import { NextResponse, type NextRequest } from "next/server";

// Optional site password. Set APP_PASSWORD on the server to require it (any
// username). Keeps strangers from using up the daily API-Football budget.
export function middleware(req: NextRequest) {
  const pass = process.env.APP_PASSWORD;
  if (!pass) return NextResponse.next();
  const h = req.headers.get("authorization") ?? "";
  if (h.startsWith("Basic ")) {
    try {
      const decoded = atob(h.slice(6));
      if (decoded.slice(decoded.indexOf(":") + 1) === pass) return NextResponse.next();
    } catch { /* malformed header */ }
  }
  return new NextResponse("Password required", { status: 401, headers: { "WWW-Authenticate": 'Basic realm="EdgeBoard"' } });
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico|api/health|api/tasks).*)"] };
