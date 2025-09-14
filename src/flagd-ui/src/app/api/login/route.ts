// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
import { NextResponse } from "next/server";
import { SignJWT } from "jose";

const enc = new TextEncoder();
const secret = () => enc.encode(process.env.FLAGD_UI_JWT_SECRET || "dev-secret");

export async function POST(req: Request) {
  const { username, password } = await req.json();
  if (username !== process.env.FLAGD_UI_USER || password !== process.env.FLAGD_UI_PASS) {
    return NextResponse.json({ ok: false, error: "Unauthorized" }, { status: 401 });
  }

  const jwt = await new SignJWT({ sub: username, role: "admin" })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime("8h")
    .sign(secret());

  const res = NextResponse.json({ ok: true });
  res.cookies.set({
    name: "flagdui_jwt",
    value: jwt,
    httpOnly: false,
    secure: false,
    sameSite: "lax",
    path: "/feature",
    maxAge: 60 * 60 * 8,
  });
  return res;
}
