"""Resend API ile e-posta gonderimi (esik bildirimleri icin).

Gerekli ortam degiskeni: RESEND_API_KEY

Not: Resend'de ozel bir domain dogrulanmadiysa, varsayilan gonderen adresi
olan onboarding@resend.dev yalnizca Resend hesabinin kendi (kayit) e-posta
adresine teslimat yapar. Baska adreslere gondermek icin Resend panelinden
bir domain dogrulaman gerekir.
"""

from __future__ import annotations

import os

import requests

RESEND_ENDPOINT = "https://api.resend.com/emails"
VARSAYILAN_GONDEREN = "Haber Takip Platformu <onboarding@resend.dev>"


def yapilandirilmis_mi() -> bool:
    return bool(os.environ.get("RESEND_API_KEY"))


def eposta_gonder(aliciler: list[str], konu: str, html_govde: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY tanımlı değil.")
    if not aliciler:
        return

    resp = requests.post(
        RESEND_ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "from": os.environ.get("RESEND_FROM", VARSAYILAN_GONDEREN),
            "to": aliciler,
            "subject": konu,
            "html": html_govde,
        },
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(f"Resend hatası ({resp.status_code}): {resp.text[:300]}")
