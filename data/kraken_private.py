"""Client Kraken Spot privé, strictement en lecture seule.

Démonstration : python -m data.kraken_private [--limit 10]
Aucun appel réseau ou chargement de credentials à l'import.
"""
import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
from threading import Lock
import time
from urllib.parse import quote, quote_plus, urlencode

import requests

_ORIGIN = "https://api.kraken.com"
_READ_ENDPOINTS = frozenset({"Balance", "OpenOrders", "ClosedOrders"})
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_NONCE_LOCK = Lock()
_REQUEST_LOCK = Lock()
_last_nonce = 0


class KrakenPrivateError(Exception):
    """Erreur contrôlée : ne contient ni credentials ni réponse HTTP brute."""


class KrakenCredentialsError(KrakenPrivateError):
    pass


class KrakenAuthenticationError(KrakenPrivateError):
    pass


class KrakenNetworkError(KrakenPrivateError):
    pass


class KrakenHTTPError(KrakenPrivateError):
    pass


class KrakenRateLimitError(KrakenPrivateError):
    def __init__(self, retry_after=None):
        self.retry_after = retry_after
        message = "Limite d'appels Kraken atteinte ; réessayer plus tard."
        if retry_after is not None:
            message += f" Attendre au moins {retry_after} seconde(s)."
        super().__init__(message)


def next_nonce():
    """Strictement croissant entre threads / clients de ce processus, en millisecondes."""
    global _last_nonce
    with _NONCE_LOCK:
        _last_nonce = max(time.time_ns() // 1_000_000, _last_nonce + 1)
        return str(_last_nonce)


def _decoded_secret(secret):
    try:
        decoded = base64.b64decode(secret, validate=True)
    except (ValueError, binascii.Error, TypeError):
        raise KrakenCredentialsError("KRAKEN_API_SECRET doit être un secret Kraken encodé en base64.") from None
    if not decoded:
        raise KrakenCredentialsError("KRAKEN_API_SECRET est vide.")
    return decoded


def sign_request(path, data, secret):
    """Fonction cryptographique pure ; ne peut envoyer aucune requête."""
    encoded = urlencode(data)
    digest = hashlib.sha256((str(data["nonce"]) + encoded).encode()).digest()
    signature = hmac.new(_decoded_secret(secret), path.encode() + digest, hashlib.sha512).digest()
    return base64.b64encode(signature).decode("ascii")


def _credentials_from_env(env_path):
    names = ("KRAKEN_API_KEY", "KRAKEN_API_SECRET")
    values = {}
    if env_path.is_file():
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            raise KrakenCredentialsError("Impossible de lire le fichier .env local.") from None
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, separator, value = line.partition("=")
            if separator and key.strip() in names:
                value = value.strip()
                if value.startswith(('"', "'")):
                    if len(value) < 2 or value[-1] != value[0]:
                        raise KrakenCredentialsError("Valeur entre guillemets mal fermée dans .env.")
                    value = value[1:-1]
                else:
                    value = value.split(" #", 1)[0].rstrip()
                values[key.strip()] = value
    # Les variables exportées priment, même si leur valeur est vide.
    return tuple(os.environ.get(name, values.get(name, "")) for name in names)


def _api_error(errors):
    # Ne jamais recopier une erreur distante : elle peut contenir des informations sensibles.
    if any(e.startswith(("EAPI:Rate limit", "EService:Throttled")) for e in errors):
        return KrakenRateLimitError()
    if any(e.startswith("EAPI:Invalid nonce") for e in errors):
        return KrakenAuthenticationError("Nonce refusé : vérifier l'horloge et utiliser une clé dédiée à ce processus.")
    if any(e.startswith(("EAPI:Invalid key", "EAPI:Invalid signature", "EAPI:Invalid session", "EAPI:Bad OTP")) for e in errors):
        return KrakenAuthenticationError("Authentification refusée : vérifier la clé, le secret, les restrictions IP et la 2FA API.")
    if any(e.startswith(("EGeneral:Permission denied", "EAPI:Permission denied")) for e in errors):
        return KrakenAuthenticationError("Permission refusée : vérifier les droits de consultation des fonds et des ordres.")
    if any(e.startswith("EGeneral:Temporary lockout") for e in errors):
        return KrakenAuthenticationError("Accès temporairement bloqué par Kraken ; attendre avant de réessayer.")
    return KrakenPrivateError("Kraken a refusé la requête de lecture ; aucune réponse brute n'est affichée.")


class KrakenPrivateClient:
    def __init__(self, api_key, api_secret):
        if not isinstance(api_key, str) or not isinstance(api_secret, str) or not api_key.strip() or not api_secret.strip():
            raise KrakenCredentialsError("Renseigner KRAKEN_API_KEY et KRAKEN_API_SECRET dans .env ou l'environnement.")
        api_key, api_secret = api_key.strip(), api_secret.strip()
        if not api_key.isascii() or not all(c.isalnum() or c in "+/=_-" for c in api_key):
            raise KrakenCredentialsError("Format de KRAKEN_API_KEY invalide.")
        _decoded_secret(api_secret)
        self._api_key = api_key
        self._api_secret = api_secret
        self._session = requests.Session()

    @classmethod
    def from_env(cls, env_path=None):
        return cls(*_credentials_from_env(Path(env_path) if env_path is not None else _ENV_PATH))

    def close(self):
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _redact(self, value, signature=""):
        tokens = {self._api_key, self._api_secret}
        tokens.update(quote(token, safe="") for token in tuple(tokens))
        tokens.update(quote_plus(token) for token in tuple(tokens))
        if signature:
            tokens.add(signature)
        def clean(item):
            if isinstance(item, str):
                for token in sorted(tokens, key=len, reverse=True):
                    item = item.replace(token, "[REDACTED]")
                return item
            if isinstance(item, dict):
                return {clean(k): clean(v) for k, v in item.items()}
            if isinstance(item, list):
                return [clean(v) for v in item]
            return item
        return clean(value)

    def _request(self, endpoint, params=None):
        if endpoint not in _READ_ENDPOINTS:
            raise KrakenPrivateError("Endpoint interdit : ce client est exclusivement en lecture seule.")
        # Sérialise signature + envoi pour ne pas inverser les nonces entre threads.
        with _REQUEST_LOCK:
            data = dict(params or {})
            data["nonce"] = next_nonce()
            path = f"/0/private/{endpoint}"
            body = urlencode(data)
            signature = sign_request(path, data, self._api_secret)
            headers = {"API-Key": self._api_key, "API-Sign": signature,
                       "Content-Type": "application/x-www-form-urlencoded"}
            try:
                response = self._session.post(_ORIGIN + path, data=body, headers=headers,
                                              timeout=15, allow_redirects=False)
            except requests.Timeout:
                raise KrakenNetworkError("Délai de connexion Kraken dépassé ; réessayer ultérieurement.") from None
            except requests.RequestException:
                raise KrakenNetworkError("Connexion à Kraken impossible ; vérifier le réseau.") from None
            if response.status_code == 429:
                value = response.headers.get("Retry-After", "")
                retry_after = int(value) if value.isascii() and value.isdigit() and len(value) <= 8 else None
                raise KrakenRateLimitError(retry_after)
            if response.status_code in (401, 403):
                raise KrakenAuthenticationError("Accès HTTP refusé par Kraken ; vérifier les accès et restrictions de la clé.")
            if not 200 <= response.status_code < 300:
                raise KrakenHTTPError(f"Erreur HTTP Kraken {response.status_code} ; aucune réponse brute n'est affichée.")
            try:
                payload = response.json()
            except ValueError:
                raise KrakenPrivateError("Réponse Kraken invalide : JSON attendu.") from None
            if (not isinstance(payload, dict) or not isinstance(payload.get("error"), list)
                    or any(not isinstance(e, str) for e in payload["error"])):
                raise KrakenPrivateError("Réponse Kraken invalide : structure inattendue.")
            if payload["error"]:
                raise _api_error(payload["error"])
            if not isinstance(payload.get("result"), dict):
                raise KrakenPrivateError("Réponse Kraken invalide : résultat manquant.")
            return self._redact(payload["result"], signature)

    def balance(self):
        """Soldes du portefeuille par défaut, pas nécessairement libres de tout engagement."""
        return self._request("Balance")

    def open_orders(self):
        result = self._request("OpenOrders", {"trades": "false"})
        if not isinstance(result.get("open"), dict):
            raise KrakenPrivateError("Réponse Kraken invalide : ordres ouverts manquants.")
        return result["open"]

    def closed_orders(self, limit=10):
        """Derniers ordres de la première page Kraken (50 maximum), remplis ou annulés."""
        if type(limit) is not int or not 1 <= limit <= 50:
            raise KrakenPrivateError("La limite d'ordres clôturés doit être un entier entre 1 et 50.")
        result = self._request("ClosedOrders", {"trades": "false", "closetime": "close"})
        orders = result.get("closed")
        if not isinstance(orders, dict):
            raise KrakenPrivateError("Réponse Kraken invalide : ordres clôturés manquants.")
        try:
            recent = sorted(orders.items(), key=lambda item: float(item[1]["closetm"]), reverse=True)
        except (ValueError, KeyError, TypeError):
            raise KrakenPrivateError("Réponse Kraken invalide : dates de clôture manquantes ou invalides.") from None
        return dict(recent[:limit])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, choices=range(1, 51), default=10, metavar="1..50")
    args = parser.parse_args(argv)
    try:
        with KrakenPrivateClient.from_env() as client:
            balances = client.balance()
            print("Connexion authentifiée : OK (Balance). Mode lecture seule.")
            print("Soldes du compte (Balance ; montants libres après réservations non calculés) :")
            print(json.dumps(balances, ensure_ascii=False, indent=2))
            orders = client.open_orders()
            print(f"Ordres ouverts ({len(orders)}) :")
            print(json.dumps(orders, ensure_ascii=False, indent=2))
            closed = client.closed_orders(args.limit)
            print(f"Derniers ordres clôturés ({len(closed)}, première page de 50 maximum) :")
            print(json.dumps(closed, ensure_ascii=False, indent=2))
            print("Vérification des trois accès de lecture : OK.")
        return 0
    except KrakenPrivateError as error:
        print(f"Vérification incomplète / connexion impossible : {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Vérification interrompue.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
