import Head from 'next/head';
import { useEffect, useState } from 'react';
import Layout from '../components/Layout';
import { useAuth } from '../providers/Auth.provider';
import Link from 'next/link';
import { useRouter } from 'next/router';

const LoginPage = () => {
  const router = useRouter();
  const { login, isAuthenticated } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isAuthenticated) router.replace('/');
  }, [isAuthenticated, router]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(username, password);
      router.replace('/');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  if (isAuthenticated) return null;

  return (
    <Layout>
      <Head><title>Login</title></Head>
      <div style={{ maxWidth: 420, margin: '40px auto', padding: 24, border: '1px solid #eee', borderRadius: 12 }}>
        <h1>Login</h1>
        <form onSubmit={onSubmit} style={{ display: 'grid', gap: 12 }}>
          <label>
            Username
            <input value={username} onChange={e => setUsername(e.target.value)} required style={{ width: '100%', padding: 8 }}/>
          </label>
          <label>
            Password
            <input type="password" value={password} onChange={e => setPassword(e.target.value)} required style={{ width: '100%', padding: 8 }}/>
          </label>
          <button type="submit" disabled={loading} style={{ padding: '10px 16px', borderRadius: 8 }}>
            {loading ? '...' : 'Login'}
          </button>
          {error && <p style={{ color: 'crimson' }}>{error}</p>}
        </form>
        <p style={{ marginTop: 12 }}>Do you have an account? <Link href="/register">Register</Link></p>
      </div>
    </Layout>
  );
};

export default LoginPage;
