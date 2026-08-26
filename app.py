"""
Haber Takip Platformu - Vercel icin web surumu (v2: surekli izleme).

Mimari:
  - /api/tara: disaridan (GitHub Actions/cron-job.org gibi bir servisten) her
    15 dakikada bir cagrilir, POLL_SECRET ile korunur. Finviz'deki TUM haber
    turlerini + bloglari PARALEL ceker, daha once gorulmemis olanlari (bir
    seferde en fazla MAX_YENI_HABER_BASINA_TARAMA kadar, kategoriler arasinda
    adil dagitarak, zaman asimini asmamak icin) Gemini ile
    siniflandirir/ozetler/Turkce'ye cevirir, Upstash Redis'teki kalici depoya
    ekler; artik Finviz'de olmayanlari depodan siler.
  - Esik bildirimleri: esikler artik SABIT (ESIKLER: 10 cok onemli / 25 onemli
    / 50 bakmaya deger), kullanici tarafindan degistirilemez. Gece sessiz
    saatlerinde (00:00-06:00, Europe/Istanbul) esik e-postasi gonderilmez,
    haberler sadece birikir. Bir aliciya en fazla MIN_GONDERIM_ARALIGI_DK'da
    (90 dk) bir esik e-postasi gider.
  - /api/sabah-ozeti: HARICI bir cron gorevi tarafindan her sabah 06:00'da
    cagrilir; gece boyunca biriken cok_onemli haberleri toplu gonderir.
  - /api/gun-sonu: HARICI bir cron gorevi tarafindan gunde bir kez (ör.
    23:59) cagrilir; her aliciya gunun ozetini + esige hic ulasmayip
    bildirilmemis kalan haberleri tek e-postada gonderir, sayaclari sifirlar.
  - /api/haberler: depodaki tum haberleri dondurur (istemci bunlari
    tarih/tur/onem/saat araligina gore kendi tarafinda filtreler/siralar).
  - /api/analiz, /api/gun-ozeti: istege bagli AI islemleri; sunucunun kendi
    GEMINI_API_KEY'ini kullanir (artik istemciden anahtar alinmiyor).
  - /api/ayarlar: yalnizca bildirim e-postalarini (liste) okur/yazar.

Statik arayuz (static_ui/) ayni uygulama uzerinden servis edilir.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import email_client
import redis_store
from finviz_scraper import tum_turleri_cek
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
# 20'lik gruplar Gemini'de gercekten 10+ saniye surebiliyor (zaman asimi
# degil, gercekten o kadar uretim suresi istiyor - 20 haberi cevirip
# ozetlemek epey token demek). Daha kucuk gruplar hem daha hizli doner hem
# de cron-job.org'un sabit 30 saniyelik siniri icinde kalma sansini artirir.
TARA_GRUP_BOYUTU = 10
# Bir /api/tara cagrisinda siniflandirilacak azami yeni haber sayisi. Finviz
# taramasi + Gemini siniflandirmasi cron/Vercel zaman asimini asmasin diye
# sinirlandirilir; kapasiteyi asan yeni haberler depoya eklenmez, bu yuzden
# bir sonraki taramada tekrar "yeni" olarak gorulup sirayla islenir.
MAX_YENI_HABER_BASINA_TARAMA = 20
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


def _gemini_anahtari() -> str:
    return os.environ.get("GEMINI_API_KEY", "")


def _istanbul_saati() -> datetime:
    return datetime.now(ISTANBUL_TZ)


def _kategoriler_arasi_adil_sec(ogeler: list[dict], sinir: int) -> list[dict]:
    """Kapasite kadar oge secerken kategoriler arasinda sirayla (round-robin)
    dagitir; boylece en cok haberi olan tek bir kategori (genelde 'ana')
    kapasitenin tamamini tuketip digerlerini disarida birakmaz."""
    kategoriler: dict[str, list[dict]] = {}
    for o in ogeler:
        kategoriler.setdefault(o.get("kategori", "ana"), []).append(o)

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
    return {"ok": True, "haberler": ogeler, "sonTarama": redis_store.son_tarama_yukle()}


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

    return {
        "ok": True,
        "esikler": ESIKLER,
        "gunduzBaslangicSaat": GECE_BITIS_SAAT,
        "aliciDurumlari": alici_durumlari,
        "epostaYapilandirilmisMi": email_client.yapilandirilmis_mi(),
        "sonTarama": redis_store.son_tarama_yukle(),
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

    prompt = analiz_prompt_olustur(baslik, ozet, kaynak_ozeti)
    schema = analiz_schema_olustur()

    try:
        sonuc = gemini_json_iste(prompt, schema, api_key, GEMINI_MODEL)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    return {"ok": True, "analiz": sonuc.get("analiz", "")}


def _gun_ozeti_olustur(depo: dict, api_key: str) -> dict:
    kategorili: dict[str, list[dict]] = {}
    for o in depo.values():
        kategorili.setdefault(o.get("kategori", "ana"), []).append(o)

    prompt = gun_ozeti_prompt_olustur(kategorili)
    schema = gun_ozeti_schema_olustur()
    sonuc = gemini_json_iste(prompt, schema, api_key, GEMINI_MODEL)
    return {"kategoriler": sonuc.get("kategoriler", []), "genelOzet": sonuc.get("genel_ozet", "")}


@app.post("/api/gun-ozeti")
def gun_ozeti():
    api_key = _gemini_anahtari()
    if not api_key:
        return JSONResponse({"ok": False, "hata": "Sunucuda GEMINI_API_KEY tanımlı değil."}, status_code=500)

    try:
        depo = redis_store.depo_yukle()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)
    if not depo:
        return JSONResponse({"ok": False, "hata": "Özetlenecek haber yok."}, status_code=400)

    try:
        return {"ok": True, **_gun_ozeti_olustur(depo, api_key)}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)


def _siniflandir_grup(grup: list[dict], api_key: str) -> str | None:
    """Basarisiz olan ogeleri sahte/bozuk bir siniflandirmayla (cevrilmemis
    baslik, bos ozet) kalici olarak isaretlemek YERINE '_siniflandirilamadi'
    ile bayraklar; bu ogeler _tara_calistir tarafindan depoya HIC YAZILMAZ,
    boylece bir sonraki taramada tekrar 'yeni' sayilip yeniden denenirler."""
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
            baslik = o.get("baslikTr") or o.get("baslik")
            ozet = o.get("ai_ozet", "")
            parcalar.append(f'<li><a href="{url}">{baslik}</a><br><small>{ozet}</small></li>')
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
        etiket = KATEGORI_ETIKET.get(k.get("kategori"), k.get("kategori"))
        parcalar.append(f"<h3>{etiket}</h3><p>{k.get('ozet', '')}</p>")

    if gun_ozeti.get("genelOzet"):
        parcalar.append(f"<h3>Genel Özet</h3><p>{gun_ozeti['genelOzet']}</p>")

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
                baslik = o.get("baslikTr") or o.get("baslik")
                ozet = o.get("ai_ozet", "")
                parcalar.append(f'<li><a href="{url}">{baslik}</a><br><small>{ozet}</small></li>')
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


def _tara_calistir() -> dict:
    """Tek bir tarama dongusu calistirir (hem cron hem manuel tetikleme
    tarafindan paylasilir): Finviz'i ceker, yeni ogeleri siniflandirir,
    depoyu gunceller, esik/e-posta kontrolu yapar. Basarili durumda yanit
    sozlugunu dondurur, hata durumunda TaramaHatasi firlatir."""

    api_key = _gemini_anahtari()
    if not api_key:
        raise TaramaHatasi("Sunucuda GEMINI_API_KEY tanımlı değil.", 500)

    try:
        guncel_ogeler, tarama_hatalari = tum_turleri_cek()
    except Exception as e:  # noqa: BLE001
        raise TaramaHatasi(str(e)) from e

    try:
        depo = redis_store.depo_yukle()
    except Exception as e:  # noqa: BLE001
        raise TaramaHatasi(str(e)) from e

    guncel_url_seti = {o["url"] for o in guncel_ogeler}

    # Ayni haber farkli kategorilerde (ör. Piyasa + Pazar Nabzi) ya da
    # taramalar arasinda degisen takip/yonlendirme parametreleriyle farkli
    # URL'lerle gorunebiliyor. Sadece URL'e gore tekillestirmek bu durumda
    # ayni haberi tekrar tekrar "yeni" sayip bildirimlerde/e-postada birebir
    # tekrarlanmasina yol aciyordu; bu yuzden basliga gore de tekillestirilir.
    mevcut_basliklar = {(o.get("baslik") or "").strip().lower() for o in depo.values() if o.get("baslik")}
    tum_yeni_ogeler: list[dict] = []
    gorulen_yeni_baslik: set[str] = set()
    for o in guncel_ogeler:
        if o["url"] in depo:
            continue
        baslik_norm = (o.get("baslik") or "").strip().lower()
        if baslik_norm and (baslik_norm in mevcut_basliklar or baslik_norm in gorulen_yeni_baslik):
            continue
        if baslik_norm:
            gorulen_yeni_baslik.add(baslik_norm)
        tum_yeni_ogeler.append(o)

    yeni_ogeler = _kategoriler_arasi_adil_sec(tum_yeni_ogeler, MAX_YENI_HABER_BASINA_TARAMA)
    isleme_alinmayan_sayisi = len(tum_yeni_ogeler) - len(yeni_ogeler)

    # ONEMLI: her Finviz kategorisi kendi haberlerini 0'dan baslayan kendi
    # id numaralariyla ayristirir (ör. Piyasa'daki 1. haber id="0", Hisse'deki
    # 1. haber de id="0"). Farkli kategorilerden gelen haberler ayni
    # siniflandirma grubunda bulusunca, Gemini'nin sonucu YANLIS haberle
    # eslesiyordu (ör. bir haberin Turkce basligi baska, alakasiz bir haberin
    # ozeti oluyordu). Gruplamadan HEMEN once, tum sette KESIN benzersiz
    # id'ler atanarak bu carpraz eslesme onlenir.
    for _sira, o in enumerate(yeni_ogeler):
        o["id"] = str(_sira)

    siniflandirma_hatalari: list[str] = []
    for i in range(0, len(yeni_ogeler), TARA_GRUP_BOYUTU):
        grup = yeni_ogeler[i : i + TARA_GRUP_BOYUTU]
        hata = _siniflandir_grup(grup, api_key)
        if hata:
            siniflandirma_hatalari.append(hata)

    # Siniflandirilamayan ogeler depoya HIC YAZILMAZ (cevrilmemis/bos ozetli
    # halde kalici olarak saklanmis olmasinlar diye); URL'leri depoda
    # olmadigi icin bir sonraki taramada tekrar "yeni" sayilip yeniden
    # siniflandirilmaya calisilirlar.
    siniflandirilamayan_sayisi = sum(1 for o in yeni_ogeler if o.get("_siniflandirilamadi"))
    yeni_ogeler = [o for o in yeni_ogeler if not o.get("_siniflandirilamadi")]

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
            o["saat"] = zaman_utc.astimezone(ISTANBUL_TZ).strftime("%H:%M")
        else:
            o["ilkGorulme"] = simdi
        depo[o["url"]] = o

    silinen_urller = [url for url in depo.keys() if url not in guncel_url_seti]
    for url in silinen_urller:
        del depo[url]

    # Her taramada otomatik bakim: gecmiste (ör. eski koddan kalma) olusmus
    # mukerrer ya da cevrilmeden/ozetsiz kalmis kayitlari da temizler; boylece
    # Ayarlar'daki bakim butonlarina elle basmaya normalde gerek kalmaz.
    mukerrer_temizlenen = _depoyu_mukerrerlerden_ayikla(depo)
    basarisiz_temizlenen = _depodan_basarisiz_siniflandirmalari_ayikla(depo)

    try:
        redis_store.depo_kaydet(depo)
        redis_store.son_tarama_kaydet(simdi)
    except Exception as e:  # noqa: BLE001
        raise TaramaHatasi(str(e)) from e

    siniflandirma_hatalari.extend(_esik_takibi_ve_bildirim(yeni_ogeler, depo))

    try:
        redis_store.son_hatalar_kaydet(tarama_hatalari + siniflandirma_hatalari)
    except Exception:  # noqa: BLE001
        pass  # tanilama amacli, taramayi basarisiz saymaya degmez

    return {
        "ok": True,
        "yeniSayisi": len(yeni_ogeler),
        "islenmeyenYeniSayisi": isleme_alinmayan_sayisi,
        "siniflandirilamayanSayisi": siniflandirilamayan_sayisi,
        "silinenSayisi": len(silinen_urller),
        "mukerrerTemizlenenSayisi": mukerrer_temizlenen,
        "basarisizTemizlenenSayisi": basarisiz_temizlenen,
        "toplamDepo": len(depo),
        "taramaHatalari": tarama_hatalari,
        "siniflandirmaHatalari": siniflandirma_hatalari,
    }


@app.post("/api/tara")
def tara(request: Request):
    """Disaridan (cron-job.org gibi) periyodik cagrilir; POLL_SECRET ile korunur."""
    beklenen_sir = os.environ.get("POLL_SECRET")
    if beklenen_sir:
        gelen_sir = request.headers.get("x-poll-secret") or request.query_params.get("secret")
        if gelen_sir != beklenen_sir:
            return JSONResponse({"ok": False, "hata": "Yetkisiz."}, status_code=401)

    try:
        return _tara_calistir()
    except TaramaHatasi as e:
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=e.status_code)


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
def depo_sifirla():
    """TUM haber deposunu sifirlar (butun haberleri kalici olarak siler).
    Kok nedeni duzeltilen bir hata (id carpismasi) yuzunden gecmiste bazi
    haberlerin Turkce basligi/ozeti BASKA, alakasiz bir habere ait olmus
    olabilir; hangi kayitlarin bozuk oldugunu guvenilir sekilde ayirt etmenin
    yolu olmadigindan en temiz cozum sifirdan baslamaktir. Sonraki taramalar
    haberleri normal sekilde yeniden cekip (artik duzeltilmis id mantigiyla)
    dogru siniflandirir. Bildirim ayarlari/e-posta listesi ETKILENMEZ,
    yalnizca haber deposu sifirlanir. Geri alinamaz, bilerek kullanin."""
    try:
        redis_store.depo_kaydet({})
        redis_store.sayaclari_kaydet({})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)
    return {"ok": True}


@app.post("/api/depo-tekillestir")
def depo_tekillestir():
    """Ayarlar'daki 'Mükerrer Haberleri Temizle' butonu tarafindan elle de
    cagrilabilir; ayni islem artik her /api/tara taramasinda otomatik
    calistigi icin normalde gerek kalmaz. Zararsiz/geri alinabilir bir islem
    oldugu icin sir gerektirmez."""
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
def basarisiz_siniflandirmalari_temizle():
    """Ayarlar'daki 'Bozuk Kayıtları Temizle' butonu tarafindan elle de
    cagrilabilir; ayni islem artik her /api/tara taramasinda otomatik
    calistigi icin normalde gerek kalmaz. Zararsiz/geri alinabilir bir islem
    oldugu icin sir gerektirmez."""
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


@app.post("/api/gun-sonu")
def gun_sonu(request: Request):
    """Gunun sonunda, HARICI ikinci bir cron-job.org gorevi tarafindan gunde
    bir kez cagrilir (ornegin 23:45): her aliciya (1) o gunku genel haber
    ozetini VE (2) esigine hic ulasmadigi icin o gune kadar ayrica
    bildirilmemis kalan haberleri TEK bir e-postada gonderir, ardindan o
    alicinin sayaclarini sifirlar (yeni gune temiz baslar). /api/tara ile
    ayni POLL_SECRET korumasini kullanir."""
    beklenen_sir = os.environ.get("POLL_SECRET")
    if beklenen_sir:
        gelen_sir = request.headers.get("x-poll-secret") or request.query_params.get("secret")
        if gelen_sir != beklenen_sir:
            return JSONResponse({"ok": False, "hata": "Yetkisiz."}, status_code=401)

    api_key = _gemini_anahtari()
    if not api_key:
        return JSONResponse({"ok": False, "hata": "Sunucuda GEMINI_API_KEY tanımlı değil."}, status_code=500)

    try:
        depo = redis_store.depo_yukle()
        ayarlar = redis_store.ayarlar_yukle()
        bekleyenler_tum = redis_store.sayaclari_yukle()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    aliciler = [a for a in ayarlar.get("alicilar", []) if a.get("eposta")]
    if not aliciler:
        return {"ok": True, "gonderilenSayisi": 0}

    gun_ozeti = {"kategoriler": [], "genelOzet": ""}
    if depo:
        try:
            gun_ozeti = _gun_ozeti_olustur(depo, api_key)
        except Exception as e:  # noqa: BLE001
            gun_ozeti = {"kategoriler": [], "genelOzet": f"Gün özeti oluşturulamadı: {e}"}

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
    /api/tara ile ayni POLL_SECRET korumasini kullanir."""
    beklenen_sir = os.environ.get("POLL_SECRET")
    if beklenen_sir:
        gelen_sir = request.headers.get("x-poll-secret") or request.query_params.get("secret")
        if gelen_sir != beklenen_sir:
            return JSONResponse({"ok": False, "hata": "Yetkisiz."}, status_code=401)

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
    sekilde, dakikalarca beklemeden test edebilirsiniz. /api/tara ile ayni
    POLL_SECRET korumasini kullanir. Eklenen sahte haberler 'test.local'
    adresli sahte URL'ler kullanir; bir sonraki GERCEK taramada Finviz'de
    bulunamayacaklari icin otomatik olarak depodan silinirler (kendiliginden
    temizlenir, elle silmeye gerek yoktur)."""
    beklenen_sir = os.environ.get("POLL_SECRET")
    if beklenen_sir:
        gelen_sir = request.headers.get("x-poll-secret") or request.query_params.get("secret")
        if gelen_sir != beklenen_sir:
            return JSONResponse({"ok": False, "hata": "Yetkisiz."}, status_code=401)

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
    gercek taramayi beklemek istemeyenler icin. Ayni POLL_SECRET korumasi."""
    beklenen_sir = os.environ.get("POLL_SECRET")
    if beklenen_sir:
        gelen_sir = request.headers.get("x-poll-secret") or request.query_params.get("secret")
        if gelen_sir != beklenen_sir:
            return JSONResponse({"ok": False, "hata": "Yetkisiz."}, status_code=401)

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
