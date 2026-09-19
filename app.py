"""
Haber Takip Platformu - Vercel icin web surumu (v2: surekli izleme).

Mimari:
  - Haber kaynaklari: ABD (Finviz, HTML kazima) + Turkiye, Almanya, Cin (RSS/
    Atom akislari); hepsi finviz_scraper.tum_turleri_cek() icinde PARALEL
    cekilir, her haberin 'ulke' alani (US/TR/DE/CN) vardir.
  - Tarama iki BAGIMSIZ adima bolunmustur (biri yavaslasa/zaman asimina
    ugrasa bile digerini etkilemesin, ikisi de kendi cron'undan ayri ayri
    tetiklenebilsin diye):
      - /api/haber-cek: TUM kaynaklari (Finviz turleri + bloglar + RSS)
        PARALEL ceker (Gemini'ye HIC dokunmaz, ~2sn surer), daha once
        gorulmemis olanlari Upstash Redis'teki bekleme kuyruguna
        (htp:bekleyen_siniflandirma) ekler; artik kaynakta olmayanlari
        depodan siler (hata veren bir kaynagin haberlerine dokunmaz).
      - /api/haber-siniflandir: kuyruktan en fazla
        MAX_ISLENECEK_SINIFLANDIRMA_BASINA kadar haberi (kategoriler arasinda
        adil dagitarak) Gemini ile siniflandirir/ozetler/Turkce'ye cevirir,
        kalici depoya ekler; kuyruktaki fazlasi bir sonraki cagriya kalir.
      - /api/tara: geriye donuk uyumluluk/manuel test icin ikisini sirayla
        cagiran bir kisayoldur.
    Ucu de disaridan (cron-job.org gibi bir servisten) periyodik cagrilir,
    X-Poll-Secret basligiyla (POLL_SECRET) korunur; POLL_SECRET tanimli
    degilse bu uc noktalar HIC calismaz (401).
  - Esik bildirimleri: esikler artik SABIT (ESIKLER: 10 cok onemli / 25 onemli
    / 50 bakmaya deger), kullanici tarafindan degistirilemez. Gece sessiz
    saatlerinde (00:00-06:00, Europe/Istanbul) esik e-postasi gonderilmez,
    haberler sadece birikir. Bir aliciya en fazla MIN_GONDERIM_ARALIGI_DK'da
    (90 dk) bir esik e-postasi gider.
  - /api/sabah-ozeti: HARICI bir cron gorevi tarafindan her sabah 06:00'da
    cagrilir; gece boyunca biriken cok_onemli haberleri toplu gonderir.
  - Gun ozeti (gun_ozeti_servisi.py): Istanbul saatiyle 10:00/14:00/18:00'de
    uretilir. Vercel Cron (vercel.json, UTC 07/11/15) GET /api/cron/gun-ozeti'ni
    CRON_SECRET (Authorization: Bearer) ile cagirir; bugunun en yeni haberlerinden
    (kategori basina en fazla 8, ulkeler arasinda dengeli) Gemini ile TEK ozet
    uretilip tarih anahtarli kayda (htp:gun_ozeti:v2:<tarih>, durum ready|
    generating|failed) yazilir. GET /api/gun-ozeti ("Gunu Ozetle" modali) Gemini'yi
    HIC cagirmaz, kaydi okur; ozet bayatsa POST /api/gun-ozeti/yenile kontrollu
    (kilit + 15 dk bekleme) yenileme baslatir. /api/gun-ozeti-olustur harici cron
    servisi icin ayni mantigi X-Poll-Secret ile sunar.
  - /api/gun-sonu: HARICI bir cron gorevi tarafindan gunde bir kez (ör.
    23:59) cagrilir; her aliciya gunun ozetini (Redis'te bugune ait hazir
    ozet varsa onu, yoksa BIR kez uretip Redis'e kaydederek) + esige hic
    ulasmayip bildirilmemis kalan haberleri tek e-postada gonderir,
    sayaclari sifirlar.
  - /api/haberler: depodaki tum haberleri dondurur (istemci bunlari
    tarih/tur/ulke/onem/saat araligina gore kendi tarafinda filtreler/siralar).
  - /api/analiz: istege bagli AI islemi; sunucunun kendi GEMINI_API_KEY'ini
    kullanir ve IP basina 10 dakikada 5 istekle sinirlidir.
  - /api/ayarlar: yalnizca bildirim e-postalarini (liste) okur/yazar.

Statik arayuz (static_ui/) ayni uygulama uzerinden servis edilir.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from html import escape as _html_kacis
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import email_client
import gun_ozeti_servisi
import market_data_service
import market_symbols
import redis_store
from finviz_scraper import _saat_metnini_utc_zamanina_cevir, haber_grubu, tum_turleri_cek
from gemini_client import (
    KATEGORI_ETIKET,
    analiz_prompt_olustur,
    analiz_schema_olustur,
    gemini_json_iste,
    gun_ozeti_prompt_olustur,
    gun_ozeti_schema_olustur,
    siniflandirma_prompt_olustur,
    siniflandirma_schema_olustur,
)

app = FastAPI(title="Haber Takip Platformu")

# CORS: yalnizca bilinen originlere izin verilir (arayuz zaten ayni originden
# servis edildigi icin normal kullanimda CORS gerekmez). Ek originler
# ALLOWED_ORIGINS ortam degiskeniyle, virgulle ayrilarak eklenir.
VARSAYILAN_IZINLI_ORIGINLER = [
    "https://haber-takip-nper.vercel.app",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]


def _izinli_originler() -> list[str]:
    ek = [o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",")]
    originler = list(VARSAYILAN_IZINLI_ORIGINLER)
    originler.extend(o for o in ek if o and o not in originler)
    return originler


app.add_middleware(
    CORSMiddleware,
    allow_origins=_izinli_originler(),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Poll-Secret"],
)
# /api/haberler (yuzlerce haber) ve statik dosyalar (grafik kutuphanesi) icin
# yanit boyutunu kucultur.
app.add_middleware(GZipMiddleware, minimum_size=1024)

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
# 20'lik gruplar Gemini'de gercekten 10+ saniye surebiliyor (zaman asimi
# degil, gercekten o kadar uretim suresi istiyor - 20 haberi cevirip
# ozetlemek epey token demek). Daha kucuk gruplar hem daha hizli doner hem
# de cron-job.org'un sabit 30 saniyelik siniri icinde kalma sansini artirir.
TARA_GRUP_BOYUTU = 10
# Bir /api/haber-siniflandir cagrisinda siniflandirilacak azami haber
# sayisi: TAM OLARAK tek bir Gemini grubuna esitlenir, boylece tek cagri
# cron-job.org'un sabit 30 saniyelik siniri icinde guvenle kalir. Kuyruktaki
# fazlasi kuyrukta kalir, bir sonraki cagrida sirayla islenir.
MAX_ISLENECEK_SINIFLANDIRMA_BASINA = TARA_GRUP_BOYUTU
SINIF_ESIK_LISTESI = redis_store.SINIF_ESIK_LISTESI
SINIF_ETIKET_TR = {"cok_onemli": "Çok Önemli", "onemli": "Önemli", "bakmaya_deger": "Bakmaya Değer"}

# Esikler artik kullanicidan alinmiyor, sabit: herkes ayni sayilari kullanir.
ESIKLER = {"cok_onemli": 10, "onemli": 25, "bakmaya_deger": 50}

# Ayni alici arka arkaya cok sik esik e-postasi almasin diye, bir alici
# ancak bu kadar dakikada bir esik e-postasi alabilir (esik erken asilsa
# bile, bu sure dolana kadar biriken haberler e-postada bekletilir).
MIN_GONDERIM_ARALIGI_DK = 90

# Gece sessiz saatleri: bu saatler arasinda esik e-postasi GONDERILMEZ
# (haberler yine de sayaca eklenmeye devam eder); sabah GECE_BITIS saatinde
# /api/sabah-ozeti gece boyunca biriken cok_onemli haberleri toplu gonderir.
GECE_BASLANGIC_SAAT = 0
GECE_BITIS_SAAT = 6
ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")

# Gun ozeti: Gemini'ye bugunun EN YENI haberlerinden kategori basina en fazla
# bu kadari gonderilir (tum depo degil) - kota ve timeout tasarrufu.
GUN_OZETI_KATEGORI_BASINA_HABER = 8
# Vercel fonksiyon siniri 60 sn (vercel.json); Redis okuma/yazma icin pay
# birakmak icin ozet uretiminde tek deneme + 25 sn timeout.
GUN_OZETI_GEMINI_TIMEOUT_SN = 25
GUN_OZETI_GEMINI_DENEME = 1

# /api/analiz herkese acik oldugu icin Gemini kotasini tuketmesin diye IP
# basina sinirlanir.
ANALIZ_AZAMI_ISTEK = 5
ANALIZ_PENCERE_SN = 10 * 60

# Ulke sirasi (dengeli secimde ve gosterimde): Turkiye, ABD, Almanya, Cin.
ULKE_SIRA = ["TR", "US", "DE", "CN"]


def _gemini_anahtari() -> str:
    return os.environ.get("GEMINI_API_KEY", "")


def _istanbul_saati() -> datetime:
    return datetime.now(ISTANBUL_TZ)


def _tarihe_gore_yaklasik_zaman(tarih_str: str | None) -> datetime | None:
    """Finviz'in sadece tarih verdigi (saat vermedigi, ör. 'Aug-23') eski
    haberleri icin, o gunun ortasini (12:00, Istanbul) yaklasik bir zaman
    olarak kullanir. Bu deger olmadan bu haberler 'ilkGorulme'yi tarama anina
    (simdi) esitlemek zorunda kalir, bu da gunler onceki bir haberi az once
    kesfedilmis gibi gosterip siralamayi (Yeniden Eskiye/Eskiye Yeni) bozar."""
    if not tarih_str:
        return None
    try:
        gun = date.fromisoformat(tarih_str)
    except ValueError:
        return None
    return datetime.combine(gun, dtime(12, 0), tzinfo=ISTANBUL_TZ).astimezone(timezone.utc)


def _ulke_kodu(oge: dict) -> str:
    """Haberin ulke kodu. Ulke alani eklenmeden once kaydedilmis eski
    haberlerin hepsi Finviz'den geldi, yani ABD'dir."""
    return (oge.get("ulke") or "US").upper()


def _ulkeler_arasi_dengeli_sec(ogeler: list[dict], sinir: int) -> list[dict]:
    """Verilen listeden (siralamasini bozmadan, yani ilk gelenler once) en
    fazla 'sinir' oge secerken ulkeler arasinda sirayla (round-robin) dagitir;
    boylece cok haberi olan bir ulke (ör. ABD) digerlerini disarida birakmaz."""
    kovalar: dict[str, list[dict]] = {}
    for o in ogeler:
        kovalar.setdefault(_ulke_kodu(o), []).append(o)

    sirali_ulkeler = [u for u in ULKE_SIRA if u in kovalar] + [u for u in kovalar if u not in ULKE_SIRA]
    secilen: list[dict] = []
    while len(secilen) < sinir and any(kovalar[u] for u in sirali_ulkeler):
        for u in sirali_ulkeler:
            if kovalar[u] and len(secilen) < sinir:
                secilen.append(kovalar[u].pop(0))
    return secilen


def _kategoriler_arasi_adil_sec(ogeler: list[dict], sinir: int) -> list[dict]:
    """Kapasite kadar oge secerken (ulke, kategori) gruplari arasinda sirayla
    (round-robin) dagitir; boylece en cok haberi olan tek bir grup (genelde
    ABD 'ana') kapasitenin tamamini tuketip digerlerini disarida birakmaz.
    Ulkeye gore de ayirmak, ayni 'ana' kategorisine giren TR/DE/CN RSS
    haberlerinin ABD haberlerinin arkasinda beklemesini onler."""
    kategoriler: dict[tuple[str, str], list[dict]] = {}
    for o in ogeler:
        kategoriler.setdefault((_ulke_kodu(o), o.get("kategori", "ana")), []).append(o)

    sirali_kategoriler = list(kategoriler.keys())
    secilen: list[dict] = []
    i = 0
    while len(secilen) < sinir and any(kategoriler[k] for k in sirali_kategoriler):
        k = sirali_kategoriler[i % len(sirali_kategoriler)]
        if kategoriler[k]:
            secilen.append(kategoriler[k].pop(0))
        i += 1
    return secilen


@app.get("/api/haberler")
def haberler():
    try:
        depo = redis_store.depo_yukle()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    ogeler = sorted(depo.values(), key=lambda o: o.get("ilkGorulme", ""), reverse=True)
    # Haberle GUVENILIR bicimde iliskilendirilebilen tek bir hisse kodu varsa
    # (yalnizca Pazar Nabzi), haber detayindaki piyasa grafigi icin eklenir.
    zengin = []
    for o in ogeler:
        sembol = market_symbols.haber_sembolu(o)
        zengin.append({**o, "iliskiliSembol": sembol} if sembol else o)
    return {"ok": True, "haberler": zengin, "sonTarama": redis_store.son_tarama_yukle()}


# ------------------------------------------------------------ Piyasa verisi
_HISSE_KUMESI_TTL_SN = 60
_hisse_kumesi_onbellek: dict = {"zaman": 0.0, "kume": frozenset()}


def _haber_hisse_sembolleri() -> frozenset[str]:
    """Depodaki haberlerde gecen (guvenilir) hisse kodlari: haber detayi
    grafigi icin disariya SORULABILECEK tek hisse kumesi. Kullanici girdisi
    dogrudan saglayiciya gitmesin diye izin listesi buradan turetilir."""
    simdi = time.time()
    if simdi - _hisse_kumesi_onbellek["zaman"] < _HISSE_KUMESI_TTL_SN:
        return _hisse_kumesi_onbellek["kume"]
    depo = redis_store.depo_yukle()
    kume = frozenset(s for s in (market_symbols.haber_sembolu(o) for o in depo.values()) if s)
    _hisse_kumesi_onbellek.update(zaman=simdi, kume=kume)
    return kume


def _hisse_sembolu_dogrula(sembol: str) -> bool:
    try:
        return sembol in _haber_hisse_sembolleri()
    except Exception:  # noqa: BLE001 - dogrulanamayan sembol reddedilir
        return False


def _piyasa_yaniti(yanit: dict, onbellek_sn: int) -> JSONResponse:
    """Taze basarili yanit kisa sure kenarda (CDN) onbelleklenir; eski veri ve
    hatalar asla."""
    basliklar = {"Cache-Control": "no-store"}
    if onbellek_sn > 0:
        basliklar["Cache-Control"] = f"public, max-age=0, s-maxage={min(onbellek_sn, 1800)}"
    return JSONResponse(yanit, headers=basliklar)


def _piyasa_hatasi(e: market_data_service.PiyasaHatasi) -> JSONResponse:
    basliklar = {"Cache-Control": "no-store"}
    if e.retry_after:
        basliklar["Retry-After"] = str(e.retry_after)
    return JSONResponse({"ok": False, "kod": e.kod, "hata": e.mesaj}, status_code=e.http, headers=basliklar)


@app.get("/api/market/config")
def piyasa_yapilandirma():
    """Ulke/endeks/aralik tanimlari (saglayiciya baglanmaz, her zaman calisir)."""
    return JSONResponse(market_data_service.yapilandirma(), headers={"Cache-Control": "public, max-age=0, s-maxage=300"})


@app.get("/api/market/overview")
def piyasa_genel_bakis(country: str = Query("TR", max_length=4)):
    try:
        yanit, sn = market_data_service.genel_bakis(country)
    except market_data_service.PiyasaHatasi as e:
        return _piyasa_hatasi(e)
    return _piyasa_yaniti(yanit, sn)


@app.get("/api/market/history")
def piyasa_gecmis(
    symbol: str = Query(..., max_length=12),
    range_: str = Query("1D", alias="range", max_length=4),
    interval: str | None = Query(None, max_length=4),
):
    try:
        yanit, sn = market_data_service.gecmis(symbol, range_, interval, hisse_dogrulayici=_hisse_sembolu_dogrula)
    except market_data_service.PiyasaHatasi as e:
        return _piyasa_hatasi(e)
    return _piyasa_yaniti(yanit, sn)


# ------------------------------------------------------------ Haber istatistikleri
HABER_ISTATISTIK_ARALIKLARI = {"24h": 24}
KATEGORI_ETIKET_KISA = {
    "ana": "Piyasa",
    "hisse": "Hisse",
    "etf": "ETF",
    "kripto": "Kripto",
    "pazar_nabzi": "Pazar Nabzı",
    "blog": "Blog",
}
SINIF_SIRA = ["cok_onemli", "onemli", "bakmaya_deger", "onemsiz"]
SINIF_ETIKET_TUM = {**SINIF_ETIKET_TR, "onemsiz": "Önemsiz"}


def _haber_istatistikleri_hesapla(depo: dict, simdi_utc: datetime, saat: int = 24) -> dict:
    """Son `saat` saatte (Istanbul saat dilimine gore, icinde bulunulan saat
    dahil) ilk kez goruldugu (ilkGorulme) zamana gore haber sayilari."""
    bitis = simdi_utc.astimezone(ISTANBUL_TZ).replace(minute=0, second=0, microsecond=0)
    baslangic = bitis - timedelta(hours=saat - 1)
    saatlik = [0] * saat
    ulkeler: dict[str, int] = {}
    kategoriler: dict[str, int] = {}
    siniflar: dict[str, int] = {}
    toplam = 0
    for o in depo.values():
        try:
            t = datetime.fromisoformat(o["ilkGorulme"]).astimezone(ISTANBUL_TZ)
        except (KeyError, ValueError, TypeError):
            continue
        idx = int((t - baslangic).total_seconds() // 3600)
        if t < baslangic or idx >= saat:
            continue
        saatlik[idx] += 1
        toplam += 1
        u = _ulke_kodu(o)
        ulkeler[u] = ulkeler.get(u, 0) + 1
        k = o.get("kategori") or "ana"
        kategoriler[k] = kategoriler.get(k, 0) + 1
        s = o.get("sinif") or "bakmaya_deger"
        siniflar[s] = siniflar.get(s, 0) + 1

    return {
        "ok": True,
        "range": f"{saat}h",
        "generated_at": simdi_utc.isoformat(timespec="seconds"),
        "total": toplam,
        "hourly": [
            {"hour_start": (baslangic + timedelta(hours=i)).isoformat(timespec="minutes"), "count": saatlik[i]}
            for i in range(saat)
        ],
        "by_country": [
            {"key": u, "label": market_symbols.ULKELER.get(u, u), "count": ulkeler[u]}
            for u in [*ULKE_SIRA, *[x for x in ulkeler if x not in ULKE_SIRA]]
            if u in ulkeler
        ],
        "by_category": [
            {"key": k, "label": KATEGORI_ETIKET_KISA.get(k, k), "count": c}
            for k, c in sorted(kategoriler.items(), key=lambda kv: -kv[1])
        ],
        "by_importance": [
            {"key": s, "label": SINIF_ETIKET_TUM.get(s, s), "count": siniflar[s]}
            for s in SINIF_SIRA
            if s in siniflar
        ],
    }


@app.get("/api/news/statistics")
def haber_istatistikleri(range_: str = Query("24h", alias="range", max_length=4)):
    saat = HABER_ISTATISTIK_ARALIKLARI.get(range_.strip().lower())
    if saat is None:
        return JSONResponse({"ok": False, "kod": "gecersiz_parametre", "hata": "Geçersiz aralık."}, status_code=400)
    try:
        depo = redis_store.depo_yukle()
    except Exception:  # noqa: BLE001 - ayrinti istemciye gonderilmez
        return JSONResponse({"ok": False, "kod": "depo_hatasi", "hata": "Haber verisi şu anda alınamıyor."}, status_code=503)
    yanit = _haber_istatistikleri_hesapla(depo, datetime.now(timezone.utc), saat)
    return JSONResponse(yanit, headers={"Cache-Control": "public, max-age=0, s-maxage=60"})


@app.get("/api/durum")
def durum():
    """E-posta bildirim mekanizmasini teshis etmek icin: alici basina bekleyen
    (henuz o aliciya mail atilmamis) haber sayilarini, en son gonderim
    zamanini ve e-posta yapilandirmasinin sunucuda tanimli olup olmadigini
    gosterir."""
    try:
        bekleyenler_tum = redis_store.sayaclari_yukle()
        ayarlar = redis_store.ayarlar_yukle()
        son_gonderim = redis_store.son_gonderim_yukle()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    aliciler = ayarlar.get("alicilar", [])

    alici_durumlari = []
    for alici in aliciler:
        eposta = alici.get("eposta")
        if not eposta:
            continue
        bekleyen = bekleyenler_tum.get(eposta, {})
        alici_durumlari.append(
            {
                "eposta": eposta,
                "bekleyenSayilar": {s: len(bekleyen.get(s, [])) for s in SINIF_ESIK_LISTESI},
                "sonGonderim": son_gonderim.get(eposta),
            }
        )

    try:
        kuyruktaBekleyen = len(redis_store.bekleyen_siniflandirma_yukle())
    except Exception:  # noqa: BLE001
        kuyruktaBekleyen = None

    return {
        "ok": True,
        "esikler": ESIKLER,
        "gunduzBaslangicSaat": GECE_BITIS_SAAT,
        "aliciDurumlari": alici_durumlari,
        "epostaYapilandirilmisMi": email_client.yapilandirilmis_mi(),
        "sonTarama": redis_store.son_tarama_yukle(),
        "sonSiniflandirma": redis_store.son_siniflandirma_yukle(),
        "kuyruktaBekleyenSayisi": kuyruktaBekleyen,
        "sonHatalar": redis_store.son_hatalar_yukle(),
        "istanbulSaati": _istanbul_saati().isoformat(timespec="milliseconds"),
    }


@app.get("/api/ayarlar")
def ayarlar_getir():
    try:
        return {"ok": True, "ayarlar": redis_store.ayarlar_yukle()}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)


@app.post("/api/ayarlar")
async def ayarlar_guncelle(request: Request):
    body = await request.json()

    alicilar = []
    gorulen_eposta: set[str] = set()
    for a in body.get("alicilar") or []:
        eposta = (a.get("eposta") or "").strip()
        if not eposta or eposta in gorulen_eposta:
            continue
        gorulen_eposta.add(eposta)
        alicilar.append({"eposta": eposta})

    yeni_ayarlar = {"alicilar": alicilar}
    try:
        redis_store.ayarlar_kaydet(yeni_ayarlar)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)
    return {"ok": True, "ayarlar": yeni_ayarlar}


@app.post("/api/analiz")
async def analiz(request: Request):
    body = await request.json()
    baslik = (body.get("baslik") or "").strip()
    ozet = (body.get("ozet") or "").strip()
    kaynak_ozeti = (body.get("kaynakOzeti") or "").strip()

    api_key = _gemini_anahtari()
    if not api_key:
        return JSONResponse({"ok": False, "hata": "Sunucuda GEMINI_API_KEY tanımlı değil."}, status_code=500)
    if not baslik:
        return JSONResponse({"ok": False, "hata": "Analiz edilecek haber bulunamadı."}, status_code=400)

    # Herkese acik ve Gemini kotasi harcayan bir uc: IP basina sinirla. IP
    # Redis anahtarina dogrudan yazilmaz, SHA-256 ile hashlenip kisaltilir.
    ip_hash = hashlib.sha256(_istemci_ip(request).encode("utf-8")).hexdigest()[:32]
    try:
        siniri_asti = redis_store.istek_siniri_asildi_mi(
            f"htp:rl:analiz:{ip_hash}", ANALIZ_AZAMI_ISTEK, ANALIZ_PENCERE_SN
        )
    except Exception:  # noqa: BLE001
        # Sinir denetlenemiyorsa (Redis erisilemez) Gemini kotasini korumak
        # icin istegi reddederiz; ayrintiyi istemciye sizdirmadan.
        return JSONResponse({"ok": False, "hata": "İstek sınırı şu an denetlenemiyor, lütfen sonra tekrar deneyin."}, status_code=503)
    if siniri_asti:
        return JSONResponse(
            {
                "ok": False,
                "hata": f"Çok fazla analiz isteği gönderdiniz. {ANALIZ_PENCERE_SN // 60} dakika içinde en fazla "
                f"{ANALIZ_AZAMI_ISTEK} analiz yapılabilir; lütfen biraz sonra tekrar deneyin.",
            },
            status_code=429,
            headers={"Retry-After": str(ANALIZ_PENCERE_SN)},
        )

    prompt = analiz_prompt_olustur(baslik, ozet, kaynak_ozeti)
    schema = analiz_schema_olustur()

    try:
        sonuc = gemini_json_iste(prompt, schema, api_key, GEMINI_MODEL)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    return {"ok": True, "analiz": sonuc.get("analiz", "")}


def _haber_istanbul_tarihi(oge: dict) -> str | None:
    """Haberin Istanbul takvim gunu (YYYY-MM-DD): once ilkGorulme'den, yoksa
    kayitli 'tarih' alanindan."""
    try:
        return datetime.fromisoformat(oge["ilkGorulme"]).astimezone(ISTANBUL_TZ).date().isoformat()
    except (KeyError, ValueError, TypeError):
        return oge.get("tarih")


def _gun_ozeti_haberlerini_sec(depo: dict) -> dict[str, list[dict]]:
    """Gun ozetine girecek haberleri secer: yalnizca BUGUNUN (Istanbul) haberleri,
    kategori basina en yeniden en eskiye en fazla GUN_OZETI_KATEGORI_BASINA_HABER
    tane, ulkeler arasinda dengeli (round-robin). 'Onemsiz' haberler ozete
    girmez: borsayla ilgisi olmayan bir haber hem token harcar hem de
    sinirli kategori kontenjanini onemli haberlerden alir."""
    bugun = _istanbul_saati().date().isoformat()
    kategorili: dict[str, list[dict]] = {}
    for o in depo.values():
        if o.get("sinif") == "onemsiz" or _haber_istanbul_tarihi(o) != bugun:
            continue
        kategorili.setdefault(o.get("kategori", "ana"), []).append(o)

    secilen: dict[str, list[dict]] = {}
    for kategori, ogeler in kategorili.items():
        ogeler.sort(key=lambda o: o.get("ilkGorulme", ""), reverse=True)
        secilen[kategori] = _ulkeler_arasi_dengeli_sec(ogeler, GUN_OZETI_KATEGORI_BASINA_HABER)
    return secilen


def _gun_ozeti_uret(depo: dict, api_key: str) -> dict | None:
    """Gemini ile gun ozetini BIR kez uretir (tek deneme, ~25 sn timeout).
    Bugun icin ozetlenecek haber yoksa None doner (Gemini cagrilmaz)."""
    secilen = _gun_ozeti_haberlerini_sec(depo)
    haber_sayisi = sum(len(v) for v in secilen.values())
    if haber_sayisi == 0:
        return None

    prompt = gun_ozeti_prompt_olustur(secilen)
    schema = gun_ozeti_schema_olustur()
    sonuc = gemini_json_iste(
        prompt,
        schema,
        api_key,
        GEMINI_MODEL,
        timeout=GUN_OZETI_GEMINI_TIMEOUT_SN,
        deneme_sayisi=GUN_OZETI_GEMINI_DENEME,
    )
    return {
        "kategoriler": sonuc.get("kategoriler", []),
        "genelOzet": sonuc.get("genel_ozet", ""),
        "olusturulmaZamani": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "haberSayisi": haber_sayisi,
    }


def _gun_ozeti_uretici(depo: dict | None = None) -> dict:
    """Gun ozeti servisinin uretim fonksiyonu: Gemini ile BIR kez ozet uretir.
    Hata kodlari istemciye gitmez (yalnizca kaydedilir): anahtar_yok, depo_hatasi,
    haber_yok ya da Gemini hatasindan turetilen kod."""
    api_key = _gemini_anahtari()
    if not api_key:
        raise gun_ozeti_servisi.OzetHatasi("anahtar_yok")
    if depo is None:
        try:
            depo = redis_store.depo_yukle()
        except Exception as e:  # noqa: BLE001
            raise gun_ozeti_servisi.OzetHatasi("depo_hatasi") from e
    ozet = _gun_ozeti_uret(depo, api_key)
    if ozet is None:
        raise gun_ozeti_servisi.OzetHatasi("haber_yok")
    return {"kategoriler": ozet["kategoriler"], "genelOzet": ozet["genelOzet"], "haberSayisi": ozet["haberSayisi"]}


def _ozet_depo_hatasi() -> JSONResponse:
    return JSONResponse(
        {"ok": False, "kod": "depo_hatasi", "hata": "Günün özeti şu anda alınamıyor."},
        status_code=503,
        headers={"Cache-Control": "no-store"},
    )


def _ozet_yaniti(icerik: dict) -> JSONResponse:
    return JSONResponse(icerik, headers={"Cache-Control": "no-store"})


@app.get("/api/gun-ozeti")
@app.post("/api/gun-ozeti")  # eski istemciler POST kullaniyordu
def gun_ozeti():
    """'Gunu Ozetle' modalinin okudugu uc: Gemini'yi CAGIRMAZ. Bugunun kaydini
    (durum: ready|generating|failed|missing) ve varsa icerigi dondurur; bugun
    icerik yoksa son basarili ozeti (onceki gun) 'gecmisGun' isaretiyle verir.
    Uretim cron ile (10:00/14:00/18:00 Istanbul) ya da kontrollu yenilemeyle olur."""
    try:
        return _ozet_yaniti(gun_ozeti_servisi.oku(uretilebilir=bool(_gemini_anahtari())))
    except Exception:  # noqa: BLE001 - ayrinti istemciye gonderilmez
        return _ozet_depo_hatasi()


@app.post("/api/gun-ozeti/yenile")
def gun_ozeti_yenile():
    """Kullanici kaynakli KONTROLLU yenileme: yalnizca ozet planli saate gore bayatsa,
    uretim devam etmiyorsa ve son denemeden 15 dk gectiyse Gemini'ye gider (tek
    seferde tek istek: Redis kilidi). Diger durumlarda neden bilgisiyle doner."""
    try:
        s = gun_ozeti_servisi.kullanici_yenilemesi(_gun_ozeti_uretici, uretilebilir=bool(_gemini_anahtari()))
    except Exception:  # noqa: BLE001
        return _ozet_depo_hatasi()
    return _ozet_yaniti({**s["durum"], "sonuc": s["sonuc"]})


def _cron_dogrula(request: Request) -> bool:
    """Vercel Cron, CRON_SECRET tanimliysa 'Authorization: Bearer <CRON_SECRET>' basligini
    kendisi ekler. Harici cron servisleri (cron-job.org) icin mevcut X-Poll-Secret de
    kabul edilir. Ikisi de tanimsizsa hicbir istek yetkili sayilmaz."""
    beklenen = os.environ.get("CRON_SECRET", "")
    if beklenen:
        gelen = request.headers.get("authorization", "")
        if hmac.compare_digest(gelen.encode("utf-8"), f"Bearer {beklenen}".encode("utf-8")):
            return True
    return _poll_secret_dogrula(request)


def _ozet_cron_calistir() -> JSONResponse:
    try:
        s = gun_ozeti_servisi.uret(
            _gun_ozeti_uretici, kaynak="cron", min_aralik_sn=gun_ozeti_servisi.CRON_MIN_ARALIK_SN
        )
    except Exception:  # noqa: BLE001
        return _ozet_depo_hatasi()
    kayit = s["kayit"] or {}
    basarisiz = s["sonuc"] == "failed"
    return JSONResponse(
        {
            "ok": not basarisiz,
            "sonuc": s["sonuc"],
            "tarih": kayit.get("tarih"),
            "haberSayisi": kayit.get("haberSayisi"),
            "hataKodu": s["hataKodu"],
        },
        status_code=502 if basarisiz else 200,  # Vercel cron gunlugunde basarisiz gorunsun
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/cron/gun-ozeti")
def cron_gun_ozeti(request: Request):
    """Vercel Cron (vercel.json: 07:00/11:00/15:00 UTC = 10:00/14:00/18:00 Istanbul)
    tarafindan GET ile cagrilir. Kopya teslime karsi (Vercel ayni calismayi
    nadiren iki kez teslim edebilir) son 60 dk icinde uretilmis ozet varsa atlar;
    eszamanli tek Gemini istegi Redis kilidiyle saglanir."""
    if not _cron_dogrula(request):
        return _yetkisiz()
    return _ozet_cron_calistir()


@app.post("/api/gun-ozeti-olustur")
def gun_ozeti_olustur(request: Request):
    """Harici cron servisi (cron-job.org) icin: cron ucuyla ayni mantik, POST +
    X-Poll-Secret (POLL_SECRET) ile korunur."""
    if not _poll_secret_dogrula(request):
        return _yetkisiz()
    return _ozet_cron_calistir()


def _siniflandir_grup(grup: list[dict], api_key: str) -> str | None:
    """Basarisiz olan ogeleri sahte/bozuk bir siniflandirmayla (cevrilmemis
    baslik, bos ozet) kalici olarak isaretlemek YERINE '_siniflandirilamadi'
    ile bayraklar; bu ogeler _siniflandir_calistir tarafindan depoya HIC
    YAZILMAZ, bunun yerine kuyruga geri konup bir sonraki siniflandirma
    cagrisinda yeniden denenirler."""
    prompt = siniflandirma_prompt_olustur(grup)
    schema = siniflandirma_schema_olustur()
    try:
        sonuc = gemini_json_iste(prompt, schema, api_key, GEMINI_MODEL)
    except Exception as e:  # noqa: BLE001
        for o in grup:
            o["_siniflandirilamadi"] = True
        return f"{len(grup)} haberlik grup sınıflandırılamadı, sonraki taramada tekrar denenecek: {e}"

    sonuc_map = {r["id"]: r for r in sonuc.get("results", [])}
    for o in grup:
        r = sonuc_map.get(o["id"])
        if r:
            o["sinif"] = r.get("sinif", "bakmaya_deger")
            o["baslikTr"] = r.get("baslik_tr") or o["baslik"]
            o["ai_ozet"] = r.get("ozet", "")
        else:
            o["_siniflandirilamadi"] = True
    return None


EPOSTA_SINIF_BASINA_AZAMI = 20


def _urlleri_baslik_bazinda_tekillestir(urller: list[str], depo: dict) -> list[str]:
    """Ayni baslikli haber (farkli URL'lerle bekleyen listesine birden fazla
    kez girmis olsa bile) e-postada yalnizca bir kez gorunsun diye, verilen
    URL listesini basliga gore tekillestirir (ilk gorulen URL korunur)."""
    gorulen_baslik: set[str] = set()
    tekil: list[str] = []
    for url in urller:
        o = depo.get(url)
        baslik_norm = ((o.get("baslikTr") or o.get("baslik")) if o else "").strip().lower()
        if baslik_norm and baslik_norm in gorulen_baslik:
            continue
        if baslik_norm:
            gorulen_baslik.add(baslik_norm)
        tekil.append(url)
    return tekil


def _esik_email_html(tetiklenen: dict[str, list[str]], depo: dict) -> str:
    """E-posta govdesini olusturur. Bekleyen liste cok uzunsa (ör. gecmis bir
    gonderim hatasi yuzunden birikmisse) e-postanin devasa buyup zaman
    asimina/gonderim hatasina yol acmamasi icin sinif basina yalnizca en son
    EPOSTA_SINIF_BASINA_AZAMI haber gosterilir, kalani ozetlenir."""
    parcalar = ["<h2>Haber Takip Platformu</h2><p>Aşağıdaki önem eşikleri aşıldı:</p>"]
    for sinif, ham_urller in tetiklenen.items():
        urller = _urlleri_baslik_bazinda_tekillestir(ham_urller, depo)
        gosterilen = urller[-EPOSTA_SINIF_BASINA_AZAMI:]
        gizli_sayisi = len(urller) - len(gosterilen)

        parcalar.append(f"<h3>{SINIF_ETIKET_TR.get(sinif, sinif)} ({len(urller)} yeni haber)</h3><ul>")
        for url in gosterilen:
            o = depo.get(url)
            if not o:
                continue
            baslik = _html_kacis(o.get("baslikTr") or o.get("baslik") or "")
            ozet = _html_kacis(o.get("ai_ozet", ""))
            parcalar.append(f'<li><a href="{_html_kacis(url, quote=True)}">{baslik}</a><br><small>{ozet}</small></li>')
        if gizli_sayisi > 0:
            parcalar.append(f"<li><em>+ {gizli_sayisi} haber daha (uygulamadan görüntüleyebilirsiniz)</em></li>")
        parcalar.append("</ul>")
    return "\n".join(parcalar)


def _gun_sonu_email_html(gun_ozeti: dict, bekleyen: dict, depo: dict) -> str:
    """Gun sonu e-postasinin govdesini olusturur: once haber turu basina
    ozet + genel ozet, sonra (varsa) o gun esige hic ulasmadigi icin
    bildirilmemis kalan haberlerin listesi."""
    parcalar = ["<h2>Haber Takip Platformu — Günün Özeti</h2>"]

    for k in gun_ozeti.get("kategoriler", []):
        etiket = _html_kacis(str(KATEGORI_ETIKET.get(k.get("kategori"), k.get("kategori"))))
        parcalar.append(f"<h3>{etiket}</h3><p>{_html_kacis(k.get('ozet', ''))}</p>")

    if gun_ozeti.get("genelOzet"):
        parcalar.append(f"<h3>Genel Özet</h3><p>{_html_kacis(gun_ozeti['genelOzet'])}</p>")

    toplam_bekleyen = sum(len(bekleyen.get(s, [])) for s in SINIF_ESIK_LISTESI)
    if toplam_bekleyen > 0:
        parcalar.append(
            "<hr><h3>Eşiğe Ulaşmadığı İçin Daha Önce Gönderilmeyen Haberler</h3>"
            "<p>Bu haberler eşiğinize ulaşmadığı için ayrı bir bildirim tetiklemedi, "
            "ama gün bittiği için burada topluca gönderiliyor:</p>"
        )
        for s in SINIF_ESIK_LISTESI:
            urller = _urlleri_baslik_bazinda_tekillestir(bekleyen.get(s, []), depo)
            if not urller:
                continue
            gosterilen = urller[-EPOSTA_SINIF_BASINA_AZAMI:]
            gizli_sayisi = len(urller) - len(gosterilen)
            parcalar.append(f"<h4>{SINIF_ETIKET_TR.get(s, s)} ({len(urller)})</h4><ul>")
            for url in gosterilen:
                o = depo.get(url)
                if not o:
                    continue
                baslik = _html_kacis(o.get("baslikTr") or o.get("baslik") or "")
                ozet = _html_kacis(o.get("ai_ozet", ""))
                parcalar.append(f'<li><a href="{_html_kacis(url, quote=True)}">{baslik}</a><br><small>{ozet}</small></li>')
            if gizli_sayisi > 0:
                parcalar.append(f"<li><em>+ {gizli_sayisi} haber daha</em></li>")
            parcalar.append("</ul>")

    return "\n".join(parcalar)


def _esik_takibi_ve_bildirim(yeni_ogeler: list[dict], depo: dict) -> list[str]:
    """Yeni ogeleri alici basina bekleyen sayaclara ekler, esigi asan
    alicilere e-posta gonderir. Hem gercek taramada (/api/tara) hem sentetik
    test enjeksiyonunda (/api/sentetik-haber) kullanilir, boylece iki yol da
    aynen tetikleme mantigindan gecer.

    Iki kisitlama uygulanir:
      - Gece sessiz saatleri (00:00-06:00, Europe/Istanbul): bu saatlerde
        esik e-postasi GONDERILMEZ, haberler sadece sayaca eklenir. Sabah
        06:00'da /api/sabah-ozeti gece boyunca biriken cok_onemli haberleri
        toplu gonderir.
      - Alici basina MIN_GONDERIM_ARALIGI_DK: esik asilmis olsa bile, o
        aliciya en son ne zaman mail gittiyse aradan bu kadar dakika
        gecmeden yeni mail atilmaz (spam hissi vermesin diye); haberler
        bekleyen listesinde birikmeye devam eder, sure dolunca gonderilir.

    Hata mesajlarinin listesini dondurur."""
    hatalar: list[str] = []

    ayarlar = redis_store.ayarlar_yukle()
    aliciler = [a for a in ayarlar.get("alicilar", []) if a.get("eposta")]

    bekleyenler_tum = redis_store.sayaclari_yukle()  # {eposta: {sinif: [url, ...]}}

    for alici in aliciler:
        bekleyen = bekleyenler_tum.setdefault(alici["eposta"], {s: [] for s in SINIF_ESIK_LISTESI})
        for o in yeni_ogeler:
            s = o.get("sinif")
            if s in bekleyen:
                bekleyen[s].append(o["url"])

    simdi_istanbul = _istanbul_saati()
    gece_mi = GECE_BASLANGIC_SAAT <= simdi_istanbul.hour < GECE_BITIS_SAAT

    if aliciler and not gece_mi:
        if not email_client.yapilandirilmis_mi():
            hatalar.append("GMAIL_ADDRESS / GMAIL_APP_PASSWORD tanımlı değil, e-posta gönderilemedi.")
        else:
            son_gonderim = redis_store.son_gonderim_yukle()
            for alici in aliciler:
                eposta = alici["eposta"]
                bekleyen = bekleyenler_tum[eposta]
                tetiklenen = {
                    s: bekleyen[s] for s in SINIF_ESIK_LISTESI if len(bekleyen.get(s, [])) >= ESIKLER[s]
                }
                if not tetiklenen:
                    continue

                son = son_gonderim.get(eposta)
                if son:
                    try:
                        gecen_dk = (simdi_istanbul - datetime.fromisoformat(son)).total_seconds() / 60
                    except ValueError:
                        gecen_dk = MIN_GONDERIM_ARALIGI_DK
                    if gecen_dk < MIN_GONDERIM_ARALIGI_DK:
                        continue  # esik asildi ama cok yakin zamanda mail gitmis, biraz daha bekle

                try:
                    html = _esik_email_html(tetiklenen, depo)
                    email_client.eposta_gonder([eposta], "Haber Takip Platformu - Yeni Önemli Haberler", html)
                    for s in tetiklenen:
                        bekleyen[s] = []
                    son_gonderim[eposta] = simdi_istanbul.isoformat(timespec="milliseconds")
                except Exception as e:  # noqa: BLE001
                    hatalar.append(f"{eposta}: e-posta gönderilemedi: {e}")

            try:
                redis_store.son_gonderim_kaydet(son_gonderim)
            except Exception as e:  # noqa: BLE001
                hatalar.append(str(e))

    try:
        redis_store.sayaclari_kaydet(bekleyenler_tum)
    except Exception as e:  # noqa: BLE001
        hatalar.append(str(e))

    return hatalar


class TaramaHatasi(Exception):
    def __init__(self, mesaj: str, status_code: int = 502):
        super().__init__(mesaj)
        self.status_code = status_code


def _haber_cek_calistir() -> dict:
    """HIZLI adim: tum kaynaklari (Finviz + TR/DE/CN RSS) PARALEL tarar,
    Gemini'ye HIC dokunmaz (~2sn). Daha once gorulmemis ogeleri siniflandirma
    bekleme kuyruguna ekler; artik kaynakta olmayanlari depodan (ve
    kuyruktan) siler. Basarili durumda yanit sozlugunu dondurur, hata
    durumunda TaramaHatasi firlatir."""

    try:
        guncel_ogeler, tarama_hatalari, basarisiz_gruplar = tum_turleri_cek()
    except Exception as e:  # noqa: BLE001
        raise TaramaHatasi(str(e)) from e

    try:
        depo = redis_store.depo_yukle()
        kuyruk = redis_store.bekleyen_siniflandirma_yukle()
    except Exception as e:  # noqa: BLE001
        raise TaramaHatasi(str(e)) from e

    guncel_url_seti = {o["url"] for o in guncel_ogeler}

    # Ayni haber farkli kategorilerde (ör. Piyasa + Pazar Nabzi) ya da
    # taramalar arasinda degisen takip/yonlendirme parametreleriyle farkli
    # URL'lerle gorunebiliyor. Sadece URL'e gore tekillestirmek bu durumda
    # ayni haberi tekrar tekrar "yeni" sayip kuyruga/e-postaya birebir
    # tekrarlanmasina yol aciyordu; bu yuzden basliga gore de (hem depoda hem
    # zaten kuyrukta bekleyenler icinde) tekillestirilir.
    mevcut_basliklar = {(o.get("baslik") or "").strip().lower() for o in depo.values() if o.get("baslik")}
    kuyruktaki_basliklar = {(o.get("baslik") or "").strip().lower() for o in kuyruk.values() if o.get("baslik")}
    yeni_kuyruklanan = 0
    for o in guncel_ogeler:
        if o["url"] in depo or o["url"] in kuyruk:
            continue
        baslik_norm = (o.get("baslik") or "").strip().lower()
        if baslik_norm and (baslik_norm in mevcut_basliklar or baslik_norm in kuyruktaki_basliklar):
            continue
        if baslik_norm:
            kuyruktaki_basliklar.add(baslik_norm)
        kuyruk[o["url"]] = o
        yeni_kuyruklanan += 1

    # Bu taramada HATA veren bir kaynagin (ör. gecici ag hatasi alan bir RSS)
    # haberleri 'kaynakta artik yok' sayilip silinmez; yoksa bir sonraki
    # basarili taramada hepsi yeni sanilip Gemini'ye yeniden siniflandirtilir
    # (kota israfi) ve esik e-postalarini tekrar tetikler.
    silinen_urller = [
        url for url, o in depo.items() if url not in guncel_url_seti and haber_grubu(o) not in basarisiz_gruplar
    ]
    for url in silinen_urller:
        del depo[url]
    # Kuyrukta bekleyip artik kaynagin guncel listesinde olmayan (ör. sayfa
    # kaydiginda dusmus) ogeler de ayni sekilde ayiklanir.
    kuyruk = {
        url: o for url, o in kuyruk.items() if url in guncel_url_seti or haber_grubu(o) in basarisiz_gruplar
    }

    simdi = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    try:
        redis_store.depo_kaydet(depo)
        redis_store.bekleyen_siniflandirma_kaydet(kuyruk)
        redis_store.son_tarama_kaydet(simdi)
    except Exception as e:  # noqa: BLE001
        raise TaramaHatasi(str(e)) from e

    try:
        onceki_hatalar = redis_store.son_hatalar_yukle()
        if not isinstance(onceki_hatalar, dict):
            onceki_hatalar = {}
        onceki_hatalar["tarama"] = tarama_hatalari
        redis_store.son_hatalar_kaydet(onceki_hatalar)
    except Exception:  # noqa: BLE001
        pass  # tanilama amacli, taramayi basarisiz saymaya degmez

    return {
        "ok": True,
        "yeniKuyruklanan": yeni_kuyruklanan,
        "silinenSayisi": len(silinen_urller),
        "kuyruktaBekleyenToplam": len(kuyruk),
        "toplamDepo": len(depo),
        "taramaHatalari": tarama_hatalari,
    }


def _siniflandir_calistir() -> dict:
    """YAVAS adim: bekleme kuyrugundan en fazla
    MAX_ISLENECEK_SINIFLANDIRMA_BASINA kadar haberi (kategoriler arasinda
    adil dagitarak) Gemini ile siniflandirir/ozetler/Turkce'ye cevirir,
    depoya ekler, esik/e-posta kontrolunu tetikler. Tek cagri TEK bir Gemini
    grubuyla sinirlandirilir, boylece suresi ongorulebilir kalir (cron-job.org
    30sn siniri icinde). Basarili durumda yanit sozlugunu dondurur, hata
    durumunda TaramaHatasi firlatir."""

    api_key = _gemini_anahtari()
    if not api_key:
        raise TaramaHatasi("Sunucuda GEMINI_API_KEY tanımlı değil.", 500)

    try:
        depo = redis_store.depo_yukle()
        kuyruk = redis_store.bekleyen_siniflandirma_yukle()
    except Exception as e:  # noqa: BLE001
        raise TaramaHatasi(str(e)) from e

    if not kuyruk:
        return {
            "ok": True,
            "islenenSayisi": 0,
            "siniflandirilamayanSayisi": 0,
            "kuyruktaKalanSayisi": 0,
            "toplamDepo": len(depo),
            "siniflandirmaHatalari": [],
        }

    yeni_ogeler = _kategoriler_arasi_adil_sec(list(kuyruk.values()), MAX_ISLENECEK_SINIFLANDIRMA_BASINA)
    for o in yeni_ogeler:
        kuyruk.pop(o["url"], None)

    # ONEMLI: kuyruktaki ogeler farkli tarama turlerinden/zamanlardan
    # gelebildigi icin id'leri carpisabilir (her Finviz kategorisi kendi
    # ic sayaciyla ayristirir, ör. iki farkli tur da "0" verebilir). Gruba
    # HEMEN once, tum sette KESIN benzersiz id'ler atanarak Gemini'nin
    # sonucunun yanlis haberle eslesmesi onlenir.
    for _sira, o in enumerate(yeni_ogeler):
        o["id"] = str(_sira)

    siniflandirma_hatalari: list[str] = []
    for i in range(0, len(yeni_ogeler), TARA_GRUP_BOYUTU):
        grup = yeni_ogeler[i : i + TARA_GRUP_BOYUTU]
        hata = _siniflandir_grup(grup, api_key)
        if hata:
            siniflandirma_hatalari.append(hata)

    # Siniflandirilamayan ogeler depoya YAZILMAZ, kuyruga GERI konur; bir
    # sonraki /api/haber-siniflandir cagrisinda tekrar denenir.
    siniflandirilamayan_sayisi = 0
    basarili_ogeler: list[dict] = []
    for o in yeni_ogeler:
        if o.pop("_siniflandirilamadi", False):
            kuyruk[o["url"]] = o
            siniflandirilamayan_sayisi += 1
        else:
            basarili_ogeler.append(o)
    yeni_ogeler = basarili_ogeler

    # Farkli kaynaklarin ORIJINAL basliklari farkli olsa bile (ör. ayni
    # olayi degisik sekilde anlatan iki site), Gemini'nin TURKCE cevirisi
    # ayni/cok benzer metne yakinsayabiliyor (ozellikle Pazar Nabzi'nin
    # kendi ozetiyle gercek bir makalenin ayni olayi anlatmasi durumunda).
    # Bu yuzden siniflandirmadan SONRA, artik elde olan baslikTr'ye gore
    # ikinci bir tekillestirme yapilir.
    mevcut_baslik_tr = {(o.get("baslikTr") or "").strip().lower() for o in depo.values() if o.get("baslikTr")}
    gorulen_yeni_baslik_tr: set[str] = set()
    nihai_yeni_ogeler: list[dict] = []
    for o in yeni_ogeler:
        b = (o.get("baslikTr") or o.get("baslik") or "").strip().lower()
        if b and (b in mevcut_baslik_tr or b in gorulen_yeni_baslik_tr):
            continue
        if b:
            gorulen_yeni_baslik_tr.add(b)
        nihai_yeni_ogeler.append(o)
    yeni_ogeler = nihai_yeni_ogeler

    simdi = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    for o in yeni_ogeler:
        # Finviz zamanlari kendi sitesinde ABD Dogu saatiyle yazilir; scraper
        # bunu zaten gercek bir UTC ana zamana cevirip "zamanUtc" olarak
        # ekliyor. Elde varsa hem ilkGorulme'yi (sirlama/gunun ozeti icin)
        # hem de kullaniciya gosterilen "saat"i Istanbul saatine gore bu
        # degerden turetiyoruz; yoksa (ör. sadece tarih iceren eski kayit)
        # tarama anini kullaniyoruz.
        zaman_utc_str = o.get("zamanUtc")
        zaman_utc = None
        if zaman_utc_str:
            try:
                zaman_utc = datetime.fromisoformat(zaman_utc_str)
            except ValueError:
                zaman_utc = None
        if zaman_utc:
            o["ilkGorulme"] = zaman_utc.isoformat(timespec="milliseconds")
            istanbul_zaman = zaman_utc.astimezone(ISTANBUL_TZ)
            o["saat"] = istanbul_zaman.strftime("%H:%M")
            # finviz_scraper "tarih"i ABD Dogu gunune gore hesapliyor; bu
            # Istanbul'da farkli bir takvim gunune denk gelebilir (ör. NY'de
            # gece yarisina yakin bir haber Istanbul'da ertesi gune sarkar).
            # "Bugun"/"Dun" filtresiyle tutarli olmasi icin Istanbul gunune
            # gore yeniden yaziyoruz.
            o["tarih"] = istanbul_zaman.date().isoformat()
        else:
            yaklasik = _tarihe_gore_yaklasik_zaman(o.get("tarih"))
            o["ilkGorulme"] = yaklasik.isoformat(timespec="milliseconds") if yaklasik else simdi
        depo[o["url"]] = o

    # Her siniflandirmada otomatik bakim: gecmiste (ör. eski koddan kalma)
    # olusmus mukerrer ya da cevrilmeden/ozetsiz kalmis kayitlari da temizler;
    # boylece Ayarlar'daki bakim butonlarina elle basmaya normalde gerek
    # kalmaz. Ayrica saat-cevirme ozelligi eklenmeden once kaydedilmis eski
    # kayitlarin saatini de geriye donuk Turkce saate cevirir.
    saat_donusturulen = _depodaki_eski_saatleri_turkce_saate_cevir(depo)
    tarih_duzeltilen = _depodaki_tarihleri_istanbul_gunune_gore_duzelt(depo)
    mukerrer_temizlenen = _depoyu_mukerrerlerden_ayikla(depo)
    basarisiz_temizlenen = _depodan_basarisiz_siniflandirmalari_ayikla(depo)

    try:
        redis_store.depo_kaydet(depo)
        redis_store.bekleyen_siniflandirma_kaydet(kuyruk)
        redis_store.son_siniflandirma_kaydet(simdi)
    except Exception as e:  # noqa: BLE001
        raise TaramaHatasi(str(e)) from e

    siniflandirma_hatalari.extend(_esik_takibi_ve_bildirim(yeni_ogeler, depo))

    try:
        onceki_hatalar = redis_store.son_hatalar_yukle()
        if not isinstance(onceki_hatalar, dict):
            onceki_hatalar = {}
        onceki_hatalar["siniflandirma"] = siniflandirma_hatalari
        redis_store.son_hatalar_kaydet(onceki_hatalar)
    except Exception:  # noqa: BLE001
        pass  # tanilama amacli, siniflandirmayi basarisiz saymaya degmez

    return {
        "ok": True,
        "islenenSayisi": len(yeni_ogeler),
        "siniflandirilamayanSayisi": siniflandirilamayan_sayisi,
        "kuyruktaKalanSayisi": len(kuyruk),
        "mukerrerTemizlenenSayisi": mukerrer_temizlenen,
        "basarisizTemizlenenSayisi": basarisiz_temizlenen,
        "saatDonusturulenSayisi": saat_donusturulen,
        "tarihDuzeltilenSayisi": tarih_duzeltilen,
        "toplamDepo": len(depo),
        "siniflandirmaHatalari": siniflandirma_hatalari,
    }


def _poll_secret_dogrula(request: Request) -> bool:
    """Cron/yonetim uc noktalarinin yetkisi. POLL_SECRET tanimli DEGILSE hicbir
    istek yetkili sayilmaz (eskiden tanimsizken uclar acik kaliyordu). Sir
    yalnizca X-Poll-Secret basligindan alinir: sorgu parametresindeki
    (URL'deki) sirlar sunucu/proxy loglarina yazilabilir. Karsilastirma sabit
    zamanlidir (zamanlama saldirisina karsi)."""
    beklenen_sir = os.environ.get("POLL_SECRET", "")
    if not beklenen_sir:
        return False
    gelen_sir = request.headers.get("x-poll-secret", "")
    return hmac.compare_digest(gelen_sir.encode("utf-8"), beklenen_sir.encode("utf-8"))


def _yetkisiz() -> JSONResponse:
    return JSONResponse({"ok": False, "hata": "Yetkisiz."}, status_code=401)


def _istemci_ip(request: Request) -> str:
    """Istemci IP'si. Vercel, X-Forwarded-For'u kendisi (istemci degerini
    ezerek) ayarladigi icin ilk deger gercek istemcidir."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    gercek = request.headers.get("x-real-ip", "").strip()
    if gercek:
        return gercek
    return request.client.host if request.client else "bilinmiyor"


@app.post("/api/haber-cek")
def haber_cek(request: Request):
    """Disaridan (cron-job.org gibi) her 15 dakikada bir cagrilir; X-Poll-Secret
    (POLL_SECRET) ile korunur. Sadece haber kaynaklarini tarar, Gemini'ye
    dokunmaz - hizli ve zaman asimi riski neredeyse yok."""
    if not _poll_secret_dogrula(request):
        return _yetkisiz()

    try:
        return _haber_cek_calistir()
    except TaramaHatasi as e:
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=e.status_code)


@app.post("/api/haber-siniflandir")
def haber_siniflandir(request: Request):
    """Disaridan (cron-job.org gibi) her 15 dakikada bir, /api/haber-cek'ten
    BAGIMSIZ olarak cagrilir; X-Poll-Secret (POLL_SECRET) ile korunur.
    Bekleme kuyrugundan tek bir grubu Gemini ile siniflandirir."""
    if not _poll_secret_dogrula(request):
        return _yetkisiz()

    try:
        return _siniflandir_calistir()
    except TaramaHatasi as e:
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=e.status_code)


@app.post("/api/tara")
def tara(request: Request):
    """Geriye donuk uyumluluk/manuel test icin: /api/haber-cek ve
    /api/haber-siniflandir'i sirayla cagiran kisayol. X-Poll-Secret
    (POLL_SECRET) ile korunur. Otomatik periyodik tetiklemede artik bu ikisini
    AYRI cron'lardan cagirmak (biri yavaslasa bile digerini etkilemesin diye)
    tercih edilir."""
    if not _poll_secret_dogrula(request):
        return _yetkisiz()

    try:
        cek_sonuc = _haber_cek_calistir()
        siniflandir_sonuc = _siniflandir_calistir()
    except TaramaHatasi as e:
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=e.status_code)

    return {
        "ok": True,
        "haberCek": cek_sonuc,
        "siniflandirma": siniflandir_sonuc,
    }


_SAAT_TR_FORMAT_RE = re.compile(r"^\d{2}:\d{2}$")


def _depodaki_eski_saatleri_turkce_saate_cevir(depo: dict) -> int:
    """Saat-cevirme ozelligi eklenmeden once depoya girmis kayitlarin 'saat'
    alani hala Finviz'in ham metni olabilir (ör. '02:05PM', '19 min'). Bu
    kayitlar icin, o haberin ilk gorulme anini (ilkGorulme) referans alarak
    ayni cevrimi geriye donuk uygular; boylece kullanicinin depoyu sifirlamasina
    gerek kalmadan mevcut haberler de duzelir. Zaten cevrilmis ('HH:MM')
    kayitlar atlanir."""
    donusturulen = 0
    for o in depo.values():
        saat_metni = o.get("saat") or ""
        if _SAAT_TR_FORMAT_RE.match(saat_metni):
            continue
        tarih_str = o.get("tarih")
        if not tarih_str:
            continue
        try:
            tarih = date.fromisoformat(tarih_str)
        except ValueError:
            continue
        try:
            referans_simdi = datetime.fromisoformat(o.get("ilkGorulme", ""))
        except ValueError:
            referans_simdi = datetime.now(timezone.utc)
        zaman_utc = _saat_metnini_utc_zamanina_cevir(saat_metni, tarih, referans_simdi)
        if not zaman_utc:
            # Saat bilgisi olmayan (sadece tarih iceren, ör. "Aug-23") eski
            # kayitlarin ilkGorulme'si tarama anina esitlenmis olabilir; bu da
            # gunler onceki bir haberi az once kesfedilmis gibi gosterip
            # siralamayi bozar. O gunun ortasina (yaklasik) cekiyoruz.
            yaklasik = _tarihe_gore_yaklasik_zaman(tarih_str)
            if yaklasik:
                yeni_ilkgorulme = yaklasik.isoformat(timespec="milliseconds")
                if o.get("ilkGorulme") != yeni_ilkgorulme:
                    o["ilkGorulme"] = yeni_ilkgorulme
                    donusturulen += 1
            continue
        istanbul_zaman = zaman_utc.astimezone(ISTANBUL_TZ)
        o["saat"] = istanbul_zaman.strftime("%H:%M")
        o["ilkGorulme"] = zaman_utc.isoformat(timespec="milliseconds")
        o["tarih"] = istanbul_zaman.date().isoformat()
        donusturulen += 1
    return donusturulen


def _depodaki_tarihleri_istanbul_gunune_gore_duzelt(depo: dict) -> int:
    """'saat' alani zaten cevrilmis (HH:MM) olan ama 'tarih' alani hala eski
    (bu duzeltmeden once ABD Dogu gunune gore hesaplanmis) kayitlarda, tarihi
    ilkGorulme'nin Istanbul gunune gore yeniden hesaplar. Boylece 'Bugun'/'Dun'
    filtresi ve tarih rozeti, gosterilen saatle tutarli olur."""
    duzeltilen = 0
    for o in depo.values():
        saat_metni = o.get("saat") or ""
        if not _SAAT_TR_FORMAT_RE.match(saat_metni):
            continue
        try:
            zaman_utc = datetime.fromisoformat(o.get("ilkGorulme", ""))
        except ValueError:
            continue
        dogru_tarih = zaman_utc.astimezone(ISTANBUL_TZ).date().isoformat()
        if o.get("tarih") != dogru_tarih:
            o["tarih"] = dogru_tarih
            duzeltilen += 1
    return duzeltilen


def _depoyu_mukerrerlerden_ayikla(depo: dict) -> int:
    """Ayni (Turkce cevrilmis) basliga sahip mukerrer haberleri depodan siler
    (en once gorulen kopya tutulur). Silinen kayit sayisini dondurur."""
    sirali = sorted(depo.values(), key=lambda o: o.get("ilkGorulme", ""))
    gorulen_baslik: set[str] = set()
    silinecek_urller: list[str] = []
    for o in sirali:
        b = (o.get("baslikTr") or o.get("baslik") or "").strip().lower()
        if b and b in gorulen_baslik:
            silinecek_urller.append(o["url"])
            continue
        if b:
            gorulen_baslik.add(b)
    for url in silinecek_urller:
        del depo[url]
    return len(silinecek_urller)


def _depodan_basarisiz_siniflandirmalari_ayikla(depo: dict) -> int:
    """Hala cevrilmemis/ozetsiz kalmis (baslikTr==baslik ve ai_ozet bos)
    haberleri depodan siler; boylece bir sonraki taramada tekrar 'yeni'
    sayilip yeniden siniflandirilmaya calisilirlar. Silinen kayit sayisini
    dondurur."""
    silinecek_urller = [
        url
        for url, o in depo.items()
        if not (o.get("ai_ozet") or "").strip() and (o.get("baslikTr") or "") == (o.get("baslik") or "")
    ]
    for url in silinecek_urller:
        del depo[url]
    return len(silinecek_urller)


@app.post("/api/depo-sifirla")
def depo_sifirla(request: Request):
    """TUM haber deposunu sifirlar (butun haberleri kalici olarak siler).
    Kok nedeni duzeltilen bir hata (id carpismasi) yuzunden gecmiste bazi
    haberlerin Turkce basligi/ozeti BASKA, alakasiz bir habere ait olmus
    olabilir; hangi kayitlarin bozuk oldugunu guvenilir sekilde ayirt etmenin
    yolu olmadigindan en temiz cozum sifirdan baslamaktir. Sonraki taramalar
    haberleri normal sekilde yeniden cekip (artik duzeltilmis id mantigiyla)
    dogru siniflandirir. Bildirim ayarlari/e-posta listesi ETKILENMEZ,
    yalnizca haber deposu sifirlanir. Geri alinamaz, bilerek kullanin.
    X-Poll-Secret (POLL_SECRET) ile korunur."""
    if not _poll_secret_dogrula(request):
        return _yetkisiz()
    try:
        redis_store.depo_kaydet({})
        redis_store.sayaclari_kaydet({})
        redis_store.bekleyen_siniflandirma_kaydet({})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)
    return {"ok": True}


@app.post("/api/depo-tekillestir")
def depo_tekillestir(request: Request):
    """Mukerrer haberleri depodan siler; ayni islem zaten her siniflandirmada
    otomatik calisir, bu uc elle tetikleme icindir. Silme islemi yaptigi icin
    X-Poll-Secret (POLL_SECRET) ile korunur."""
    if not _poll_secret_dogrula(request):
        return _yetkisiz()
    try:
        depo = redis_store.depo_yukle()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    silinen = _depoyu_mukerrerlerden_ayikla(depo)

    try:
        redis_store.depo_kaydet(depo)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    return {"ok": True, "silinenSayisi": silinen, "kalanSayisi": len(depo)}


@app.post("/api/basarisiz-siniflandirmalari-temizle")
def basarisiz_siniflandirmalari_temizle(request: Request):
    """Cevrilmemis/ozetsiz kalmis bozuk kayitlari depodan siler; ayni islem
    zaten her siniflandirmada otomatik calisir, bu uc elle tetikleme icindir.
    Silme islemi yaptigi icin X-Poll-Secret (POLL_SECRET) ile korunur."""
    if not _poll_secret_dogrula(request):
        return _yetkisiz()
    try:
        depo = redis_store.depo_yukle()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    silinen = _depodan_basarisiz_siniflandirmalari_ayikla(depo)

    try:
        redis_store.depo_kaydet(depo)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    return {"ok": True, "silinenSayisi": silinen, "kalanSayisi": len(depo)}


def _gun_sonu_ozetini_hazirla(depo: dict) -> dict:
    """Gun sonu e-postasinin ozeti: bugune ait icerikli bir ozet varsa (planli
    saatlerde uretilmis) onu kullanir, Gemini cagrilmaz; yoksa BIR kez uretip kaydeder.
    Hata e-postanin gitmesini engellemez; ozetin yerine genel bir not konur."""
    bos = {"kategoriler": [], "genelOzet": ""}
    try:
        hazir = gun_ozeti_servisi.bugunun_hazir_ozeti()
    except Exception:  # noqa: BLE001
        return {**bos, "genelOzet": "Gün özeti şu anda alınamadı."}
    if hazir:
        return hazir

    try:
        sonuc = gun_ozeti_servisi.uret(lambda: _gun_ozeti_uretici(depo), kaynak="gun-sonu")
    except Exception:  # noqa: BLE001
        return {**bos, "genelOzet": "Gün özeti şu anda alınamadı."}
    kayit = sonuc["kayit"] or {}
    if sonuc["sonuc"] in ("ready", "atlandi") and (kayit.get("genelOzet") or kayit.get("kategoriler")):
        return kayit
    if sonuc["hataKodu"] == "haber_yok":
        return bos
    return {**bos, "genelOzet": "Gün özeti oluşturulamadı."}


@app.post("/api/gun-sonu")
def gun_sonu(request: Request):
    """Gunun sonunda, HARICI ikinci bir cron-job.org gorevi tarafindan gunde
    bir kez cagrilir (ornegin 23:45): her aliciya (1) o gunku genel haber
    ozetini VE (2) esigine hic ulasmadigi icin o gune kadar ayrica
    bildirilmemis kalan haberleri TEK bir e-postada gonderir, ardindan o
    alicinin sayaclarini sifirlar (yeni gune temiz baslar). Ozet icin once
    Redis'teki hazir (bugune ait) ozeti kullanir, yoksa BIR kez uretip Redis'e
    kaydeder. X-Poll-Secret (POLL_SECRET) ile korunur."""
    if not _poll_secret_dogrula(request):
        return _yetkisiz()

    try:
        depo = redis_store.depo_yukle()
        ayarlar = redis_store.ayarlar_yukle()
        bekleyenler_tum = redis_store.sayaclari_yukle()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    aliciler = [a for a in ayarlar.get("alicilar", []) if a.get("eposta")]
    if not aliciler:
        return {"ok": True, "gonderilenSayisi": 0}

    gun_ozeti = _gun_sonu_ozetini_hazirla(depo)

    hatalar: list[str] = []
    gonderilen = 0

    if not email_client.yapilandirilmis_mi():
        hatalar.append("GMAIL_ADDRESS / GMAIL_APP_PASSWORD tanımlı değil, e-posta gönderilemedi.")
    else:
        for alici in aliciler:
            eposta = alici["eposta"]
            bekleyen = bekleyenler_tum.get(eposta, {})
            html = _gun_sonu_email_html(gun_ozeti, bekleyen, depo)
            try:
                email_client.eposta_gonder([eposta], "Haber Takip Platformu - Günün Özeti", html)
                bekleyenler_tum[eposta] = {s: [] for s in SINIF_ESIK_LISTESI}
                gonderilen += 1
            except Exception as e:  # noqa: BLE001
                hatalar.append(f"{eposta}: e-posta gönderilemedi: {e}")

    try:
        redis_store.sayaclari_kaydet(bekleyenler_tum)
    except Exception as e:  # noqa: BLE001
        hatalar.append(str(e))

    try:
        redis_store.son_hatalar_kaydet(hatalar)
    except Exception:  # noqa: BLE001
        pass  # tanilama amacli, istegi basarisiz saymaya degmez

    return {"ok": True, "gonderilenSayisi": gonderilen, "hatalar": hatalar}


@app.post("/api/sabah-ozeti")
def sabah_ozeti(request: Request):
    """Her sabah GECE_BITIS_SAAT'te (06:00, Europe/Istanbul), HARICI ucuncu
    bir cron gorevi tarafindan gunde bir kez cagrilir: gece sessiz saatleri
    (00:00-06:00) boyunca esik e-postasi gonderilmeden biriken cok_onemli
    haberleri her aliciya toplu gonderir, ardindan yalnizca o alicinin
    cok_onemli sayacini sifirlar (onemli/bakmaya_deger sayaclari etkilenmez,
    normal akista ya gun icinde esigi asar ya da gun sonunda gonderilir).
    X-Poll-Secret (POLL_SECRET) ile korunur."""
    if not _poll_secret_dogrula(request):
        return _yetkisiz()

    try:
        depo = redis_store.depo_yukle()
        ayarlar = redis_store.ayarlar_yukle()
        bekleyenler_tum = redis_store.sayaclari_yukle()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    aliciler = [a for a in ayarlar.get("alicilar", []) if a.get("eposta")]
    if not aliciler:
        return {"ok": True, "gonderilenSayisi": 0}

    hatalar: list[str] = []
    gonderilen = 0

    if not email_client.yapilandirilmis_mi():
        hatalar.append("GMAIL_ADDRESS / GMAIL_APP_PASSWORD tanımlı değil, e-posta gönderilemedi.")
    else:
        son_gonderim = redis_store.son_gonderim_yukle()
        simdi_iso = _istanbul_saati().isoformat(timespec="milliseconds")
        for alici in aliciler:
            eposta = alici["eposta"]
            bekleyen = bekleyenler_tum.get(eposta, {})
            urller = bekleyen.get("cok_onemli", [])
            if not urller:
                continue
            html = _esik_email_html({"cok_onemli": urller}, depo)
            try:
                email_client.eposta_gonder(
                    [eposta], "Haber Takip Platformu - Gece Boyunca Gelen Çok Önemli Haberler", html
                )
                bekleyen["cok_onemli"] = []
                son_gonderim[eposta] = simdi_iso
                gonderilen += 1
            except Exception as e:  # noqa: BLE001
                hatalar.append(f"{eposta}: e-posta gönderilemedi: {e}")

        try:
            redis_store.son_gonderim_kaydet(son_gonderim)
        except Exception as e:  # noqa: BLE001
            hatalar.append(str(e))

    try:
        redis_store.sayaclari_kaydet(bekleyenler_tum)
    except Exception as e:  # noqa: BLE001
        hatalar.append(str(e))

    return {"ok": True, "gonderilenSayisi": gonderilen, "hatalar": hatalar}


@app.post("/api/sentetik-haber")
def sentetik_haber_ekle(request: Request):
    """Test amaclidir: gercek Finviz taramasi ya da Gemini siniflandirmasi
    beklemeden, dogrudan istenen sinifta/sayida sahte haber ekleyip AYNI
    esik/e-posta tetikleme mantigini (_esik_takibi_ve_bildirim) calistirir.
    Boylece eşik ayarlarinizin gercekten calisip calismadigini deterministik
    sekilde, dakikalarca beklemeden test edebilirsiniz. X-Poll-Secret
    (POLL_SECRET) ile korunur. Eklenen sahte haberler 'test.local'
    adresli sahte URL'ler kullanir; bir sonraki GERCEK taramada kaynakta
    bulunamayacaklari icin otomatik olarak depodan silinirler (kendiliginden
    temizlenir, elle silmeye gerek yoktur)."""
    if not _poll_secret_dogrula(request):
        return _yetkisiz()

    sinif = request.query_params.get("sinif", "cok_onemli")
    if sinif not in SINIF_ESIK_LISTESI:
        return JSONResponse(
            {"ok": False, "hata": f"Geçersiz sınıf: {sinif} (geçerli: {', '.join(SINIF_ESIK_LISTESI)})"},
            status_code=400,
        )
    try:
        adet = max(1, min(200, int(request.query_params.get("adet", "1"))))
    except ValueError:
        adet = 1

    try:
        depo = redis_store.depo_yukle()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    simdi = datetime.now(timezone.utc)
    yeni_ogeler = []
    for i in range(adet):
        url = f"https://test.local/sentetik/{simdi.timestamp()}-{i}"
        oge = {
            "id": url,
            "url": url,
            "kategori": "ana",
            "ulke": "US",
            "tarih": simdi.date().isoformat(),
            "saat": simdi.astimezone(ISTANBUL_TZ).strftime("%H:%M"),
            "kaynak": "sentetik-test",
            "kaynakOzeti": "",
            "baslik": f"[TEST] {SINIF_ETIKET_TR.get(sinif, sinif)} sentetik haber {i + 1}",
            "baslikTr": f"[TEST] {SINIF_ETIKET_TR.get(sinif, sinif)} sentetik haber {i + 1}",
            "sinif": sinif,
            "ai_ozet": "Bu, eşik/e-posta mekanizmasını test etmek için oluşturulmuş sahte bir haberdir.",
            "ilkGorulme": simdi.isoformat(timespec="milliseconds"),
        }
        depo[url] = oge
        yeni_ogeler.append(oge)

    try:
        redis_store.depo_kaydet(depo)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    hatalar = _esik_takibi_ve_bildirim(yeni_ogeler, depo)

    return {"ok": True, "eklenenSayisi": len(yeni_ogeler), "sinif": sinif, "hatalar": hatalar}


@app.post("/api/sentetik-haber-temizle")
def sentetik_haber_temizle(request: Request):
    """/api/sentetik-haber ile eklenen tum sahte test haberlerini
    (kaynak == 'sentetik-test' olanlari) depodan hemen siler; bir sonraki
    gercek taramayi beklemek istemeyenler icin. Ayni X-Poll-Secret korumasi."""
    if not _poll_secret_dogrula(request):
        return _yetkisiz()

    try:
        depo = redis_store.depo_yukle()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    silinecek_urller = [url for url, o in depo.items() if o.get("kaynak") == "sentetik-test"]
    for url in silinecek_urller:
        del depo[url]

    try:
        redis_store.depo_kaydet(depo)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    return {"ok": True, "silinenSayisi": len(silinecek_urller)}


_ui_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static_ui")
app.mount("/", StaticFiles(directory=_ui_dir, html=True), name="static")
