const { Pool } = require('pg');

const pool = process.env.DATABASE_URL
  ? new Pool({ connectionString: process.env.DATABASE_URL })
  : new Pool({
      host: process.env.PGHOST || 'postgres',
      port: +(process.env.PGPORT || 5432),
      database: process.env.PGDATABASE || 'payments',
      user: process.env.PGUSER || 'payments_rw',
      password: process.env.PGPASSWORD || 'payments_rw_pwd',
      ssl: process.env.PGSSL === 'true' ? { rejectUnauthorized: false } : false,
    });

async function upsertPaymentMethod({ userId, cardHolderName, cardNumber, expMonth, expYear, cvv }) {
  const sql = `
    INSERT INTO payment_methods (user_id, card_holder_name, card_number, exp_month, exp_year, cvv)
    VALUES ($1,$2,$3,$4,$5,$6)
    ON CONFLICT (user_id) DO UPDATE SET
      card_holder_name = EXCLUDED.card_holder_name,
      card_number      = EXCLUDED.card_number,
      exp_month        = EXCLUDED.exp_month,
      exp_year         = EXCLUDED.exp_year,
      cvv              = EXCLUDED.cvv,
      updated_at       = NOW()
    RETURNING *;
  `;
  const vals = [userId, cardHolderName, cardNumber, expMonth, expYear, cvv];
  const { rows } = await pool.query(sql, vals);
  return rows[0];
}

async function getPaymentMethod(userId) {
  const { rows } = await pool.query(
    'SELECT * FROM payment_methods WHERE user_id = $1 LIMIT 1',
    [userId]
  );
  return rows[0] || null;
}

module.exports = { upsertPaymentMethod, getPaymentMethod };
