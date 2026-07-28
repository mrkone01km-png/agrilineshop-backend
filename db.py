"""
Couche d'accès à la base de données — PostgreSQL (persistant), hébergée
gratuitement sur Neon (neon.tech) ou tout autre fournisseur Postgres.

On utilise ici pg8000, un pilote PostgreSQL écrit entièrement en Python
(pas de composant compilé) : psycopg2 posait un problème d'incompatibilité
binaire avec la version de Python utilisée par Render. pg8000 évite ce
genre de souci puisqu'il n'y a rien à compiler.

Important : Render (le serveur qui fait tourner ce backend) a un disque
"éphémère" sur son offre gratuite — tout fichier écrit localement (comme
l'ancienne base SQLite) est effacé à chaque redémarrage. PostgreSQL, lui,
vit ailleurs (chez Neon), donc les données survivent aux redémarrages.

La variable d'environnement DATABASE_URL (fournie par Neon) doit être
renseignée dans les réglages "Environment" de Render.
"""
import os
from urllib.parse import urlparse
import pg8000.dbapi

DATABASE_URL = os.environ.get("DATABASE_URL", "")

SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        role TEXT NOT NULL CHECK(role IN ('producteur','acheteur')),
        nom TEXT NOT NULL,
        identifiant TEXT NOT NULL UNIQUE,
        telephone TEXT NOT NULL,
        pays TEXT,
        ville TEXT,
        culture TEXT,
        password_hash TEXT NOT NULL,
        note REAL DEFAULT 5.0,
        statut TEXT NOT NULL DEFAULT 'actif' CHECK(statut IN ('actif','suspendu')),
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS products (
        id SERIAL PRIMARY KEY,
        producteur_id INTEGER NOT NULL REFERENCES users(id),
        nom TEXT NOT NULL,
        prix INTEGER NOT NULL,
        quantite TEXT,
        categorie TEXT,
        description TEXT,
        image1 TEXT NOT NULL,
        image2 TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS contact_unlocks (
        id SERIAL PRIMARY KEY,
        acheteur_id INTEGER NOT NULL REFERENCES users(id),
        product_id INTEGER NOT NULL REFERENCES products(id),
        montant INTEGER NOT NULL DEFAULT 300,
        methode TEXT NOT NULL CHECK(methode IN ('Wave','MTN Money','Orange Money','Moov Money')),
        reference TEXT NOT NULL UNIQUE,
        statut TEXT NOT NULL DEFAULT 'en_attente' CHECK(statut IN ('en_attente','paye','echoue')),
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS visits (
        id SERIAL PRIMARY KEY,
        route TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS avis (
        id SERIAL PRIMARY KEY,
        acheteur_id INTEGER NOT NULL REFERENCES users(id),
        producteur_id INTEGER NOT NULL REFERENCES users(id),
        note INTEGER NOT NULL CHECK(note BETWEEN 1 AND 5),
        commentaire TEXT,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(acheteur_id, producteur_id)
    )""",
    """CREATE TABLE IF NOT EXISTS admin (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        identifiant TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        prix_deblocage INTEGER NOT NULL DEFAULT 300,
        wave_numero TEXT DEFAULT '',
        mtn_numero TEXT DEFAULT '',
        orange_numero TEXT DEFAULT '',
        moov_numero TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS prix_categories (
        categorie TEXT PRIMARY KEY,
        prix INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS agents (
        id SERIAL PRIMARY KEY,
        nom TEXT NOT NULL,
        code TEXT NOT NULL UNIQUE,
        telephone TEXT,
        total_paye INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
    )""",
]


class DictCursor:
    """Habille un curseur pg8000 pour renvoyer des lignes accessibles comme
    des dictionnaires (row["colonne"]), au lieu de simples tuples."""

    def __init__(self, raw_cursor):
        self._cur = raw_cursor

    def _columns(self):
        return [d[0] for d in self._cur.description] if self._cur.description else []

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        return dict(zip(self._columns(), row))

    def fetchall(self):
        cols = self._columns()
        return [dict(zip(cols, row)) for row in self._cur.fetchall()]


class PGConnection:
    """Enveloppe une connexion pg8000 pour garder, dans le reste du code,
    la même façon d'écrire qu'avec sqlite3 : conn.execute(sql, params)
    renvoie directement un curseur sur lequel on peut faire .fetchone()
    ou .fetchall(), avec des lignes accessibles comme des dictionnaires."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=None):
        cur = self._raw.cursor()
        cur.execute(sql, params if params is not None else ())
        return DictCursor(cur)

    def executescript(self, sql_statements):
        cur = self._raw.cursor()
        for statement in sql_statements:
            cur.execute(statement)
        cur.close()

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL n'est pas configurée. Ajoute-la dans Render, "
            "Environment, avec la chaîne de connexion fournie par Neon."
        )
    parsed = urlparse(DATABASE_URL)
    raw = pg8000.dbapi.connect(
        user=parsed.username,
        password=parsed.password,
        host=parsed.hostname,
        port=parsed.port or 5432,
        database=parsed.path.lstrip("/"),
        ssl_context=True,
    )
    return PGConnection(raw)


def init_db(default_admin_hash):
    conn = get_conn()
    conn.executescript(SCHEMA_STATEMENTS)
    # Migration douce (ne supprime jamais de données) pour les bases déjà
    # créées avant l'ajout de la colonne "pays".
    conn.executescript(["ALTER TABLE users ADD COLUMN IF NOT EXISTS pays TEXT"])
    conn.executescript(["ALTER TABLE users ADD COLUMN IF NOT EXISTS agent_id INTEGER REFERENCES agents(id)"])
    conn.executescript(["ALTER TABLE users ADD COLUMN IF NOT EXISTS latitude REAL"])
    conn.executescript(["ALTER TABLE users ADD COLUMN IF NOT EXISTS longitude REAL"])
    row = conn.execute("SELECT id FROM admin WHERE id = 1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO admin (id, identifiant, password_hash) VALUES (1, %s, %s)",
            ("admin", default_admin_hash),
        )
    conn.commit()
    conn.close()
