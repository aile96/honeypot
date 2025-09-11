import Head from 'next/head';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import Layout from '../components/Layout';
import { useAuth } from '../providers/Auth.provider';
import Link from 'next/link';

const RegisterPage = () => {
  const router = useRouter();
  const { register, isAuthenticated } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const [address, setAddress] = useState('');
  const [zip, setZip] = useState('');
  const [city, setCity] = useState('');
  const [state, setState] = useState('');
  const [country, setCountry] = useState('');
  const [phone, setPhone] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isAuthenticated) router.replace('/');
  }, [isAuthenticated, router]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    // ... set states
    try {
      await register(
      username,
      password,
      email || undefined,
      address || undefined,
      zip || undefined,
      city || undefined,
      state || undefined,
      country || undefined,
      phone || undefined,
    );
      router.replace('/');                 // <-- redirect dopo auto-login
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Registration failed');
    } finally {
      setLoading(false);
    }
  };

  if (isAuthenticated) return null;

  return (
    <Layout>
      <Head><title>Register</title></Head>
      <div style={{ maxWidth: 420, margin: '40px auto', padding: 24, border: '1px solid #eee', borderRadius: 12 }}>
        <h1>Register</h1>
        <form onSubmit={onSubmit} style={{ display: 'grid', gap: 12 }}>
          <label>
            Username
            <input value={username} onChange={e => setUsername(e.target.value)} required style={{ width: '100%', padding: 8 }}/>
          </label>
          <label>
            Password
            <input type="password" value={password} onChange={e => setPassword(e.target.value)} required style={{ width: '100%', padding: 8 }}/>
          </label>
          <label>
            Email (optional)
            <input value={email} onChange={e => setEmail(e.target.value)} style={{ width: '100%', padding: 8 }}/>
          </label>
          <label>
            Street Address (optional)
            <input value={address} onChange={e => setAddress(e.target.value)} style={{ width: '100%', padding: 8 }}/>
          </label>
          <label>
            Zip Code (optional)
            <input value={zip} onChange={e => setZip(e.target.value)} style={{ width: '100%', padding: 8 }}/>
          </label>
          <label>
            City (optional)
            <input value={city} onChange={e => setCity(e.target.value)} style={{ width: '100%', padding: 8 }}/>
          </label>
          <label>
            State (optional)
            <input value={state} onChange={e => setState(e.target.value)} style={{ width: '100%', padding: 8 }}/>
          </label>
          <label>
            Country (optional)
            <input value={country} onChange={e => setCountry(e.target.value)} style={{ width: '100%', padding: 8 }}/>
          </label>
          <label>
            Phone (optional)
            <input value={phone} onChange={e => setPhone(e.target.value)} style={{ width: '100%', padding: 8 }}/>
          </label>
          <button type="submit" disabled={loading} style={{ padding: '10px 16px', borderRadius: 8 }}>
            {loading ? '...' : 'Create account'}
          </button>
          {error && <p style={{ color: 'crimson' }}>{error}</p>}
        </form>
        <p style={{ marginTop: 12 }}>Do you already have an account? <Link href="/login">Login</Link></p>
      </div>
    </Layout>
  );
};

export default RegisterPage;
