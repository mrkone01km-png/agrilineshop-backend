"""
Utilitaires d'authentification : émission et vérification de jetons JWT,
pour les comptes utilisateurs (producteur/acheteur) et pour l'admin.
"""
import jwt
import datetime
from functools import wraps
from flask import request, jsonify

SECRET_KEY = "change-moi-en-production-avec-une-vraie-cle-secrete"


def make_token(payload, hours=12):
    data = payload.copy()
    data["exp"] = datetime.datetime.utcnow() + datetime.timedelta(hours=hours)
    return jwt.encode(data, SECRET_KEY, algorithm="HS256")


def decode_token(token):
    return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])


def _get_token_from_header():
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header.split(" ", 1)[1]
    return None


def require_role(*roles):
    """Décorateur : protège une route et injecte request.user avec le payload du jeton."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            token = _get_token_from_header()
            if not token:
                return jsonify({"erreur": "Authentification requise."}), 401
            try:
                payload = decode_token(token)
            except jwt.ExpiredSignatureError:
                return jsonify({"erreur": "Session expirée, reconnectez-vous."}), 401
            except jwt.InvalidTokenError:
                return jsonify({"erreur": "Jeton invalide."}), 401
            if roles and payload.get("role") not in roles:
                return jsonify({"erreur": "Accès refusé pour ce rôle."}), 403
            request.user = payload
            return fn(*args, **kwargs)

        return wrapper

    return decorator
