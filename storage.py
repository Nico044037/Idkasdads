import os
import psycopg
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
FERNET_KEY = os.getenv("ENCRYPTION_KEY")

def _cipher():
    if not FERNET_KEY:
        raise RuntimeError("ENCRYPTION_KEY is missing from .env")
    return Fernet(FERNET_KEY.encode())

def _connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing from .env")
    return psycopg.connect(DATABASE_URL, sslmode="require")

def init_db():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oauth_users (
                    user_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    access_token BYTEA NOT NULL,
                    refresh_token BYTEA NOT NULL,
                    expires_at BIGINT NOT NULL,
                    authorized_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    joined BOOLEAN NOT NULL DEFAULT FALSE,
                    PRIMARY KEY (user_id, guild_id)
                )
            """)
        conn.commit()

def save_authorization(user_id, username, guild_id, access_token, refresh_token, expires_at):
    c = _cipher()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO oauth_users
                    (user_id, username, guild_id, access_token, refresh_token, expires_at, joined)
                VALUES (%s, %s, %s, %s, %s, %s, FALSE)
                ON CONFLICT (user_id, guild_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    access_token = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    expires_at = EXCLUDED.expires_at,
                    authorized_at = NOW()
            """, (
                user_id, username, guild_id,
                c.encrypt(access_token.encode()),
                c.encrypt(refresh_token.encode()),
                int(expires_at)
            ))
        conn.commit()

def get_authorized_user(user_id, guild_id):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT access_token, refresh_token, expires_at
                FROM oauth_users WHERE user_id=%s AND guild_id=%s
            """, (user_id, guild_id))
            row = cur.fetchone()
    if not row:
        return None
    c = _cipher()
    return {
        "access_token": c.decrypt(bytes(row[0])).decode(),
        "refresh_token": c.decrypt(bytes(row[1])).decode(),
        "expires_at": int(row[2])
    }

def refresh_user_token(user_id, guild_id, access_token, refresh_token, expires_at):
    c = _cipher()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE oauth_users SET access_token=%s, refresh_token=%s, expires_at=%s
                WHERE user_id=%s AND guild_id=%s
            """, (
                c.encrypt(access_token.encode()), c.encrypt(refresh_token.encode()),
                int(expires_at), user_id, guild_id
            ))
        conn.commit()

def set_joined(user_id, guild_id, joined=True):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE oauth_users SET joined=%s WHERE user_id=%s AND guild_id=%s",
                (joined, user_id, guild_id)
            )
        conn.commit()

def get_stats(guild_id):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*), COUNT(*) FILTER (WHERE joined=TRUE)
                FROM oauth_users WHERE guild_id=%s
            """, (guild_id,))
            row = cur.fetchone()
    return {"authorized": row[0], "joined": row[1]}