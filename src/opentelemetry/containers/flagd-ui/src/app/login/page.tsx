"use client";

import { useState } from "react";

export default function LoginPage() {
  const [username, setU] = useState("");
  const [password, setP] = useState("");
  const [error, setError] = useState("");

  // read basePath from env
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await fetch(`${basePath}/api/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (r.ok) {
      window.location.href = basePath || "/";
    } else {
      setError("Invalid credentials");
    }
  };

  return (
    <div className="mx-auto mt-10 max-w-md rounded-2xl border p-6 shadow">
      <h1 className="mb-4 text-2xl font-semibold">Login</h1>
      <form onSubmit={onSubmit} className="flex flex-col gap-3">
        <input
          className="rounded border p-2"
          placeholder="Username"
          value={username}
          onChange={(e) => setU(e.target.value)}
        />
        <input
          className="rounded border p-2"
          placeholder="Password"
          type="password"
          value={password}
          onChange={(e) => setP(e.target.value)}
        />
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button
          className="rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700"
          type="submit"
        >
          Login
        </button>
      </form>
    </div>
  );
}
