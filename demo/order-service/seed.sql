-- seed.sql — create schema and seed sample orders
CREATE TABLE IF NOT EXISTS orders (
    id            TEXT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    item          TEXT NOT NULL,
    amount        NUMERIC(10, 2) NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO orders (id, customer_name, item, amount, status) VALUES
  ('123', 'Alice',   'Laptop',       1299.99, 'shipped'),
  ('124', 'Bob',     'Keyboard',       89.00, 'delivered'),
  ('125', 'Carol',   'Monitor',       349.50, 'pending'),
  ('126', 'Dave',    'Headphones',    149.99, 'shipped'),
  ('127', 'Eve',     'Webcam',         79.00, 'pending'),
  ('128', 'Frank',   'Desk Chair',   399.00, 'delivered'),
  ('129', 'Grace',   'USB Hub',        39.99, 'pending'),
  ('130', 'Heidi',   'SSD Drive',    109.00, 'shipped'),
  ('131', 'Ivan',    'Mouse',          49.99, 'delivered'),
  ('132', 'Judy',    'Microphone',   129.00, 'pending')
ON CONFLICT (id) DO NOTHING;
