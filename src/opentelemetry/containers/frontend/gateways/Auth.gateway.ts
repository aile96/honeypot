// Simple client for our auth API routes
type LoginBody = { username: string; password: string };
type RegisterBody = { username: string; password: string; email?: string; address?: string; zip?: string; city?: string; state?: string; country?: string; phone?: string };
type VerifyBody = { token: string };

export type VerifyResponse = { valid: boolean; payload?: unknown; error?: string };

const AuthGateway = () => ({
  async login(body: LoginBody): Promise<{ token: string }> {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
  async register(body: RegisterBody): Promise<{ status: string } | Record<string, unknown>> {
    const res = await fetch('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
  async verify(token: string): Promise<VerifyResponse> {
    const res = await fetch('/api/auth/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token } as VerifyBody),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
});

export default AuthGateway();
