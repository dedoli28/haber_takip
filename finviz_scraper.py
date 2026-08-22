"""Finviz haber/blog sayfalarini ceker. Sunucu tarafinda calisir.

Finviz'in birden fazla haber kategorisi var (Piyasa, Hisse, ETF, Kripto) ve
her biri ayri bir URL'de (?v=N). Kategoriye gore saat hucresinin formati da
degisiyor:
  - Piyasa Haberleri ve Bloglar: mutlak saat (ör. "02:05PM") ya da "Aug-19"
  - Hisse/ETF/Kripto Haberleri: goreli sure (ör. "19 min", "3 hours") ya da
    "Aug-19"

Not: Finviz'in "Market Pulse" (?v=6) sekmesi tamamen farkli bir yapida
(gercek makale linki yok, AI uretimi ticker guncellemeleri) oldugu icin
desteklenmiyor.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

FINVIZ_URL = "https://finviz.com/news.ashx"

KATEGORI_V_PARAM = {
    "ana": None,
    "hisse": "3",
    "etf": "4",
    "kripto": "5",
}

TIME_RE = re.compile(r"^\d{1,2}:\d{2}\s*(AM|PM)$", re.IGNORECASE)
RELATIVE_RE = re.compile(r"^\d+\s*(min|mins|minute|minutes|hour|hours|hr|hrs)$", re.IGNORECASE)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _satir_tarihini_belirle(saat_metni: str, bugun: date) -> date:
    if TIME_RE.match(saat_metni) or RELATIVE_RE.match(saat_metni):
        return bugun
    try:
        d = datetime.strptime(f"{saat_metni}-{bugun.year}", "%b-%d-%Y").date()
        if d > bugun:
            d = d.replace(year=d.year - 1)
        return d
    except ValueError:
        return bugun


def _sayfayi_getir(v_param: str | None) -> BeautifulSoup:
    url = FINVIZ_URL if not v_param else f"{FINVIZ_URL}?v={v_param}"
    resp = requests.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _tabloyu_ayristir(tablo) -> list[dict]:
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


def finviz_haberlerini_cek(kategori: str = "ana") -> list[dict]:
    """Secilen kategorideki (ana/hisse/etf/kripto) haber tablosunu ceker."""
    if kategori not in KATEGORI_V_PARAM:
        kategori = "ana"
    soup = _sayfayi_getir(KATEGORI_V_PARAM[kategori])
    tablo = soup.select_one("table.styled-table-new")
    if tablo is None:
        raise RuntimeError("Finviz sayfasında haber tablosu bulunamadı (site yapısı değişmiş olabilir).")
    return _tabloyu_ayristir(tablo)


def finviz_bloglarini_cek() -> list[dict]:
    """Bloglar tablosu yalnizca ana (kategori parametresiz) sayfada, ikinci
    tablo olarak bulunuyor; kategori seciminden bagimsizdir."""
    soup = _sayfayi_getir(None)
    tablolar = soup.select("table.styled-table-new")
    if len(tablolar) < 2:
        raise RuntimeError("Finviz sayfasında bloglar tablosu bulunamadı (site yapısı değişmiş olabilir).")
    return _tabloyu_ayristir(tablolar[1])
