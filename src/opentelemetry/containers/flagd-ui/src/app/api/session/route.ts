import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { jwtVerify } from "jose";

const enc = new TextEncoder();
const secret = () => enc.encode(process.env.FLAGD_UI_JWT_SECRET || "dev-secret");

// (opzionale) la route dipende da cookie: evita cache aggressive
// export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const cookieStore = await cookies(); // <- in Next 15 è Promise
    const token = cookieStore.get("flagdui_jwt")?.value;
    if (!token) return NextResponse.json({ authenticated: false });

    const { payload } = await jwtVerify(token, secret(), { algorithms: ["HS256"] });
    return NextResponse.json({ authenticated: true, sub: payload.sub, role: payload.role });
  } catch {
    return NextResponse.json({ authenticated: false });
  }
}
