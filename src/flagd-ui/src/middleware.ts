import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { jwtVerify } from "jose";

const enc = new TextEncoder();
const secret = enc.encode(process.env.FLAGD_UI_JWT_SECRET || "dev-secret");

const isPublic = (p: string) =>
  p.startsWith(`/_next`) ||
  p === `/favicon.ico` ||
  p.startsWith(`/api/login`) ||
  p.startsWith(`/api/logout`) ||
  p.startsWith(`/api/session`) ||
  p.startsWith(`/login`) ||
  p.startsWith(`/readme`);

const needsAuth = (p: string) =>
  p.startsWith(`/advanced`) ||
  p.startsWith(`/api/write-to-file`) ||
  p.startsWith(`/api/read-file`) ||
  p === `/` ||
  p === ``;

const isApiPath = (p: string) =>
  p === "/api" ||
  p.startsWith("/api/");

export async function middleware(req: NextRequest) {
  const url = req.nextUrl;
  const base = url.basePath || "";
  const { pathname, search } = req.nextUrl;
  const token = req.cookies.get("flagdui_jwt")?.value;
  
  if (isPublic(pathname) || !needsAuth(pathname)) {
    if(pathname.startsWith(`/login`) && token) {
      try {
        await jwtVerify(token, secret, { algorithms: ["HS256"] });
        return NextResponse.redirect(new URL(base || "/", req.url));
      } catch {return NextResponse.next();}
    }
    return NextResponse.next();
  }
  
  if (!token) {
    if (isApiPath(pathname)) {
      // per API: nessun redirect, solo 401 JSON
      return NextResponse.json(
        { ok: false, error: "Unauthorized" },
        {
          status: 401,
          headers: { "WWW-Authenticate": 'Bearer realm="feature"' },
        }
      );
    }
    const url = req.nextUrl.clone();
    url.pathname = `/login`;
    url.search = "";
    if(pathname.startsWith(`/login`)) url.searchParams.set("from", pathname + (search || ""));
    return NextResponse.redirect(url);
  }

  try {
    await jwtVerify(token, secret, { algorithms: ["HS256"] });
    return NextResponse.next();
  } catch {
    if (isApiPath(pathname)) {
      // per API: nessun redirect, solo 401 JSON
      return NextResponse.json(
        { ok: false, error: "Unauthorized" },
        {
          status: 401,
          headers: { "WWW-Authenticate": 'Bearer realm="feature"' },
        }
      );
    }
    const url = req.nextUrl.clone();
    url.pathname = `/login`;
    url.search = "";
    if(pathname.startsWith(`/login`)) url.searchParams.set("from", pathname + (search || ""));
    return NextResponse.redirect(url);
  }
}

export const config = {
  matcher: ['/', '/((?!_next/static|_next/image|favicon.ico).*)'],
};
