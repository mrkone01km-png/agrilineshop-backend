# AgriLineShop — Backend (prototype testé)

API pour la mise en relation producteurs agricoles / acheteurs, avec
commission de **300 FCFA par déblocage de contact**, payable via Wave, MTN
Money, Orange Money ou Moov Money.

Ce backend a été **exécuté et testé de bout en bout** (inscription,
publication de produit, refus si moins de 2 photos, déblocage de contact,
confirmation de paiement, statistiques admin, suspension d'un producteur).

## Installation

```bash
pip install -r requirements.txt
python3 app.py
```

Le serveur démarre sur `http://127.0.0.1:5000`. Au premier lancement, un
compte admin est créé automatiquement :

- Identifiant : `admin`
- Mot de passe : `AgriLine@2026`

À changer via `PUT /api/admin/identifiants` une fois connecté (comme sur le
prototype web que vous avez déjà vu).

## Ce que fait déjà ce backend

- **Comptes séparés** producteur / acheteur (`/api/auth/register`, `/api/auth/login`)
- **Publication de produit** refusée s'il manque une des 2 photos minimum
- **Marché public** (`GET /api/produits`) qui ne montre jamais le contact du
  producteur — le contact n'est révélé qu'après paiement confirmé
- **Déblocage de contact à 300 FCFA** : crée une transaction "en attente",
  puis un webhook de paiement la passe à "payée" avant que le contact soit
  visible
- **Tableau de bord admin** : comptes créés, visites, déblocages payés,
  revenus (= déblocages × 300 FCFA)
- **Suspension/réactivation** d'un producteur (ses produits disparaissent
  aussitôt du marché)
- **Numéros de réception** Wave/MTN/Orange/Moov, modifiables par l'admin

## Endpoints principaux

| Méthode | Route | Rôle | Description |
|---|---|---|---|
| POST | `/api/auth/register` | public | Créer un compte producteur ou acheteur |
| POST | `/api/auth/login` | public | Connexion, retourne un jeton |
| POST | `/api/produits` | producteur | Publier un produit (2 photos obligatoires) |
| GET | `/api/produits` | public | Liste du marché (contact masqué) |
| POST | `/api/produits/<id>/debloquer` | acheteur | Initier le paiement de 300 FCFA |
| POST | `/api/paiements/webhook` | opérateur mobile money | Confirme le paiement |
| GET | `/api/produits/<id>/contact` | acheteur | Contact (si payé) |
| POST | `/api/admin/login` | admin | Connexion admin |
| PUT | `/api/admin/identifiants` | admin | Changer identifiant/mot de passe |
| GET | `/api/admin/stats` | admin | Comptes, visites, déblocages, revenus |
| GET | `/api/admin/producteurs` | admin | Liste avec note et statut |
| PATCH | `/api/admin/producteurs/<id>/statut` | admin | Suspendre / réactiver |
| GET/PUT | `/api/admin/paiement-numeros` | admin | Numéros Wave/MTN/Orange/Moov |

## Déployer sur Render (gratuit, avec juste un email)

1. Mets ces fichiers (`app.py`, `db.py`, `auth_utils.py`, `requirements.txt`) dans un dépôt GitHub (via "Upload files" sur github.com, sans ligne de commande).
2. Crée un compte gratuit sur render.com.
3. "New +" → "Web Service" → connecte ton dépôt GitHub.
4. Renseigne :
   - Runtime : **Python 3**
   - Build Command : `pip install -r requirements.txt`
   - Start Command : `python app.py`
5. Valide. Render te donne une adresse du type `https://agrilineshop-api.onrender.com`.

Cette adresse est celle que le site (sur Netlify) devra appeler pour que les
comptes et produits soient vraiment sauvegardés.

**À savoir sur le plan gratuit de Render** : le service peut se mettre en
veille après quelques minutes sans visite (la première requête après une
veille prend alors quelques secondes de plus), et le stockage des fichiers
n'est pas garanti permanent sur la durée. Pour une base de données qui dure
vraiment dans le temps, l'étape suivante sera de passer sur une vraie base
PostgreSQL (Render en propose une gratuite également).

## Ce qu'il reste à faire pour une mise en production réelle

Ce backend pose une base solide et testée, mais trois chantiers restent
volontairement **hors de portée d'un prototype construit dans une
conversation** :

1. **Vrais paiements mobile money** — Le endpoint `/api/paiements/webhook`
   simule la confirmation. En réalité, il faut : un contrat marchand avec
   Wave, MTN, Orange et Moov ; leurs clés API ; l'appel réel de "push USSD"
   au moment du déblocage ; et la vérification de signature de leur webhook.
   Chaque opérateur a sa propre documentation et ses propres délais
   d'intégration.
2. **Stockage des images** — Actuellement les images sont acceptées telles
   quelles (URL ou base64). En production, il faut un service de stockage
   (S3, Cloudinary, ou un bucket équivalent) pour héberger les photos et
   servir des URLs légères.
3. **Base de données de production** — SQLite convient pour ce prototype ;
   pour la mise en ligne, on migre vers PostgreSQL avec sauvegardes
   automatiques.

Je peux avancer sur n'importe lequel de ces trois chantiers ensuite —
par exemple préparer la structure de code pour un des opérateurs de
mobile money, ou passer la base sur PostgreSQL.
