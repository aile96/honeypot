// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

function hasSessionCookie(): boolean {
  if (typeof document === "undefined") return false;
  // cookie created by API /feature/api/login
  return document.cookie
    .split(";")
    .some((c) => c.trim().startsWith("flagdui_jwt"));
}

export default function NavBar() {
  const pathname = usePathname();
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    const check = () => setAuthed(hasSessionCookie());
    check();
    const i = setInterval(check, 2000);
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", check);
    return () => {
      clearInterval(i);
      window.removeEventListener("focus", check);
      document.removeEventListener("visibilitychange", check);
    };
  }, []);

  const goToLogin = () => {
    window.location.href = `/feature/login`;
  };

  const doLogout = async () => {
    try {
      await fetch("/feature/api/logout", { method: "POST" });
    } catch {
      // ignore network errors
    } finally {
      setAuthed(false);
      goToLogin();
    }
  };

  return (
    <nav className="bg-gray-800 p-4 sm:p-6">
      <div className="container mx-auto flex items-center justify-between">
        <Link href="/" className="text-xl font-bold text-white">
          Flagd Configurator
        </Link>

        <div className="flex items-center gap-4">
          <ul className="flex space-x-2 sm:space-x-4">
            <li>
              <Link
                href="/"
                className={`rounded-md px-3 py-2 text-sm font-medium ${
                  pathname === "/"
                    ? "bg-blue-700 text-white underline underline-offset-4"
                    : "text-gray-300 hover:bg-gray-700 hover:text-white"
                } transition-all duration-200`}
              >
                Basic
              </Link>
            </li>
            <li>
              <Link
                href="/advanced"
                className={`rounded-md px-3 py-2 text-sm font-medium ${
                  pathname === "/advanced"
                    ? "bg-blue-700 text-white underline underline-offset-4"
                    : "text-gray-300 hover:bg-gray-700 hover:text-white"
                } transition-all duration-200`}
              >
                Advanced
              </Link>
            </li>
          </ul>

          <button
            onClick={authed ? doLogout : goToLogin}
            className="rounded-md bg-gray-700 px-3 py-2 text-sm font-medium text-gray-100 hover:bg-gray-600 transition-all duration-200"
            aria-label={authed ? "Logout" : "Login"}
          >
            {authed ? "Logout" : "Login"}
          </button>
        </div>
      </div>
    </nav>
  );
}
