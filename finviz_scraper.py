"""Finviz haber sayfasini ceker ve gunlere ayirir. Sunucu tarafinda calisir."""

from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

FINVIZ_URL = "https://finviz.com/news.ashx"
TIME_RE = re.compile(r"^\d{1,2}:\d{2}\s*(AM|PM)$", re.IGNORECASE)


def _satir_tarihini_belirle(saat_metni: str, bugun: date) -> date:
    if TIME_RE.match(saat_metni):
        return bugun
    try:
        d = datetime.strptime(f"{saat_metni}-{bugun.year}", "%b-%d-%Y").date()
        if d > bugun:
            d = d.replace(year=d.year - 1)
        return d
    except ValueError:
        return bugun


def finviz_haberlerini_cek() -> list[dict]:
    """Finviz'deki 'News' tablosundaki tum satirlari (genelde bugun + dunun
    bir kismi) sozluk listesi olarak dondurur."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = requests.get(FINVIZ_URL, headers=headers, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    tablo = soup.select_one("table.styled-table-new")
    if tablo is None:
        raise RuntimeError("Finviz sayfasında haber tablosu bulunamadı (site yapısı değişmiş olabilir).")

    bugun = datetime.now().date()
    haberler: list[dict] = []
    gorulen_url: set[str] = set()
    sayac = 0

    for satir in tablo.select("tr.news_table-row"):
        saat_hucre = satir.select_one(".news_date-cell")
        link = satir.select_one(".news_link-cell a")
        if saat_hucre is None or link is None:
            continue

        saat_metni = saat_hucre.get_text(strip=True)
        tarih = _satir_tarihini_belirle(saat_metni, bugun)

        url = link.get("href", "").strip()
        baslik = link.get_text(strip=True)
        ozet_hucre = satir.select_one(".news_link-cell")
        kaynak_ozeti = ozet_hucre.get("data-boxover-text", "").strip() if ozet_hucre else ""

        if not url or url in gorulen_url:
            continue
        gorulen_url.add(url)

        try:
            kaynak = urlparse(url).netloc.replace("www.", "")
        except Exception:
            kaynak = ""

        haberler.append(
            {
                "id": str(sayac),
                "saat": saat_metni,
                "tarih": tarih.isoformat(),
                "baslik": baslik,
                "url": url,
                "kaynak": kaynak,
                "kaynakOzeti": kaynak_ozeti,
            }
        )
        sayac += 1

    return haberler
