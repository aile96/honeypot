import LoginClient from "./LoginClient";

// Tipi accettati per la query "from"
type Search = { from?: string | string[] };

export default async function Page({
  searchParams,
}: {
  // In Next 15 searchParams è (potenzialmente) una Promise
  searchParams?: Promise<Search>;
}) {
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "/feature";
  const sp = await searchParams;             // <- attendo la Promise
  const raw = sp?.from;
  const from = Array.isArray(raw) ? raw[0] : raw ?? basePath;
  return <LoginClient from={from} />;
}
