"""
Couche d'accès à la base de données — PostgreSQL (persistant), hébergée
gratuitement sur Neon (neon.tech) ou tout autre fournisseur Postgres.

Important : Render (le serveur qui fait tourner ce backend) a un disque
"éphémère" sur son offre gratuite — tout fichier écrit localement (comme
l'ancienne base SQLite) est effacé à chaque redémarrage. PostgreSQL, lui,
vit ailleurs (chez Neon), donc les données survivent aux redémarrages.

La variable d'environnement DATABASE_URL (fournie par Neon) doit être
renseignée dans les réglages "Environment" de Render.
"""
import os
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    role TEXT NOT NULL CHECK(role IN ('producteur','acheteur')),
    nom TEXT NOT NULL,
    identifiant TEXT NOT NULL UNIQUE,
    telephone TEXT NOT NULL,
    ville TEXT,
    culture TEXT,
    password_hash TEXT NOT NULL,
    note REAL DEFAULT 5.0,
    statut TEXT NOT NULL DEFAULT 'actif' CHECK(statut IN ('actif','suspendu')),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
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
);

CREATE TABLE IF NOT EXISTS contact_unlocks (
    id SERIAL PRIMARY KEY,
    acheteur_id INTEGER NOT NULL REFERENCES users(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    montant INTEGER NOT NULL DEFAULT 300,
    methode TEXT NOT NULL CHECK(methode IN ('Wave','MTN Money','Orange Money','Moov Money')),
    reference TEXT NOT NULL UNIQUE,
    statut TEXT NOT NULL DEFAULT 'en_attente' CHECK(statut IN ('en_attente','paye','echoue')),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS visits (
    id SERIAL PRIMARY KEY,
    route TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS avis (
    id SERIAL PRIMARY KEY,
    acheteur_id INTEGER NOT NULL REFERENCES users(id),
    producteur_id INTEGER NOT NULL REFERENCES users(id),
    note INTEGER NOT NULL CHECK(note BETWEEN 1 AND 5),
    commentaire TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(acheteur_id, producteur_id)
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


class PGConnection:
    """
    Enveloppe une connexion psycopg2 pour garder, dans le reste du code,
    la même façon d'écrire qu'avec sqlite3 : conn.execute(sql, params)
    renvoie directement un curseur sur lequel on peut faire .fetchone()
    ou .fetchall(), et les lignes se comportent comme des dictionnaires.
    """

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=None):
        cur = self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params if params is not None else ())
        return cur

    def executescript(self, sql):
        cur = self._raw.cursor()
        cur.execute(sql)
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
    raw = psycopg2.connect(DATABASE_URL)
    return PGConnection(raw)


def init_db(default_admin_hash):
    conn = get_conn()
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT id FROM admin WHERE id = 1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO admin (id, identifiant, password_hash) VALUES (1, %s, %s)",
            ("admin", default_admin_hash),
        )
    conn.commit()
    conn.close()
