"""Gemeinsame Hilfen fuer die Zugriffs-Tests (Passwort + Basic-Auth-Kopf)."""
import base64

PW = "geheim-12345"


def auth(pw):
    return {"Authorization": "Basic " + base64.b64encode(f"user:{pw}".encode()).decode()}
