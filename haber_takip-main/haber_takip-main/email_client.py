"""Gmail SMTP ile e-posta gonderimi (esik bildirimleri ve deneme e-postasi icin).

Ucuncu parti bir servise (Resend, SendGrid vb.) kayit olmaya ya da domain
dogrulamaya gerek kalmadan, kullanicinin kendi Gmail hesabi uzerinden
gonderir.

Gerekli ortam degiskenleri:
  GMAIL_ADDRESS        Gonderen Gmail adresi (ornek: adiniz@gmail.com)
  GMAIL_APP_PASSWORD   Google hesabinda 2 adimli dogrulama acildiktan sonra
                        "Uygulama Sifreleri" bolumunden olusturulan 16 haneli
                        sifre. Normal Gmail sifreniz DEGILDIR.
"""

from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_SUNUCU = "smtp.gmail.com"
SMTP_PORT = 587


def yapilandirilmis_mi() -> bool:
    return bool(os.environ.get("GMAIL_ADDRESS") and os.environ.get("GMAIL_APP_PASSWORD"))


def eposta_gonder(aliciler: list[str], konu: str, html_govde: str) -> None:
    """Her aliciya AYRI AYRI gonderir: gecersiz/reddedilen tek bir adres
    (ornegin SMTP'nin kabul etmedigi bir karakter iceren bir e-posta),
    digerlerinin de gonderilmesini engellemesin diye. En az bir alicaya
    basariyla ulasilirsa hata firlatilmaz; hicbirine ulasilamazsa hepsinin
    hatasini birlestiren bir RuntimeError firlatilir."""
    adres = os.environ.get("GMAIL_ADDRESS", "").strip()
    sifre = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not adres or not sifre:
        raise RuntimeError("GMAIL_ADDRESS / GMAIL_APP_PASSWORD tanımlı değil.")
    if not aliciler:
        return

    basarili: list[str] = []
    hatalar: list[str] = []

    try:
        with smtplib.SMTP(SMTP_SUNUCU, SMTP_PORT, timeout=20) as sunucu:
            sunucu.starttls()
            sunucu.login(adres, sifre)
            for alici in aliciler:
                mesaj = MIMEMultipart("alternative")
                mesaj["Subject"] = konu
                mesaj["From"] = f"Haber Takip Platformu <{adres}>"
                mesaj["To"] = alici
                mesaj.attach(MIMEText(html_govde, "html", "utf-8"))
                try:
                    sunucu.sendmail(adres, [alici], mesaj.as_string())
                    basarili.append(alici)
                except Exception as e:  # noqa: BLE001
                    hatalar.append(f"{alici}: {e}")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Gmail SMTP hatası: {e}") from e

    if not basarili:
        raise RuntimeError("Hiçbir adrese gönderilemedi: " + "; ".join(hatalar))
