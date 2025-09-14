import { NextResponse } from "next/server";

export async function POST() {
  const res = NextResponse.json({ ok: true });
  res.cookies.set({ name: "flagdui_jwt", value: "", path: "/feature", maxAge: 0 });
  return res;
}
