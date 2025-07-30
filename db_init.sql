-- Tabel users
CREATE TABLE IF NOT EXISTS users (
    username VARCHAR(50) PRIMARY KEY,
    password_hash TEXT NOT NULL,
    name VARCHAR(100),
    role VARCHAR(20) DEFAULT 'user'
);

-- Tabel challenges (hanya VM)
CREATE TABLE IF NOT EXISTS challenges (
    id SERIAL PRIMARY KEY,
    vm_name VARCHAR(100) NOT NULL,
    vm_ip VARCHAR(50) NOT NULL,
    flag TEXT NOT NULL,
    points INTEGER DEFAULT 10
);

-- Tabel leaderboard
CREATE TABLE IF NOT EXISTS leaderboard (
    username VARCHAR(50) PRIMARY KEY REFERENCES users(username) ON DELETE CASCADE,
    name TEXT NOT NULL,
    score INTEGER DEFAULT 0,
    last_submit TIMESTAMP,
    answers JSONB DEFAULT '{}'::jsonb,
    used_hints JSONB DEFAULT '{}'::jsonb,
    active_times JSONB DEFAULT '{}'::jsonb
);