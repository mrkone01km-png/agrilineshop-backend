"""
AgriLineShop — API backend (testé de bout en bout).

Lancer en local : python3 app.py
Déployé en ligne sur Render : voir README.md.

Ce fichier est volontairement dans un seul module pour rester lisible comme
point de départ ; en production on séparerait les routes en blueprints
(auth, produits, admin) et on passerait sur PostgreSQL + un vrai service
de stockage d'images (S3, Cloudinary...).
"""
from flask import Flask, request, jsonify, Response
from werkzeug.security import generate_password_hash, check_password_hash
import uuid
import os
import datetime
import time
import csv
import io
import requests

import db
from auth_utils import make_token, require_role

app = Flask(__name__)

METHODES_VALIDES = {"Wave", "MTN Money", "Orange Money", "Moov Money"}
UNLOCK_DUREE_MINUTES = 5


@app.after_request
def add_cors_headers(response):
    # Autorise le site (hébergé sur Netlify, donc un autre nom de domaine)
    # à appeler cette API. À restreindre à ton propre domaine en production.
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    return response


@app.get("/")
def accueil_api():
    return jsonify({
        "message": "L'API AgriLineShop fonctionne normalement.",
        "info": "Cette adresse sert de serveur pour le site — le site lui-même est sur agrilineshop.netlify.app",
        "exemple": "/api/produits"
    })


@app.route("/<path:path>", methods=["OPTIONS"])
def options_handler(path):
    return "", 204


@app.before_request
def track_visit():
    # Chaque appel à la liste publique des produits est compté comme une visite.
    if request.path == "/api/produits" and request.method == "GET":
        conn = db.get_conn()
        conn.execute("INSERT INTO visits (route) VALUES (%s)", (request.path,))
        conn.commit()
        conn.close()


def get_prix_deblocage(conn):
    row = conn.execute("SELECT prix_deblocage FROM admin WHERE id = 1").fetchone()
    return row["prix_deblocage"] if row else 300


def get_prix_pour_categorie(conn, categorie):
    """Renvoie le prix personnalisé de la catégorie (ex : cacao, café, coton...)
    s'il existe, sinon le prix standard."""
    if categorie:
        row = conn.execute("SELECT prix FROM prix_categories WHERE categorie = %s", (categorie,)).fetchone()
        if row:
            return row["prix"]
    return get_prix_deblocage(conn)


def geocoder_ville(ville, pays):
    """
    Convertit une ville + pays en coordonnées (latitude, longitude), via le
    service gratuit OpenStreetMap Nominatim (aucune clé, aucun coût). En cas
    d'échec (ville introuvable, service indisponible...), renvoie (None, None)
    sans jamais bloquer l'inscription.
    """
    if not ville:
        return None, None
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{ville}, {pays or ''}", "format": "json", "limit": 1},
            headers={"User-Agent": "AgriLineShop/1.0 (contact: agrilineshop@gmail.com)"},
            timeout=5,
        )
        results = resp.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception:
        pass
    return None, None


# ---------------------------------------------------------------------------
# Authentification producteur / acheteur (par identifiant, pas par téléphone)
# ---------------------------------------------------------------------------

@app.post("/api/auth/register")
def register():
    data = request.get_json(force=True) or {}
    role = data.get("role")
    nom = (data.get("nom") or "").strip()
    identifiant = (data.get("identifiant") or "").strip()
    telephone = (data.get("telephone") or "").strip()
    mot_de_passe = data.get("mot_de_passe") or ""

    if role not in ("producteur", "acheteur"):
        return jsonify({"erreur": "Le rôle doit être 'producteur' ou 'acheteur'."}), 400
    if not nom or not identifiant or len(mot_de_passe) < 6:
        return jsonify({"erreur": "Nom, identifiant et mot de passe (6+ caractères) requis."}), 400

    conn = db.get_conn()
    existe = conn.execute("SELECT id FROM users WHERE identifiant = %s", (identifiant,)).fetchone()
    if existe:
        conn.close()
        return jsonify({"erreur": "Cet identifiant est déjà utilisé."}), 409

    agent_id = None
    code_agent = (data.get("code_agent") or "").strip()
    if code_agent:
        agent = conn.execute("SELECT id FROM agents WHERE code = %s", (code_agent,)).fetchone()
        if agent:
            agent_id = agent["id"]

    lat, lon = geocoder_ville(data.get("ville"), data.get("pays"))
    cur = conn.execute(
        """INSERT INTO users (role, nom, identifiant, telephone, pays, ville, culture, password_hash, agent_id, latitude, longitude)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (role, nom, identifiant, telephone, data.get("pays"), data.get("ville"), data.get("culture"),
         generate_password_hash(mot_de_passe), agent_id, lat, lon),
    )
    user_id = cur.fetchone()["id"]
    conn.commit()
    conn.close()

    token = make_token({"user_id": user_id, "role": role, "nom": nom})
    return jsonify({"message": "Compte créé.", "token": token,
                     "utilisateur": {"id": user_id, "role": role, "nom": nom, "identifiant": identifiant,
                                      "telephone": telephone, "pays": data.get("pays"),
                                      "ville": data.get("ville"), "culture": data.get("culture")}}), 201


@app.post("/api/auth/login")
def login():
    data = request.get_json(force=True) or {}
    identifiant = (data.get("identifiant") or "").strip()
    mot_de_passe = data.get("mot_de_passe") or ""
    role = data.get("role")  # 'producteur' ou 'acheteur', pour chercher dans le bon rôle

    conn = db.get_conn()
    if role in ("producteur", "acheteur"):
        user = conn.execute(
            "SELECT * FROM users WHERE identifiant = %s AND role = %s", (identifiant, role)
        ).fetchone()
    else:
        user = conn.execute("SELECT * FROM users WHERE identifiant = %s", (identifiant,)).fetchone()
    conn.close()

    if not user or not check_password_hash(user["password_hash"], mot_de_passe):
        return jsonify({"erreur": "Identifiant ou mot de passe incorrect."}), 401
    if user["statut"] == "suspendu":
        return jsonify({"erreur": "Ce compte a été suspendu par l'administrateur."}), 403

    token = make_token({"user_id": user["id"], "role": user["role"], "nom": user["nom"]})
    return jsonify({"token": token, "utilisateur": {
        "id": user["id"], "nom": user["nom"], "role": user["role"], "ville": user["ville"],
        "identifiant": user["identifiant"], "telephone": user["telephone"], "culture": user["culture"],
        "pays": user.get("pays")
    }})


# ---------------------------------------------------------------------------
# Statistiques publiques (page d'accueil) — pas besoin d'être connecté
# ---------------------------------------------------------------------------

@app.get("/api/stats-publiques")
def stats_publiques():
    conn = db.get_conn()
    comptes_acheteurs = conn.execute("SELECT COUNT(*) c FROM users WHERE role='acheteur'").fetchone()["c"]
    comptes_producteurs = conn.execute("SELECT COUNT(*) c FROM users WHERE role='producteur'").fetchone()["c"]
    visites = conn.execute("SELECT COUNT(*) c FROM visits").fetchone()["c"]
    prix = get_prix_deblocage(conn)
    conn.close()
    return jsonify({
        "comptes_acheteurs": comptes_acheteurs,
        "comptes_producteurs": comptes_producteurs,
        "visites": visites,
        "prix_deblocage": prix,
    })


# ---------------------------------------------------------------------------
# Produits
# ---------------------------------------------------------------------------

@app.post("/api/produits")
@require_role("producteur")
def creer_produit():
    data = request.get_json(force=True) or {}
    image1 = data.get("image1")  # image encodée en base64 envoyée par le site
    image2 = data.get("image2")
    if not image1 or not image2:
        return jsonify({"erreur": "Au moins 2 photos sont obligatoires pour publier un produit."}), 400
    if not data.get("nom") or not data.get("prix"):
        return jsonify({"erreur": "Nom et prix du produit requis."}), 400

    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO products (producteur_id, nom, prix, quantite, categorie, description, image1, image2)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (request.user["user_id"], data["nom"], data["prix"], data.get("quantite"),
         data.get("categorie"), data.get("description"), image1, image2),
    )
    produit_id = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return jsonify({"message": "Produit publié.", "id": produit_id}), 201


@app.get("/api/produits")
def lister_produits():
    categorie = request.args.get("categorie", "").strip()
    ville = request.args.get("ville", "").strip()
    recherche = request.args.get("q", "").strip()

    sql = """SELECT p.id, p.nom, p.prix, p.quantite, p.categorie, p.image1, p.image2,
                    u.nom AS producteur, u.id AS producteur_id, u.ville AS producteur_ville,
                    u.pays AS producteur_pays,
                    COALESCE((SELECT ROUND(AVG(a.note)::numeric, 1) FROM avis a WHERE a.producteur_id = u.id), u.note::numeric) AS producteur_note,
                    COALESCE((SELECT pc.prix FROM prix_categories pc WHERE pc.categorie = p.categorie),
                             (SELECT prix_deblocage FROM admin WHERE id = 1)) AS prix_deblocage_effectif
             FROM products p JOIN users u ON u.id = p.producteur_id
             WHERE u.statut = 'actif'"""
    params = []
    if categorie:
        sql += " AND p.categorie = %s"
        params.append(categorie)
    if ville:
        sql += " AND u.ville ILIKE %s"
        params.append(f"%{ville}%")
    if recherche:
        sql += " AND (p.nom ILIKE %s OR p.description ILIKE %s)"
        params.append(f"%{recherche}%")
        params.append(f"%{recherche}%")
    sql += " ORDER BY p.created_at DESC"

    conn = db.get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    # Le contact du producteur n'est jamais renvoyé ici : il faut passer par
    # /api/produits/<id>/debloquer puis /api/produits/<id>/contact.
    return jsonify([dict(r) for r in rows])


@app.get("/api/produits/mes")
@require_role("producteur")
def mes_produits():
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM products WHERE producteur_id = %s ORDER BY created_at DESC",
        (request.user["user_id"],),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.put("/api/produits/<int:produit_id>")
@require_role("producteur")
def modifier_produit(produit_id):
    conn = db.get_conn()
    produit = conn.execute("SELECT * FROM products WHERE id = %s", (produit_id,)).fetchone()
    if not produit:
        conn.close()
        return jsonify({"erreur": "Produit introuvable."}), 404
    if produit["producteur_id"] != request.user["user_id"]:
        conn.close()
        return jsonify({"erreur": "Vous ne pouvez modifier que vos propres produits."}), 403

    data = request.get_json(force=True) or {}
    nom = data.get("nom") or produit["nom"]
    prix = data.get("prix") or produit["prix"]
    quantite = data.get("quantite", produit["quantite"])
    categorie = data.get("categorie", produit["categorie"])
    description = data.get("description", produit["description"])
    image1 = data.get("image1") or produit["image1"]
    image2 = data.get("image2") or produit["image2"]

    conn.execute(
        """UPDATE products SET nom=%s, prix=%s, quantite=%s, categorie=%s, description=%s, image1=%s, image2=%s
           WHERE id = %s""",
        (nom, prix, quantite, categorie, description, image1, image2, produit_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "Produit mis à jour."})


@app.delete("/api/produits/<int:produit_id>")
@require_role("producteur")
def supprimer_produit(produit_id):
    conn = db.get_conn()
    produit = conn.execute("SELECT * FROM products WHERE id = %s", (produit_id,)).fetchone()
    if not produit:
        conn.close()
        return jsonify({"erreur": "Produit introuvable."}), 404
    if produit["producteur_id"] != request.user["user_id"]:
        conn.close()
        return jsonify({"erreur": "Vous ne pouvez supprimer que vos propres produits."}), 403

    conn.execute("DELETE FROM contact_unlocks WHERE product_id = %s", (produit_id,))
    conn.execute("DELETE FROM products WHERE id = %s", (produit_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Produit supprimé."})


# ---------------------------------------------------------------------------
# Déblocage de contact (paiement mobile money) — coeur du modèle économique
# ---------------------------------------------------------------------------

@app.post("/api/produits/<int:produit_id>/debloquer")
@require_role("acheteur")
def debloquer_contact(produit_id):
    data = request.get_json(force=True) or {}
    methode = data.get("methode")
    numero_paiement = data.get("numero_paiement")
    if methode not in METHODES_VALIDES:
        return jsonify({"erreur": "Moyen de paiement invalide (Wave, MTN Money, Orange Money, Moov Money)."}), 400
    if not numero_paiement:
        return jsonify({"erreur": "Numéro de paiement requis."}), 400

    conn = db.get_conn()
    produit = conn.execute("SELECT * FROM products WHERE id = %s", (produit_id,)).fetchone()
    if not produit:
        conn.close()
        return jsonify({"erreur": "Produit introuvable."}), 404

    prix = get_prix_pour_categorie(conn, produit["categorie"])
    reference = "AGL-" + uuid.uuid4().hex[:10].upper()
    conn.execute(
        """INSERT INTO contact_unlocks (acheteur_id, product_id, montant, methode, reference, statut)
           VALUES (%s,%s,%s,%s,%s, 'en_attente')""",
        (request.user["user_id"], produit_id, prix, methode, reference),
    )
    conn.commit()
    conn.close()

    # Ici, en production : on appelle l'API du fournisseur (Wave/MTN/Orange/Moov)
    # pour déclencher une demande de paiement ("push USSD") vers numero_paiement,
    # avec `reference` comme identifiant de transaction à réconcilier.
    return jsonify({
        "message": "Paiement initié. Confirmez sur votre téléphone pour débloquer le contact.",
        "reference": reference,
        "montant": prix,
    }), 202


@app.post("/api/produits/<int:produit_id>/debloquer-invite")
def debloquer_contact_invite(produit_id):
    """
    Permet à quelqu'un venant directement d'un réseau social (Facebook, TikTok...)
    de débloquer un contact sans créer de compte au préalable. Un compte
    acheteur léger est créé automatiquement en coulisses avec son nom et son
    téléphone, pour rester compatible avec le reste du système.
    """
    data = request.get_json(force=True) or {}
    nom = (data.get("nom") or "").strip()
    telephone = (data.get("telephone") or "").strip()
    methode = data.get("methode")
    numero_paiement = data.get("numero_paiement")

    if not nom or not telephone:
        return jsonify({"erreur": "Nom et téléphone requis."}), 400
    if methode not in METHODES_VALIDES:
        return jsonify({"erreur": "Moyen de paiement invalide (Wave, MTN Money, Orange Money, Moov Money)."}), 400
    if not numero_paiement:
        return jsonify({"erreur": "Numéro de paiement requis."}), 400

    conn = db.get_conn()
    produit = conn.execute("SELECT * FROM products WHERE id = %s", (produit_id,)).fetchone()
    if not produit:
        conn.close()
        return jsonify({"erreur": "Produit introuvable."}), 404

    identifiant_invite = "invite-" + uuid.uuid4().hex[:10]
    cur = conn.execute(
        """INSERT INTO users (role, nom, identifiant, telephone, password_hash)
           VALUES ('acheteur', %s, %s, %s, %s) RETURNING id""",
        (nom, identifiant_invite, telephone, generate_password_hash(uuid.uuid4().hex)),
    )
    user_id = cur.fetchone()["id"]

    prix = get_prix_pour_categorie(conn, produit["categorie"])
    reference = "AGL-" + uuid.uuid4().hex[:10].upper()
    conn.execute(
        """INSERT INTO contact_unlocks (acheteur_id, product_id, montant, methode, reference, statut)
           VALUES (%s,%s,%s,%s,%s, 'en_attente')""",
        (user_id, produit_id, prix, methode, reference),
    )
    conn.commit()
    conn.close()

    token = make_token({"user_id": user_id, "role": "acheteur", "nom": nom})
    return jsonify({
        "message": "Paiement initié. Confirmez sur votre téléphone pour débloquer le contact.",
        "reference": reference,
        "montant": prix,
        "token": token,
    }), 202


@app.post("/api/paiements/webhook")
def webhook_paiement():
    """
    Point d'entrée que Wave / MTN Money / Orange Money / Moov Money appellent
    pour confirmer (ou refuser) un paiement. La signature/authenticité de
    l'appel doit être vérifiée selon la documentation de chaque opérateur
    avant toute mise à jour en production.
    """
    data = request.get_json(force=True) or {}
    reference = data.get("reference")
    statut = data.get("statut")  # "paye" ou "echoue"
    if statut not in ("paye", "echoue"):
        return jsonify({"erreur": "Statut invalide."}), 400

    conn = db.get_conn()
    unlock = conn.execute("SELECT * FROM contact_unlocks WHERE reference = %s", (reference,)).fetchone()
    if not unlock:
        conn.close()
        return jsonify({"erreur": "Référence inconnue."}), 404

    conn.execute("UPDATE contact_unlocks SET statut = %s WHERE reference = %s", (statut, reference))
    conn.commit()
    conn.close()
    return jsonify({"message": "Statut mis à jour."})


@app.get("/api/produits/<int:produit_id>/contact")
@require_role("acheteur")
def voir_contact(produit_id):
    conn = db.get_conn()
    unlock = conn.execute(
        """SELECT * FROM contact_unlocks WHERE product_id = %s AND acheteur_id = %s AND statut = 'paye'
           ORDER BY created_at DESC LIMIT 1""",
        (produit_id, request.user["user_id"]),
    ).fetchone()
    if not unlock:
        conn.close()
        return jsonify({"erreur": "Contact non débloqué (paiement non confirmé)."}), 402

    paye_le = unlock["created_at"]
    if isinstance(paye_le, str):
        paye_le = datetime.datetime.strptime(paye_le, "%Y-%m-%d %H:%M:%S")
    if paye_le.tzinfo is None:
        paye_le = paye_le.replace(tzinfo=datetime.timezone.utc)
    expire_le = paye_le + datetime.timedelta(minutes=UNLOCK_DUREE_MINUTES)
    maintenant = datetime.datetime.now(datetime.timezone.utc)
    if maintenant > expire_le:
        conn.close()
        return jsonify({"erreur": "Le contact s'est reverrouillé après 5 minutes. Un nouveau paiement est nécessaire."}), 402

    producteur = conn.execute(
        "SELECT nom, telephone FROM users WHERE id = (SELECT producteur_id FROM products WHERE id = %s)",
        (produit_id,),
    ).fetchone()
    conn.close()
    secondes_restantes = int((expire_le - maintenant).total_seconds())
    return jsonify({"nom": producteur["nom"], "telephone": producteur["telephone"],
                     "secondes_restantes": secondes_restantes})


# ---------------------------------------------------------------------------
# Avis (notes et commentaires laissés par les acheteurs sur les producteurs)
# ---------------------------------------------------------------------------

@app.post("/api/producteurs/<int:producteur_id>/avis")
@require_role("acheteur")
def laisser_avis(producteur_id):
    data = request.get_json(force=True) or {}
    note = data.get("note")
    commentaire = (data.get("commentaire") or "").strip()
    if not isinstance(note, (int, float)) or note < 1 or note > 5:
        return jsonify({"erreur": "La note doit être comprise entre 1 et 5."}), 400

    conn = db.get_conn()
    # Il faut avoir déjà débloqué (payé) le contact de ce producteur pour pouvoir le noter.
    deja_client = conn.execute(
        """SELECT 1 FROM contact_unlocks cu JOIN products pr ON pr.id = cu.product_id
           WHERE cu.acheteur_id = %s AND pr.producteur_id = %s AND cu.statut = 'paye' LIMIT 1""",
        (request.user["user_id"], producteur_id),
    ).fetchone()
    if not deja_client:
        conn.close()
        return jsonify({"erreur": "Vous devez avoir débloqué le contact de ce producteur pour pouvoir le noter."}), 403

    conn.execute(
        """INSERT INTO avis (acheteur_id, producteur_id, note, commentaire) VALUES (%s,%s,%s,%s)
           ON CONFLICT(acheteur_id, producteur_id) DO UPDATE SET note=excluded.note, commentaire=excluded.commentaire""",
        (request.user["user_id"], producteur_id, int(note), commentaire),
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "Avis enregistré, merci !"})


@app.get("/api/producteurs/<int:producteur_id>/avis")
def voir_avis(producteur_id):
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT a.note, a.commentaire, a.created_at, u.nom AS acheteur
           FROM avis a JOIN users u ON u.id = a.acheteur_id
           WHERE a.producteur_id = %s ORDER BY a.created_at DESC""",
        (producteur_id,),
    ).fetchall()
    moyenne = conn.execute(
        "SELECT ROUND(AVG(note)::numeric,1) m, COUNT(*) n FROM avis WHERE producteur_id = %s", (producteur_id,)
    ).fetchone()
    conn.close()
    return jsonify({"avis": [dict(r) for r in rows], "moyenne": moyenne["m"], "nombre": moyenne["n"]})


# ---------------------------------------------------------------------------
# Mon profil (producteur ou acheteur connecté)
# ---------------------------------------------------------------------------

@app.get("/api/mon-profil")
@require_role("producteur", "acheteur")
def get_mon_profil():
    conn = db.get_conn()
    user = conn.execute("SELECT id, nom, identifiant, telephone, ville, culture, role FROM users WHERE id = %s",
                         (request.user["user_id"],)).fetchone()
    conn.close()
    if not user:
        return jsonify({"erreur": "Compte introuvable."}), 404
    return jsonify(dict(user))


@app.put("/api/mon-profil")
@require_role("producteur", "acheteur")
def maj_mon_profil():
    data = request.get_json(force=True) or {}
    nom = data.get("nom")
    telephone = data.get("telephone")
    ville = data.get("ville")
    culture = data.get("culture")
    nouveau_mdp = data.get("nouveau_mot_de_passe")

    if not nom:
        return jsonify({"erreur": "Le nom est obligatoire."}), 400
    if nouveau_mdp and len(nouveau_mdp) < 6:
        return jsonify({"erreur": "Le nouveau mot de passe doit contenir au moins 6 caractères."}), 400

    conn = db.get_conn()
    if nouveau_mdp:
        conn.execute(
            "UPDATE users SET nom=%s, telephone=%s, ville=%s, culture=%s, password_hash=%s WHERE id=%s",
            (nom, telephone, ville, culture, generate_password_hash(nouveau_mdp), request.user["user_id"]),
        )
    else:
        conn.execute(
            "UPDATE users SET nom=%s, telephone=%s, ville=%s, culture=%s WHERE id=%s",
            (nom, telephone, ville, culture, request.user["user_id"]),
        )
    conn.commit()
    conn.close()
    return jsonify({"message": "Profil mis à jour."})


# ---------------------------------------------------------------------------
# Administration
# ---------------------------------------------------------------------------

@app.post("/api/admin/login")
def admin_login():
    data = request.get_json(force=True) or {}
    conn = db.get_conn()
    admin = conn.execute("SELECT * FROM admin WHERE id = 1").fetchone()
    conn.close()
    if not admin or admin["identifiant"] != data.get("identifiant") \
            or not check_password_hash(admin["password_hash"], data.get("mot_de_passe") or ""):
        return jsonify({"erreur": "Identifiant ou mot de passe incorrect."}), 401
    token = make_token({"role": "admin"})
    return jsonify({"token": token})


@app.put("/api/admin/identifiants")
@require_role("admin")
def maj_identifiants_admin():
    data = request.get_json(force=True) or {}
    nouvel_id = data.get("nouvel_identifiant")
    nouveau_mdp = data.get("nouveau_mot_de_passe")
    if not nouvel_id or not nouveau_mdp or len(nouveau_mdp) < 8:
        return jsonify({"erreur": "Identifiant et mot de passe (8+ caractères) requis."}), 400
    conn = db.get_conn()
    conn.execute("UPDATE admin SET identifiant = %s, password_hash = %s WHERE id = 1",
                 (nouvel_id, generate_password_hash(nouveau_mdp)))
    conn.commit()
    conn.close()
    return jsonify({"message": "Identifiants administrateur mis à jour."})


@app.get("/api/admin/stats")
@require_role("admin")
def stats_admin():
    conn = db.get_conn()
    comptes_acheteurs = conn.execute("SELECT COUNT(*) c FROM users WHERE role='acheteur'").fetchone()["c"]
    comptes_producteurs = conn.execute("SELECT COUNT(*) c FROM users WHERE role='producteur'").fetchone()["c"]
    visites = conn.execute("SELECT COUNT(*) c FROM visits").fetchone()["c"]
    deblocages = conn.execute("SELECT COUNT(*) c FROM contact_unlocks WHERE statut='paye'").fetchone()["c"]
    revenus = conn.execute("SELECT COALESCE(SUM(montant),0) s FROM contact_unlocks WHERE statut='paye'").fetchone()["s"]
    prix = get_prix_deblocage(conn)
    conn.close()
    return jsonify({"comptes_acheteurs": comptes_acheteurs, "comptes_producteurs": comptes_producteurs,
                     "visites": visites, "deblocages_payes": deblocages, "revenus_fcfa": revenus,
                     "prix_deblocage": prix})


@app.get("/api/admin/prix-deblocage")
@require_role("admin")
def get_prix():
    conn = db.get_conn()
    prix = get_prix_deblocage(conn)
    conn.close()
    return jsonify({"prix_deblocage": prix})


@app.put("/api/admin/prix-deblocage")
@require_role("admin")
def set_prix():
    data = request.get_json(force=True) or {}
    prix = data.get("prix_deblocage")
    if not isinstance(prix, (int, float)) or prix <= 0:
        return jsonify({"erreur": "Montant invalide."}), 400
    conn = db.get_conn()
    conn.execute("UPDATE admin SET prix_deblocage = %s WHERE id = 1", (int(prix),))
    conn.commit()
    conn.close()
    return jsonify({"message": "Prix du déblocage mis à jour.", "prix_deblocage": int(prix)})


@app.get("/api/admin/prix-categories")
@require_role("admin")
def get_prix_categories():
    conn = db.get_conn()
    rows = conn.execute("SELECT categorie, prix FROM prix_categories ORDER BY categorie").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.put("/api/admin/prix-categories")
@require_role("admin")
def set_prix_categorie():
    data = request.get_json(force=True) or {}
    categorie = (data.get("categorie") or "").strip()
    prix = data.get("prix")
    if not categorie:
        return jsonify({"erreur": "Catégorie requise."}), 400
    if not isinstance(prix, (int, float)) or prix <= 0:
        return jsonify({"erreur": "Montant invalide."}), 400
    conn = db.get_conn()
    conn.execute(
        """INSERT INTO prix_categories (categorie, prix) VALUES (%s, %s)
           ON CONFLICT (categorie) DO UPDATE SET prix = excluded.prix""",
        (categorie, int(prix)),
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "Prix personnalisé enregistré.", "categorie": categorie, "prix": int(prix)})


@app.delete("/api/admin/prix-categories/<categorie>")
@require_role("admin")
def supprimer_prix_categorie(categorie):
    conn = db.get_conn()
    conn.execute("DELETE FROM prix_categories WHERE categorie = %s", (categorie,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Prix personnalisé supprimé, retour au prix standard."})


# ---------------------------------------------------------------------------
# Agents de terrain (recrutement de producteurs/acheteurs, commission 10%)
# ---------------------------------------------------------------------------

TAUX_COMMISSION_AGENT = 0.10


@app.post("/api/admin/agents")
@require_role("admin")
def creer_agent():
    data = request.get_json(force=True) or {}
    nom = (data.get("nom") or "").strip()
    telephone = (data.get("telephone") or "").strip()
    if not nom:
        return jsonify({"erreur": "Le nom de l'agent est requis."}), 400

    code = "AGT-" + uuid.uuid4().hex[:6].upper()
    conn = db.get_conn()
    cur = conn.execute(
        "INSERT INTO agents (nom, code, telephone) VALUES (%s,%s,%s) RETURNING id",
        (nom, code, telephone),
    )
    agent_id = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return jsonify({"message": "Agent créé.", "id": agent_id, "code": code}), 201


@app.get("/api/admin/agents")
@require_role("admin")
def liste_agents():
    conn = db.get_conn()
    agents = conn.execute("SELECT id, nom, code, telephone, total_paye FROM agents ORDER BY nom").fetchall()

    resultat = []
    for a in agents:
        # Revenu généré uniquement par les ventes des producteurs que cet agent a recrutés
        # (l'acheteur, lui, n'a plus besoin de compte : la commission ne dépend que du producteur).
        row = conn.execute(
            """SELECT COALESCE(SUM(cu.montant), 0) AS total
               FROM contact_unlocks cu
               JOIN products p ON p.id = cu.product_id
               JOIN users prod ON prod.id = p.producteur_id
               WHERE cu.statut = 'paye' AND prod.agent_id = %s""",
            (a["id"],),
        ).fetchone()
        revenu_genere = row["total"] or 0
        nb_producteurs = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE agent_id = %s AND role = 'producteur'", (a["id"],)
        ).fetchone()["c"]
        nb_acheteurs = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE agent_id = %s AND role = 'acheteur'", (a["id"],)
        ).fetchone()["c"]

        commission_totale = round(revenu_genere * TAUX_COMMISSION_AGENT)
        reste_a_payer = commission_totale - a["total_paye"]

        resultat.append({
            "id": a["id"], "nom": a["nom"], "code": a["code"], "telephone": a["telephone"],
            "nb_producteurs": nb_producteurs, "nb_acheteurs": nb_acheteurs,
            "revenu_genere": revenu_genere, "commission_totale": commission_totale,
            "total_paye": a["total_paye"], "reste_a_payer": max(0, reste_a_payer),
        })

    conn.close()
    return jsonify(resultat)


@app.post("/api/admin/agents/<int:agent_id>/payer")
@require_role("admin")
def payer_agent(agent_id):
    conn = db.get_conn()
    agent = conn.execute("SELECT * FROM agents WHERE id = %s", (agent_id,)).fetchone()
    if not agent:
        conn.close()
        return jsonify({"erreur": "Agent introuvable."}), 404

    row = conn.execute(
        """SELECT COALESCE(SUM(cu.montant), 0) AS total
           FROM contact_unlocks cu
           JOIN products p ON p.id = cu.product_id
           JOIN users prod ON prod.id = p.producteur_id
           WHERE cu.statut = 'paye' AND prod.agent_id = %s""",
        (agent_id,),
    ).fetchone()
    revenu_genere = row["total"] or 0
    commission_totale = round(revenu_genere * TAUX_COMMISSION_AGENT)
    reste_a_payer = max(0, commission_totale - agent["total_paye"])

    if reste_a_payer <= 0:
        conn.close()
        return jsonify({"erreur": "Rien à payer pour cet agent pour le moment."}), 400

    nouveau_total_paye = agent["total_paye"] + reste_a_payer
    conn.execute("UPDATE agents SET total_paye = %s WHERE id = %s", (nouveau_total_paye, agent_id))
    conn.commit()
    conn.close()
    return jsonify({"message": "Paiement enregistré.", "montant_paye": reste_a_payer, "total_paye": nouveau_total_paye})


# ---------------------------------------------------------------------------
# Statistiques de revenus par pays
# ---------------------------------------------------------------------------

@app.get("/api/admin/revenus-par-pays")
@require_role("admin")
def revenus_par_pays():
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT COALESCE(prod.pays, 'Non renseigné') AS pays, COALESCE(SUM(cu.montant), 0) AS revenu,
                  COUNT(*) AS nb_deblocages
           FROM contact_unlocks cu
           JOIN products p ON p.id = cu.product_id
           JOIN users prod ON prod.id = p.producteur_id
           WHERE cu.statut = 'paye'
           GROUP BY prod.pays
           ORDER BY revenu DESC"""
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Cartographie des lieux d'enregistrement (producteurs et/ou acheteurs)
# ---------------------------------------------------------------------------

@app.get("/api/admin/carte-comptes")
@require_role("admin")
def carte_comptes():
    role = request.args.get("role", "").strip()
    categories_param = request.args.get("categories", "").strip()
    sql = """SELECT nom, role, ville, pays, latitude, longitude
             FROM users WHERE latitude IS NOT NULL AND longitude IS NOT NULL"""
    params = []
    if role in ("producteur", "acheteur"):
        sql += " AND role = %s"
        params.append(role)
    if categories_param:
        cats = [c.strip() for c in categories_param.split(",") if c.strip()]
        if cats:
            placeholders = ",".join(["%s"] * len(cats))
            sql += f""" AND role = 'producteur' AND EXISTS (
                SELECT 1 FROM products pr WHERE pr.producteur_id = users.id AND pr.categorie IN ({placeholders})
            )"""
            params.extend(cats)
    conn = db.get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/admin/geocoder-existants")
@require_role("admin")
def geocoder_existants():
    """Géocode (gratuitement, via OpenStreetMap) les comptes déjà inscrits
    avant l'ajout de la carte, qui n'ont donc pas encore de coordonnées.
    Traite un petit lot à la fois pour respecter la limite du service gratuit."""
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT id, ville, pays FROM users
           WHERE ville IS NOT NULL AND ville != '' AND latitude IS NULL
           LIMIT 15"""
    ).fetchall()

    traites = 0
    for r in rows:
        lat, lon = geocoder_ville(r["ville"], r["pays"])
        if lat is not None:
            conn.execute("UPDATE users SET latitude = %s, longitude = %s WHERE id = %s", (lat, lon, r["id"]))
            traites += 1
        time.sleep(1)  # limite du service gratuit Nominatim : 1 requête par seconde maximum
    conn.commit()
    conn.close()
    return jsonify({"message": f"{traites} compte(s) géocodé(s).", "traites": traites, "restants_a_verifier": len(rows) - traites})


@app.get("/api/admin/comptes")
@require_role("admin")
def liste_comptes():
    # Par sécurité, les mots de passe sont hachés (chiffrés à sens unique) et
    # ne peuvent jamais être ré-affichés, même par l'admin — seul l'identifiant
    # (le nom de connexion) est visible ici.
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT nom, role, identifiant, telephone, pays, ville, statut FROM users ORDER BY role, nom"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/api/admin/producteurs")
@require_role("admin")
def liste_producteurs():
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT u.id, u.nom, u.ville, u.statut,
                  COALESCE((SELECT ROUND(AVG(a.note)::numeric,1) FROM avis a WHERE a.producteur_id = u.id), u.note::numeric) AS note
           FROM users u WHERE u.role='producteur' ORDER BY u.nom"""
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.patch("/api/admin/producteurs/<int:user_id>/statut")
@require_role("admin")
def changer_statut_producteur(user_id):
    data = request.get_json(force=True) or {}
    statut = data.get("statut")
    if statut not in ("actif", "suspendu"):
        return jsonify({"erreur": "Statut invalide ('actif' ou 'suspendu')."}), 400
    conn = db.get_conn()
    conn.execute("UPDATE users SET statut = %s WHERE id = %s AND role = 'producteur'", (statut, user_id))
    conn.commit()
    conn.close()
    return jsonify({"message": "Statut du producteur mis à jour."})


@app.get("/api/admin/paiement-numeros")
@require_role("admin")
def get_numeros_paiement():
    conn = db.get_conn()
    admin = conn.execute("SELECT wave_numero, mtn_numero, orange_numero, moov_numero FROM admin WHERE id=1").fetchone()
    conn.close()
    return jsonify(dict(admin))


@app.put("/api/admin/paiement-numeros")
@require_role("admin")
def set_numeros_paiement():
    data = request.get_json(force=True) or {}
    conn = db.get_conn()
    conn.execute(
        "UPDATE admin SET wave_numero=%s, mtn_numero=%s, orange_numero=%s, moov_numero=%s WHERE id=1",
        (data.get("wave", ""), data.get("mtn", ""), data.get("orange", ""), data.get("moov", "")),
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "Numéros de réception mis à jour."})


def seed_demo_data():
    conn = db.get_conn()
    if conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0:
        conn.execute(
            """INSERT INTO users (role, nom, identifiant, telephone, ville, culture, password_hash, note)
               VALUES ('producteur','Vendeur Test','vendeur.test','0700000001','Abidjan','Légumes',%s,5.0)""",
            (generate_password_hash("Vendeur123"),),
        )
        conn.execute(
            """INSERT INTO users (role, nom, identifiant, telephone, ville, culture, password_hash, note)
               VALUES ('producteur','Koffi Aya','koffi.aya','0701020304','Yamoussoukro','Tomates',%s,4.6)""",
            (generate_password_hash("koffi2024"),),
        )
        conn.execute(
            """INSERT INTO users (role, nom, identifiant, telephone, ville, culture, password_hash, note)
               VALUES ('acheteur','Acheteur Test','acheteur.test','0700000002','Abidjan',NULL,%s,5.0)""",
            (generate_password_hash("Acheteur123"),),
        )
        conn.commit()
    conn.close()


def csv_response(nom_fichier, entetes, lignes):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(entetes)
    writer.writerows(lignes)
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={nom_fichier}"},
    )


@app.get("/api/admin/export/utilisateurs")
@require_role("admin")
def export_utilisateurs():
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT nom, role, identifiant, telephone, pays, ville, culture, note, statut, created_at
           FROM users ORDER BY role, nom"""
    ).fetchall()
    conn.close()
    lignes = [[r["nom"], r["role"], r["identifiant"], r["telephone"], r["pays"], r["ville"],
               r["culture"], r["note"], r["statut"], r["created_at"]] for r in rows]
    return csv_response("utilisateurs.csv",
                         ["Nom", "Rôle", "Identifiant", "Téléphone", "Pays", "Ville", "Culture", "Note", "Statut", "Créé le"],
                         lignes)


@app.get("/api/admin/export/produits")
@require_role("admin")
def export_produits():
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT p.nom, p.prix, p.quantite, p.categorie, u.nom AS producteur, u.ville, u.pays, p.created_at
           FROM products p JOIN users u ON u.id = p.producteur_id ORDER BY p.created_at DESC"""
    ).fetchall()
    conn.close()
    lignes = [[r["nom"], r["prix"], r["quantite"], r["categorie"], r["producteur"], r["ville"], r["pays"], r["created_at"]]
              for r in rows]
    return csv_response("produits.csv",
                         ["Produit", "Prix", "Quantité", "Catégorie", "Producteur", "Ville", "Pays", "Publié le"],
                         lignes)


@app.get("/api/admin/export/deblocages")
@require_role("admin")
def export_deblocages():
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT cu.reference, cu.montant, cu.methode, cu.statut, cu.created_at,
                  p.nom AS produit, prod.nom AS producteur, ach.nom AS acheteur
           FROM contact_unlocks cu
           JOIN products p ON p.id = cu.product_id
           JOIN users prod ON prod.id = p.producteur_id
           JOIN users ach ON ach.id = cu.acheteur_id
           ORDER BY cu.created_at DESC"""
    ).fetchall()
    conn.close()
    lignes = [[r["reference"], r["montant"], r["methode"], r["statut"], r["created_at"],
               r["produit"], r["producteur"], r["acheteur"]] for r in rows]
    return csv_response("deblocages.csv",
                         ["Référence", "Montant", "Moyen de paiement", "Statut", "Date", "Produit", "Producteur", "Acheteur"],
                         lignes)


@app.get("/api/admin/export/agents")
@require_role("admin")
def export_agents():
    conn = db.get_conn()
    rows = conn.execute("SELECT nom, code, telephone, total_paye, created_at FROM agents ORDER BY nom").fetchall()
    conn.close()
    lignes = [[r["nom"], r["code"], r["telephone"], r["total_paye"], r["created_at"]] for r in rows]
    return csv_response("agents.csv", ["Nom", "Code", "Téléphone", "Total payé", "Créé le"], lignes)


# ---------------------------------------------------------------------------
# Statistiques pour le tableau de bord graphique
# ---------------------------------------------------------------------------

@app.get("/api/admin/stats-quotidiennes")
@require_role("admin")
def stats_quotidiennes():
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT DATE(created_at) AS jour, COALESCE(SUM(montant), 0) AS revenu, COUNT(*) AS nb
           FROM contact_unlocks WHERE statut = 'paye' AND created_at > NOW() - INTERVAL '30 days'
           GROUP BY DATE(created_at) ORDER BY jour"""
    ).fetchall()
    conn.close()
    return jsonify([{"jour": str(r["jour"]), "revenu": r["revenu"], "nb": r["nb"]} for r in rows])


@app.get("/api/admin/revenus-par-categorie")
@require_role("admin")
def revenus_par_categorie():
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT COALESCE(p.categorie, 'Non renseigné') AS categorie,
                  COALESCE(SUM(cu.montant), 0) AS revenu, COUNT(*) AS nb
           FROM contact_unlocks cu JOIN products p ON p.id = cu.product_id
           WHERE cu.statut = 'paye'
           GROUP BY p.categorie ORDER BY revenu DESC"""
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/api/admin/comptes-par-pays")
@require_role("admin")
def comptes_par_pays():
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT COALESCE(pays, 'Non renseigné') AS pays,
                  COUNT(*) FILTER (WHERE role = 'producteur') AS producteurs,
                  COUNT(*) FILTER (WHERE role = 'acheteur') AS acheteurs
           FROM users GROUP BY pays ORDER BY (COUNT(*)) DESC"""
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    db.init_db(generate_password_hash("AgriLine@2026"))
    seed_demo_data()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
