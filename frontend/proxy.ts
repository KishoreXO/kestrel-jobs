import { NextResponse, type NextRequest } from "next/server";

// The dashboard only serves this machine. Checking the Host header blocks DNS-rebinding attacks
// (a web page you visit pointing its own domain at 127.0.0.1), and checking Origin blocks other
// sites from making your browser change settings or start scans behind your back.
const LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]"];
const EXTRA_HOSTS = (process.env.ALLOWED_HOSTS ?? "")
  .split(",")
  .map((h) => h.trim().toLowerCase())
  .filter(Boolean);

function hostname(hostHeader: string): string {
  const h = hostHeader.trim().toLowerCase();
  return h.startsWith("[") ? h.slice(0, h.indexOf("]") + 1) : h.split(":")[0];
}

const allowed = (host: string) => LOCAL_HOSTS.includes(host) || EXTRA_HOSTS.includes(host);

export function proxy(request: NextRequest) {
  const host = hostname(request.headers.get("host") ?? "");
  if (!allowed(host)) {
    return new NextResponse("Kestrel only accepts requests addressed to this computer.", { status: 403 });
  }
  const origin = request.headers.get("origin");
  if (origin && request.method !== "GET" && request.method !== "HEAD") {
    let originHost = "";
    try {
      originHost = hostname(new URL(origin).host);
    } catch {
      /* "null" or malformed: rejected below */
    }
    if (!allowed(originHost)) {
      return new NextResponse("Cross-site requests are not allowed.", { status: 403 });
    }
  }
  return NextResponse.next();
}

export const config = {
  matcher: "/:path*",
};
