"""Finviz haber/blog sayfalarini ve Turkiye/Almanya/Cin RSS kaynaklarini
ceker. Sunucu tarafinda calisir.

Haber kaynaklari (her haberde 'ulke' alani tutulur):
  - ABD (US): Finviz (asagida ayrintili), HTML kazima.
  - Turkiye (TR), Almanya (DE), Cin (CN): RSS/Atom akislari (RSS_KAYNAKLARI).

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
import functools
import hashlib
import re
import warnings
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# BeautifulSoup, duz metin (etiket icermeyen) bir dizeyi ayristirirken
# "bu bir URL/dosya adina benziyor" uyarisi verebilir; zararsiz, sessize al.
warnings.filterwarnings("ignore", module="bs4")

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
                "ulke": "US",
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
                "ulke": "US",
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


# ===================== RSS/Atom kaynaklari (TR, DE, CN) =====================
#
# HTML kazima yerine mumkun olan yerde resmi RSS/XML akislari kullanilir:
# kaynak sitenin tasarimi degisse bile akis bicimi sabit kalir.
#   kod:   kaynagin kalici kisa adi (haberin 'kaynakKodu' alani; bir kaynak
#          hata verdiginde ONUN haberlerini depodan silmemek icin kullanilir)
#   ad:    kullaniciya gosterilen kaynak adi ('kaynak' alani)
#   ulke:  TR / DE / CN
#   kaynak_alani: True ise her <item> kendi asil yayincisini <source>
#          etiketinde tasir (Google News); 'kaynak' o zaman oradan alinir.
#   ozet_kullan: False ise akistaki <description> bilgi tasimaz (Google News'te
#          sadece baslik + yayinci adi tekrari), kaynakOzeti bos birakilir.
RSS_KAYNAKLARI = [
    {
        "kod": "tr-investing",
        "ad": "Investing.com",
        "ulke": "TR",
        "url": "https://tr.investing.com/rss/news_1067.rss",
    },
    {
        "kod": "tr-sozcu",
        "ad": "Sözcü",
        "ulke": "TR",
        "url": "https://www.sozcu.com.tr/feeds-rss-category-borsa",
    },
    {
        "kod": "de-wallstreet-online",
        "ad": "wallstreet:online",
        "ulke": "DE",
        "url": "https://www.wallstreet-online.de/rss/nachrichten",
    },
    {
        "kod": "de-tagesschau",
        "ad": "Tagesschau",
        "ulke": "DE",
        "url": "https://www.tagesschau.de/wirtschaft/index~rss2.xml",
    },
    {
        "kod": "cn-google-news",
        "ad": "Google News",
        "ulke": "CN",
        # Arama: China economy OR China stocks OR "Shanghai Composite", son 2 gun.
        "url": (
            "https://news.google.com/rss/search?q=China+economy+OR+China+stocks"
            "+OR+%22Shanghai+Composite%22+when:2d&hl=en-US&gl=US&ceid=US:en"
        ),
        "kaynak_alani": True,
        "ozet_kullan": False,
    },
]

RSS_KAYNAK_BASINA_AZAMI_HABER = 30
RSS_AZAMI_BOYUT_BAYT = 5 * 1024 * 1024
# (baglanti, okuma) saniye: /api/haber-cek'in cron-job.org'un 30 sn'lik siniri
# icinde kalmasi icin yavas bir kaynak tum taramayi bekletmesin.
RSS_ZAMAN_ASIMI = (5, 12)
KAYNAK_OZETI_AZAMI_KARAKTER = 500
ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")

_RSS_HEADERS = {
    "User-Agent": _HEADERS["User-Agent"],
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
}
_ISO_TARIH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _yerel_ad(etiket) -> str:
    """XML etiketinin ad alani onekini atar: '{http://...}link' -> 'link'."""
    return etiket.rsplit("}", 1)[-1] if isinstance(etiket, str) else ""


def _metni_temizle(ham: str) -> str:
    """HTML etiketlerini ve varlik kodlarini (&quot;, &amp; ...) temizleyip
    bosluklari tek bosluga indirger. Bazi akislar (ör. wallstreet:online)
    basliklarini CDATA icinde bir kez daha HTML-kacisla yazar; XML ayristirici
    bunu cozmez, burada cozulur."""
    if not ham:
        return ""
    if "<" in ham or "&" in ham:
        ham = BeautifulSoup(ham, "html.parser").get_text(" ", strip=True)
    return " ".join(ham.split())


def _tarihi_ayristir(metin: str) -> datetime | None:
    """RSS'in RFC 822 tarihlerini ('Sat, 19 Sep 2026 12:28:20 +0300') ve
    Atom/Dublin Core'un ISO 8601 tarihlerini ('2026-09-19T11:00:00+02:00',
    '...Z') UTC'ye cevirir. Saat dilimi HIC verilmemisse UTC kabul edilir
    (Investing.com'un '2026-09-18 15:14:08' bicimi, makale sayfalarindaki
    datePublished ile karsilastirilarak UTC oldugu dogrulandi)."""
    metin = (metin or "").strip()
    if not metin:
        return None

    dt: datetime | None = None
    if _ISO_TARIH_RE.match(metin):
        try:
            dt = datetime.fromisoformat(metin.replace("Z", "+00:00").replace("z", "+00:00"))
        except ValueError:
            dt = None
    else:
        try:
            dt = parsedate_to_datetime(metin)
        except (TypeError, ValueError, IndexError):
            dt = None

    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _cocuk_metni(girdi: ET.Element, *adlar: str) -> str:
    """Girdinin, verilen (ad alansiz) adlardan ILK bulunanin metnini dondurur;
    adlarin sirasi tercih sirasidir."""
    for ad in adlar:
        for cocuk in girdi:
            if _yerel_ad(cocuk.tag) == ad and cocuk.text and cocuk.text.strip():
                return cocuk.text.strip()
    return ""


def _girdi_linki(girdi: ET.Element) -> str:
    """RSS'te <link>url</link>, Atom'da <link rel="alternate" href="url"/>;
    ikisini de destekler. Hicbiri yoksa http(s) ile baslayan <guid>'e duser."""
    atom_adaylari: list[str] = []
    for cocuk in girdi:
        if _yerel_ad(cocuk.tag) != "link":
            continue
        metin = (cocuk.text or "").strip()
        if metin:
            return metin
        href = (cocuk.get("href") or "").strip()
        if href and cocuk.get("rel", "alternate") == "alternate":
            atom_adaylari.append(href)
    if atom_adaylari:
        return atom_adaylari[0]
    guid = _cocuk_metni(girdi, "guid", "id")
    return guid if guid.lower().startswith(("http://", "https://")) else ""


def _xml_ayristir(icerik: bytes) -> ET.Element:
    """Guvenilmeyen bir XML'i ayristirir. Varlik (<!ENTITY) tanimi iceren
    dokumanlar ('billion laughs' turu saldirilar) hic ayristirilmadan
    reddedilir; hicbir gercek haber akisi varlik tanimlamaz."""
    if len(icerik) > RSS_AZAMI_BOYUT_BAYT:
        raise RuntimeError("RSS yaniti cok buyuk.")
    if b"<!ENTITY" in icerik:
        raise RuntimeError("RSS yaniti guvenli olmayan XML varlik tanimi iceriyor.")
    try:
        # Bazi akislar (ör. Sozcu) XML bildiriminden/kokten once bosluk gonderir.
        return ET.fromstring(icerik.lstrip())
    except ET.ParseError as e:
        raise RuntimeError(f"RSS/XML ayrıştırılamadı: {e}") from e


def rss_ayristir(icerik: bytes, kaynak: dict, simdi_utc: datetime | None = None) -> list[dict]:
    """RSS 2.0/1.0 ve Atom akisini haber sozluklerine cevirir; tarihe gore en
    yeniden en eskiye siralayip kaynak basina en fazla
    RSS_KAYNAK_BASINA_AZAMI_HABER haber dondurur. Tarihi olmayan girdilere
    tarama ani atanir; http(s) olmayan/eksik linkli girdiler atlanir."""
    simdi_utc = simdi_utc or datetime.now(timezone.utc)
    kok = _xml_ayristir(icerik)
    girdiler = [e for e in kok.iter() if _yerel_ad(e.tag) in ("item", "entry")]

    kaynak_kodu = kaynak["kod"]
    haberler: list[dict] = []
    gorulen: set[str] = set()

    for girdi in girdiler:
        url = " ".join(_girdi_linki(girdi).split())
        if not url.lower().startswith(("http://", "https://")) or url in gorulen:
            continue

        baslik = _metni_temizle(_cocuk_metni(girdi, "title"))
        if not baslik:
            continue

        kaynak_adi = kaynak["ad"]
        if kaynak.get("kaynak_alani"):
            yayinci = _metni_temizle(_cocuk_metni(girdi, "source"))
            if yayinci:
                kaynak_adi = yayinci
                # Google News basliklari "Baslik - Yayinci" seklinde biter.
                sonek = f" - {yayinci}"
                if baslik.endswith(sonek):
                    baslik = baslik[: -len(sonek)].rstrip()

        ozet = ""
        if kaynak.get("ozet_kullan", True):
            ozet = _metni_temizle(_cocuk_metni(girdi, "description", "summary", "content", "encoded"))
            if len(ozet) > KAYNAK_OZETI_AZAMI_KARAKTER:
                ozet = ozet[: KAYNAK_OZETI_AZAMI_KARAKTER - 1].rstrip() + "…"
            if ozet == baslik:
                ozet = ""

        zaman = _tarihi_ayristir(_cocuk_metni(girdi, "pubDate", "published", "updated", "date")) or simdi_utc
        yerel = zaman.astimezone(ISTANBUL_TZ)

        gorulen.add(url)
        haberler.append(
            {
                "id": f"{kaynak_kodu}-{hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]}",
                # Kullaniciya gosterilen saat/tarih Istanbul'a gore (uygulama zaten
                # 'zamanUtc'den ayni sekilde yeniden turetir; tutarli kalsin diye).
                "saat": yerel.strftime("%H:%M"),
                "tarih": yerel.date().isoformat(),
                "zamanUtc": zaman.isoformat(timespec="milliseconds"),
                "baslik": baslik,
                "url": url,
                "kaynak": kaynak_adi,
                "kaynakOzeti": ozet,
                "kategori": "ana",
                "ulke": kaynak["ulke"],
                "kaynakKodu": kaynak_kodu,
            }
        )

    haberler.sort(key=lambda h: h["zamanUtc"], reverse=True)
    return haberler[:RSS_KAYNAK_BASINA_AZAMI_HABER]


def rss_haberlerini_cek(kaynak: dict) -> list[dict]:
    """Tek bir RSS/Atom kaynagini indirip ayristirir."""
    resp = requests.get(kaynak["url"], headers=_RSS_HEADERS, timeout=RSS_ZAMAN_ASIMI)
    resp.raise_for_status()
    # .content (bayt): XML kodlamasini (bildirimden ya da varsayilan UTF-8)
    # ayristirici kendisi cozer; .text'in kodlama tahmini yanlis olabilir.
    return rss_ayristir(resp.content, kaynak)


def haber_grubu(oge: dict) -> str:
    """Haberin hangi cekim gorevine ait oldugunu dondurur: RSS haberlerinde
    kaynak kodu, Finviz haberlerinde 'finviz:<kategori>'. Bir gorev hata
    verdiginde yalnizca ONUN haberlerinin depodan/kuyruktan silinmemesi icin
    kullanilir (eski kayitlarda 'kaynakKodu' yoktur, Finviz sayilir)."""
    return oge.get("kaynakKodu") or f"finviz:{oge.get('kategori', 'ana')}"


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


def tum_turleri_cek() -> tuple[list[dict], list[str], set[str]]:
    """Tum Finviz kategorilerini + bloglari VE tum RSS kaynaklarini (TR/DE/CN)
    PARALEL olarak ceker (Vercel/cron-job.org zaman asimini asmamak icin
    istekleri sirayla degil ayni anda atar), her ogeye 'kategori'/'ulke'
    alanlarini ekler, URL'e gore tekillestirir.

    (haberler, hatalar, basarisiz_gruplar) dondurur. Bir kaynak/kategori
    basarisiz olursa digerlerine devam edilir; basarisiz olanin adi 'hatalar'a,
    haber_grubu() degeri 'basarisiz_gruplar'a yazilir. Cagiran taraf, o
    grubun onceden kayitli haberlerini 'artik akista yok' sanip silmemelidir
    (gecici bir ag hatasi yuzunden haberler silinip Gemini'ye yeniden
    siniflandirtilmasin)."""
    birlesik: list[dict] = []
    gorulen: set[str] = set()
    hatalar: list[str] = []
    basarisiz_gruplar: set[str] = set()
    sonuclar: dict[str, list[dict]] = {}
    rss_sonuclari: dict[str, list[dict]] = {}

    gorevler = {
        "ana_blog": _ana_ve_blog_cek,
        "hisse": lambda: finviz_haberlerini_cek("hisse"),
        "etf": lambda: finviz_haberlerini_cek("etf"),
        "kripto": lambda: finviz_haberlerini_cek("kripto"),
        "pazar_nabzi": lambda: finviz_haberlerini_cek("pazar_nabzi"),
    }
    for kaynak in RSS_KAYNAKLARI:
        gorevler[kaynak["kod"]] = functools.partial(rss_haberlerini_cek, kaynak)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(gorevler)) as executor:
        gelecek_ad = {executor.submit(fn): ad for ad, fn in gorevler.items()}
        for gelecek in concurrent.futures.as_completed(gelecek_ad):
            ad = gelecek_ad[gelecek]
            try:
                sonuc = gelecek.result()
            except Exception as e:  # noqa: BLE001
                if ad == "ana_blog":
                    hatalar.append(f"ana/blog: {e}")
                    basarisiz_gruplar.update({"finviz:ana", "finviz:blog"})
                elif ad in KATEGORI_V_PARAM:
                    hatalar.append(f"{ad}: {e}")
                    basarisiz_gruplar.add(f"finviz:{ad}")
                else:
                    hatalar.append(f"{ad}: {e}")
                    basarisiz_gruplar.add(ad)
                continue
            if ad == "ana_blog":
                sonuclar.update(sonuc)
            elif ad in KATEGORI_V_PARAM:
                sonuclar[ad] = sonuc
            else:
                rss_sonuclari[ad] = sonuc

    for kategori in TUM_TURLER:
        for o in sonuclar.get(kategori, []):
            if o["url"] in gorulen:
                continue
            gorulen.add(o["url"])
            o["kategori"] = kategori
            # Her Finviz sayfasi id'leri kendi icinde 0'dan saydigi icin
            # kategori onekiyle tum sette benzersiz yapilir.
            o["id"] = f"finviz-{kategori}-{o['id']}"
            birlesik.append(o)

    for kaynak in RSS_KAYNAKLARI:
        for o in rss_sonuclari.get(kaynak["kod"], []):
            if o["url"] in gorulen:
                continue
            gorulen.add(o["url"])
            birlesik.append(o)

    return birlesik, hatalar, basarisiz_gruplar
