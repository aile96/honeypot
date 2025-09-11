import type { NextApiHandler } from 'next';

const handler: NextApiHandler = async (req, res) => {
  const { AUTH_BASE_URL = '' } = process.env;
  if (req.method !== 'POST') return res.status(405).end();
  try {
    const r = await fetch(`${AUTH_BASE_URL}/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body || {}),
    });
    const text = await r.text();
    res.status(r.status).send(text);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : 'proxy_error';
    res.status(500).json({ error: msg });
  }
};

export default handler;
