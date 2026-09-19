"""Finviz haber/blog sayfalarini ceker. Sunucu tarafinda calisir.

Finviz'in birden fazla haber kategorisi var (Piyasa, Hisse, ETF, Kripto,
Pazar Nabzi) ve her biri ayri bir URL'de (?v=N). Kategoriye gore saat
hucresinin formati da degisiyor:
  - Piyasa Haberleri ve Bloglar: mutlak saat (ör. "02:05PM") ya da "Aug-19"
  - Hisse/ETF/Kripto/Pazar Nabzi Haberleri: goreli sure (ör. "19 min",
    "3 hours") ya da "Aug-19"

Not: "Pazar Nabzi" (?v=6) diger kategorilerden farkli bir yapida - gercek
disaridan bir makale linki yok, bunun yerine Finviz'in kendi AI'inin
urettigi kisa ticker guncellemeleri var (ör. "BofA Securities initiates
Alvotech with Buy rating"), her birine bir hisse rozeti (ticker + varsa
fiyat degisimi) iliskilendirilmis. Bu yuzden ayri bir ayristirici kullanir;
"Kaynagi Ac" o hissenin Finviz sayfasina gider.
"""

from __future__ import annotations

import concurrent.futures
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

FINVIZ_URL = "https://finviz.com/news.ashx"
FINVIZ_BASE = "https://finviz.com"

KATEGORI_V_PARAM = {
    "ana": None,
    "hisse": "3",
    "etf": "4",
    "kripto": "5",
    "pazar_nabzi": "6",
}

TIME_RE = re.compile(r"^\d{1,2}:\d{2}\s*(AM|PM)$", re.IGNORECASE)
RELATIVE_RE = re.compile(r"^(\d+)\s*(min|mins|minute|minutes|hour|hours|hr|hrs)$", re.IGNORECASE)

# Finviz zamanlari kendi sitesinde ABD Dogu saatiyle (New York) gosterilir;
# "bugun" hesabi da bu saat dilimine gore yapilmali, yoksa UTC gece yarisi
# civarinda Finviz'in gunuyle bir gun kayabilir.
NY_TZ = ZoneInfo("America/New_York")

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


def _saat_metnini_utc_zamanina_cevir(saat_metni: str, tarih: date, simdi_utc: datetime) -> datetime | None:
    """Finviz'in saat hucresindeki metni gercek bir UTC zaman damgasina
    cevirir: mutlak saatler ('02:05PM') Finviz'in kendi saat dilimi olan
    ABD Dogu saatine (America/New_York) gore yazilir; goreli sureler
    ('19 min', '3 hours') tarama anindan geriye dogru hesaplanir. Sadece
    tarih iceren eski kayitlarda (ör. 'Aug-19') saat bilgisi olmadigindan
    None doner."""
    if TIME_RE.match(saat_metni):
        try:
            saat_kismi = datetime.strptime(saat_metni.upper().replace(" ", ""), "%I:%M%p").time()
        except ValueError:
            return None
        yerel = datetime.combine(tarih, saat_kismi, tzinfo=NY_TZ)
        return yerel.astimezone(timezone.utc)

    m = RELATIVE_RE.match(saat_metni)
    if m:
        sayi = int(m.group(1))
        birim = m.group(2).lower()
        delta = timedelta(minutes=sayi) if birim.startswith("min") else timedelta(hours=sayi)
        return simdi_utc - delta

    return None


def _sayfayi_getir(v_param: str | None) -> BeautifulSoup:
    url = FINVIZ_URL if not v_param else f"{FINVIZ_URL}?v={v_param}"
    resp = requests.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _tabloyu_ayristir(tablo) -> list[dict]:
    simdi_utc = datetime.now(timezone.utc)
    bugun = simdi_utc.astimezone(NY_TZ).date()
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
        zaman_utc = _saat_metnini_utc_zamanina_cevir(saat_metni, tarih, simdi_utc)

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
                "zamanUtc": zaman_utc.isoformat(timespec="milliseconds") if zaman_utc else None,
                "baslik": baslik,
                "url": url,
                "kaynak": kaynak,
                "kaynakOzeti": kaynak_ozeti,
            }
        )
        sayac += 1

    return haberler


def _pazar_nabzi_ayristir(tablo) -> list[dict]:
    simdi_utc = datetime.now(timezone.utc)
    bugun = simdi_utc.astimezone(NY_TZ).date()
    haberler: list[dict] = []
    gorulen: set[str] = set()
    sayac = 0

    for satir in tablo.select("tr.news_table-row"):
        saat_hucre = satir.select_one(".news_date-cell")
        baslik_el = satir.select_one(".market-pulse-headline")
        if saat_hucre is None or baslik_el is None:
            continue

        saat_metni = saat_hucre.get_text(strip=True)
        tarih = _satir_tarihini_belirle(saat_metni, bugun)
        zaman_utc = _saat_metnini_utc_zamanina_cevir(saat_metni, tarih, simdi_utc)
        baslik = baslik_el.get_text(strip=True)

        anahtar = f"{saat_metni}|{baslik}"
        if anahtar in gorulen:
            continue
        gorulen.add(anahtar)

        bilgi_parcalari = []
        url = None
        for rozet in satir.select(".market-pulse-badges a[data-boxover-ticker]"):
            sirket = rozet.get("data-boxover-company", "").strip()
            tam_metin = rozet.get_text(strip=True)
            ilk_span = rozet.select_one("span")
            gorunen_ticker = (
                ilk_span.get_text(strip=True) if ilk_span else (tam_metin.split()[0] if tam_metin else "")
            )
            degisim = tam_metin[len(gorunen_ticker):].strip() if tam_metin.startswith(gorunen_ticker) else ""

            parca = f"{sirket} ({gorunen_ticker})" if sirket else gorunen_ticker
            if degisim:
                parca += f", fiyat değişimi: {degisim}"
            if parca:
                bilgi_parcalari.append(parca)

            if url is None:
                href = rozet.get("href", "").strip()
                if href:
                    url = href if href.startswith("http") else f"{FINVIZ_BASE}{href}"

        haberler.append(
            {
                "id": str(sayac),
                "saat": saat_metni,
                "tarih": tarih.isoformat(),
                "zamanUtc": zaman_utc.isoformat(timespec="milliseconds") if zaman_utc else None,
                "baslik": baslik,
                "url": url or "https://finviz.com/news?v=6",
                "kaynak": "finviz.com",
                "kaynakOzeti": ("İlgili şirket(ler): " + "; ".join(bilgi_parcalari)) if bilgi_parcalari else "",
            }
        )
        sayac += 1

    return haberler


def finviz_haberlerini_cek(kategori: str = "ana") -> list[dict]:
    """Secilen kategorideki (ana/hisse/etf/kripto/pazar_nabzi) haber
    tablosunu ceker."""
    if kategori not in KATEGORI_V_PARAM:
        kategori = "ana"
    soup = _sayfayi_getir(KATEGORI_V_PARAM[kategori])
    tablo = soup.select_one("table.styled-table-new")
    if tablo is None:
        raise RuntimeError("Finviz sayfasında haber tablosu bulunamadı (site yapısı değişmiş olabilir).")

    if kategori == "pazar_nabzi":
        return _pazar_nabzi_ayristir(tablo)
    return _tabloyu_ayristir(tablo)


TUM_TURLER = ["ana", "hisse", "etf", "kripto", "pazar_nabzi", "blog"]


def _ana_ve_blog_cek() -> dict[str, list[dict]]:
    """'ana' ve 'blog' ayni varsayilan sayfadaki (ilk ve ikinci tablo) iki
    farkli tablodur; sayfayi TEK seferde cekip her ikisini de oradan
    ayristirmak, iki ayri HTTP istegi atmaktan daha hizlidir."""
    soup = _sayfayi_getir(None)
    tablolar = soup.select("table.styled-table-new")
    if not tablolar:
        raise RuntimeError("Finviz sayfasında haber tablosu bulunamadı (site yapısı değişmiş olabilir).")
    if len(tablolar) < 2:
        raise RuntimeError("Finviz sayfasında bloglar tablosu bulunamadı (site yapısı değişmiş olabilir).")
    return {"ana": _tabloyu_ayristir(tablolar[0]), "blog": _tabloyu_ayristir(tablolar[1])}


def tum_turleri_cek() -> tuple[list[dict], list[str]]:
    """Tum haber kategorilerini + bloglari PARALEL olarak ceker (Vercel/
    cron-job.org zaman asimini asmamak icin - 5 istegi sirayla degil ayni
    anda atar), her ogeye 'kategori' alanini ekler, URL'e gore tekillestirir.
    (haberler, hatalar) tuple'i dondurur - bir kategori basarisiz olursa
    digerlerine devam eder."""
    birlesik: list[dict] = []
    gorulen: set[str] = set()
    hatalar: list[str] = []
    sonuclar: dict[str, list[dict]] = {}

    gorevler = {
        "ana_blog": _ana_ve_blog_cek,
        "hisse": lambda: finviz_haberlerini_cek("hisse"),
        "etf": lambda: finviz_haberlerini_cek("etf"),
        "kripto": lambda: finviz_haberlerini_cek("kripto"),
        "pazar_nabzi": lambda: finviz_haberlerini_cek("pazar_nabzi"),
    }

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(gorevler)) as executor:
        gelecek_ad = {executor.submit(fn): ad for ad, fn in gorevler.items()}
        for gelecek in concurrent.futures.as_completed(gelecek_ad):
            ad = gelecek_ad[gelecek]
            try:
                sonuc = gelecek.result()
            except Exception as e:  # noqa: BLE001
                hatalar.append(f"{'ana/blog' if ad == 'ana_blog' else ad}: {e}")
                continue
            if ad == "ana_blog":
                sonuclar.update(sonuc)
            else:
                sonuclar[ad] = sonuc

    for kategori in TUM_TURLER:
        for o in sonuclar.get(kategori, []):
            if o["url"] in gorulen:
                continue
            gorulen.add(o["url"])
            o["kategori"] = kategori
            birlesik.append(o)

    return birlesik, hatalar
