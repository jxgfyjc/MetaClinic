// Next.js Edge Middleware — route guard (updatev2_2 §1c)
// Patient-accessible routes: /intake, /patient, /login, /api
// All other routes require a doctor_token cookie.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Route prefixes that DON'T require doctor login
const PUBLIC_PREFIXES = ["/intake", "/patient", "/login", "/api"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Allow public routes through
  if (PUBLIC_PREFIXES.some((p) => pathname === p || pathname.startsWith(p + "/"))) {
    return NextResponse.next();
  }

  // Static assets / Next internals (paths with a dot are files)
  if (pathname.startsWith("/_next") || pathname.startsWith("/favicon") || pathname.includes(".")) {
    return NextResponse.next();
  }

  // Check for doctor_token cookie (httpOnly cookie set by /api/auth/login).
  // The real auth/role check still happens server-side on every API call
  // via Depends(require_doctor). Here we only guard the page shell.
  const token = request.cookies.get("doctor_token")?.value;
  if (token) {
    return NextResponse.next();
  }

  // No token → redirect to login
  const loginUrl = new URL("/login", request.url);
  loginUrl.searchParams.set("redirect", pathname);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
