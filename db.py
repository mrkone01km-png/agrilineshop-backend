"""
Couche d'accès à la base de données (SQLite).
En production, on remplacerait SQLite par PostgreSQL/MySQL, mais le schéma
et les requêtes ci-dessous restent quasiment identiques.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "agrilineshop.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK(role IN ('producteur','acheteur')),
    nom TEXT NOT NULL,
    identifiant TEXT NOT NULL UNIQUE,
    telephone TEXT NOT NULL,
    ville TEXT,
    culture TEXT,
    password_hash TEXT NOT NULL,
    note REAL DEFAULT 5.0,
    statut TEXT NOT NULL DEFAULT 'actif' CHECK(statut IN ('actif','suspendu')),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    producteur_id INTEGER NOT NULL REFERENCES users(id),
    nom TEXT NOT NULL,
    prix INTEGER NOT NULL,
    quantite TEXT,
    categorie TEXT,
    description TEXT,
    image1 TEXT NOT NULL,
    image2 TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contact_unlocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    acheteur_id INTEGER NOT NULL REFERENCES users(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    montant INTEGER NOT NULL DEFAULT 300,
    methode TEXT NOT NULL CHECK(methode IN ('Wave','MTN Money','Orange Money','Moov Money')),
    reference TEXT NOT NULL UNIQUE,
    statut TEXT NOT NULL DEFAULT 'en_attente' CHECK(statut IN ('en_attente','paye','echoue')),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS admin (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    identifiant TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    prix_deblocage INTEGER NOT NULL DEFAULT 300,
    wave_numero TEXT DEFAULT '',
    mtn_numero TEXT DEFAULT '',
    orange_numero TEXT DEFAULT '',
    moov_numero TEXT DEFAULT ''
);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _column_exists(conn, table, column):
    cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols


def init_db(default_admin_hash):
    conn = get_conn()

    # Migration défensive : si une version précédente de la base existe déjà
    # (sans les colonnes récentes), on la recrée proprement plutôt que de
    # planter au premier appel. Sans conséquence tant qu'aucune vraie donnée
    # de production ne s'est encore accumulée.
    for table in ("users", "admin"):
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists:
            needs_migration = (
                (table == "users" and not _column_exists(conn, "users", "identifiant"))
                or (table == "admin" and not _column_exists(conn, "admin", "prix_deblocage"))
            )
            if needs_migration and table == "users":
                # products et contact_unlocks référencent users(id) : on les
                # recrée aussi pour éviter des références orphelines.
                conn.execute("DROP TABLE IF EXISTS contact_unlocks")
                conn.execute("DROP TABLE IF EXISTS products")
                conn.execute("DROP TABLE IF EXISTS users")
            elif needs_migration:
                conn.execute(f"DROP TABLE IF EXISTS {table}")

    conn.executescript(SCHEMA)
    row = conn.execute("SELECT id FROM admin WHERE id = 1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO admin (id, identifiant, password_hash) VALUES (1, ?, ?)",
            ("admin", default_admin_hash),
        )
    conn.commit()
    conn.close()
