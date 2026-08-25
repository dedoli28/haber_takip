"""
Haber Takip Platformu - Vercel icin web surumu (v2: surekli izleme).

Mimari:
  - /api/tara: disaridan (cron-job.org gibi ucretsiz bir servisten) periyodik
    olarak cagrilir, POLL_SECRET ile korunur. Finviz'deki TUM haber turlerini
    + bloglari ceker, daha once gorulmemis olanlari (bir seferde en fazla
    MAX_YENI_HABER_BASINA_TARAMA kadar, kategoriler arasinda adil dagitarak,
    zaman asimini asmamak icin) Gemini ile siniflandirir/ozetler/Turkce'ye
    cevirir, Upstash Redis'teki kalici depoya ekler; artik Finviz'de
    olmayanlari depodan siler. Esik asilinca (3 cok onemli / 5 onemli /
    15 bakmaya deger) e-posta gonderir.
  - /api/haberler: depodaki tum haberleri dondurur (istemci bunlari
    tarih/tur/onem'e gore kendi tarafinda filtreler).
  - /api/analiz, /api/gun-ozeti: istege bagli AI islemleri; sunucunun kendi
    GEMINI_API_KEY'ini kullanir (artik istemciden anahtar alinmiyor).
  - /api/ayarlar: bildirim e-postalarini okur/yazar.

Statik arayuz (static_ui/) ayni uygulama uzerinden servis edilir.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import email_client
import redis_store
from finviz_scraper import tum_turleri_cek
from gemini_client import (
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
TARA_GRUP_BOYUTU = 20
# Bir /api/tara cagrisinda siniflandirilacak azami yeni haber sayisi. Finviz
# taramasi + Gemini siniflandirmasi cron/Vercel zaman asimini asmasin diye
# sinirlandirilir; kapasiteyi asan yeni haberler depoya eklenmez, bu yuzden
# bir sonraki taramada tekrar "yeni" olarak gorulup sirayla islenir.
MAX_YENI_HABER_BASINA_TARAMA = 60
ESIKLER = {"cok_onemli": 3, "onemli": 5, "bakmaya_deger": 15}
SINIF_ETIKET_TR = {"cok_onemli": "Çok Önemli", "onemli": "Önemli", "bakmaya_deger": "Bakmaya Değer"}


def _gemini_anahtari() -> str:
    return os.environ.get("GEMINI_API_KEY", "")


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
    """E-posta bildirim mekanizmasini teshis etmek icin: eslik sayaclarinin
    (bekleyen, henuz mail atilmamis haber sayisi) o anki durumunu, esikleri
    ve e-posta yapilandirmasinin sunucuda tanimli olup olmadigini gosterir."""
    try:
        bekleyenler = redis_store.sayaclari_yukle()
        ayarlar = redis_store.ayarlar_yukle()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    return {
        "ok": True,
        "esikler": ESIKLER,
        "bekleyenSayilar": {s: len(u) for s, u in bekleyenler.items()},
        "bildirimEpostalari": ayarlar.get("bildirim_epostalari", []),
        "epostaYapilandirilmisMi": email_client.yapilandirilmis_mi(),
        "sonTarama": redis_store.son_tarama_yukle(),
    }


@app.get("/api/ayarlar")
def ayarlar_getir():
    try:
        ayarlar = redis_store.ayarlar_yukle()
        ayarlar["sonTestEpostasi"] = redis_store.son_test_epostasi_yukle()
        return {"ok": True, "ayarlar": ayarlar}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)


@app.post("/api/ayarlar")
async def ayarlar_guncelle(request: Request):
    body = await request.json()
    epostalar = [e.strip() for e in (body.get("bildirim_epostalari") or []) if e.strip()]
    try:
        redis_store.ayarlar_kaydet({"bildirim_epostalari": epostalar})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)
    return {"ok": True, "ayarlar": {"bildirim_epostalari": epostalar}}


TEST_EPOSTA_BEKLEME_SANIYE = 10 * 60


@app.post("/api/test-eposta")
def test_eposta():
    """Ayarlar modalindaki 'Deneme E-postası Gönder' butonu tarafindan
    cagrilir; kotuye kullanimi/spam'i onlemek icin son gonderimden itibaren
    TEST_EPOSTA_BEKLEME_SANIYE gecmeden tekrar gonderilemez (Redis'te kalici
    olarak takip edilir, tarayici/sekme yenilense bile gecerlidir)."""
    try:
        ayarlar = redis_store.ayarlar_yukle()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    aliciler = ayarlar.get("bildirim_epostalari", [])
    if not aliciler:
        return JSONResponse({"ok": False, "hata": "Önce Ayarlar'a en az bir bildirim e-postası ekleyin."}, status_code=400)

    if not email_client.yapilandirilmis_mi():
        return JSONResponse({"ok": False, "hata": "Sunucuda GMAIL_ADDRESS / GMAIL_APP_PASSWORD tanımlı değil."}, status_code=500)

    try:
        son = redis_store.son_test_epostasi_yukle()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    if son:
        try:
            gecen_saniye = (datetime.now(timezone.utc) - datetime.fromisoformat(son)).total_seconds()
        except ValueError:
            gecen_saniye = TEST_EPOSTA_BEKLEME_SANIYE
        if gecen_saniye < TEST_EPOSTA_BEKLEME_SANIYE:
            kalan_saniye = int(TEST_EPOSTA_BEKLEME_SANIYE - gecen_saniye)
            kalan_dk = kalan_saniye // 60 + 1
            return JSONResponse(
                {
                    "ok": False,
                    "hata": f"Çok sık deneme e-postası istendi, {kalan_dk} dakika sonra tekrar deneyin.",
                    "kalanSaniye": kalan_saniye,
                },
                status_code=429,
            )

    html = (
        "<h2>Haber Takip Platformu</h2>"
        "<p>Bu bir deneme e-postasıdır. Bu e-postayı görüyorsanız bildirim ayarlarınız doğru çalışıyor.</p>"
    )
    try:
        email_client.eposta_gonder(aliciler, "Haber Takip Platformu - Deneme E-postası", html)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    simdi = datetime.now(timezone.utc).isoformat()
    try:
        redis_store.son_test_epostasi_kaydet(simdi)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    return {"ok": True, "gonderilenSayisi": len(aliciler), "sonTestEpostasi": simdi}


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

    kategorili: dict[str, list[dict]] = {}
    for o in depo.values():
        kategorili.setdefault(o.get("kategori", "ana"), []).append(o)

    prompt = gun_ozeti_prompt_olustur(kategorili)
    schema = gun_ozeti_schema_olustur()

    try:
        sonuc = gemini_json_iste(prompt, schema, api_key, GEMINI_MODEL)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    return {"ok": True, "kategoriler": sonuc.get("kategoriler", []), "genelOzet": sonuc.get("genel_ozet", "")}


def _siniflandir_grup(grup: list[dict], api_key: str) -> str | None:
    prompt = siniflandirma_prompt_olustur(grup)
    schema = siniflandirma_schema_olustur()
    try:
        sonuc = gemini_json_iste(prompt, schema, api_key, GEMINI_MODEL)
    except Exception as e:  # noqa: BLE001
        for o in grup:
            o["sinif"] = "bakmaya_deger"
            o["baslikTr"] = o["baslik"]
            o["ai_ozet"] = ""
        return f"{len(grup)} haberlik grup sınıflandırılamadı: {e}"

    sonuc_map = {r["id"]: r for r in sonuc.get("results", [])}
    for o in grup:
        r = sonuc_map.get(o["id"])
        if r:
            o["sinif"] = r.get("sinif", "bakmaya_deger")
            o["baslikTr"] = r.get("baslik_tr") or o["baslik"]
            o["ai_ozet"] = r.get("ozet", "")
        else:
            o["sinif"] = "bakmaya_deger"
            o["baslikTr"] = o["baslik"]
            o["ai_ozet"] = ""
    return None


EPOSTA_SINIF_BASINA_AZAMI = 20


def _esik_email_html(tetiklenen: dict[str, list[str]], depo: dict) -> str:
    """E-posta govdesini olusturur. Bekleyen liste cok uzunsa (ör. gecmis bir
    gonderim hatasi yuzunden birikmisse) e-postanin devasa buyup zaman
    asimina/gonderim hatasina yol acmamasi icin sinif basina yalnizca en son
    EPOSTA_SINIF_BASINA_AZAMI haber gosterilir, kalani ozetlenir."""
    parcalar = ["<h2>Haber Takip Platformu</h2><p>Aşağıdaki önem eşikleri aşıldı:</p>"]
    for sinif, urller in tetiklenen.items():
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
    tum_yeni_ogeler = [o for o in guncel_ogeler if o["url"] not in depo]
    yeni_ogeler = _kategoriler_arasi_adil_sec(tum_yeni_ogeler, MAX_YENI_HABER_BASINA_TARAMA)
    isleme_alinmayan_sayisi = len(tum_yeni_ogeler) - len(yeni_ogeler)

    siniflandirma_hatalari: list[str] = []
    for i in range(0, len(yeni_ogeler), TARA_GRUP_BOYUTU):
        grup = yeni_ogeler[i : i + TARA_GRUP_BOYUTU]
        hata = _siniflandir_grup(grup, api_key)
        if hata:
            siniflandirma_hatalari.append(hata)

    simdi = datetime.now(timezone.utc).isoformat()
    for o in yeni_ogeler:
        o["ilkGorulme"] = simdi
        depo[o["url"]] = o

    silinen_urller = [url for url in depo.keys() if url not in guncel_url_seti]
    for url in silinen_urller:
        del depo[url]

    try:
        redis_store.depo_kaydet(depo)
        redis_store.son_tarama_kaydet(simdi)
    except Exception as e:  # noqa: BLE001
        raise TaramaHatasi(str(e)) from e

    # esik takibi + e-posta bildirimi
    bekleyenler = redis_store.sayaclari_yukle()
    for o in yeni_ogeler:
        s = o.get("sinif")
        if s in bekleyenler:
            bekleyenler[s].append(o["url"])

    tetiklenen = {s: bekleyenler[s] for s, esik in ESIKLER.items() if len(bekleyenler.get(s, [])) >= esik}

    if tetiklenen:
        ayarlar = redis_store.ayarlar_yukle()
        aliciler = ayarlar.get("bildirim_epostalari", [])
        if aliciler and email_client.yapilandirilmis_mi():
            try:
                html = _esik_email_html(tetiklenen, depo)
                email_client.eposta_gonder(aliciler, "Haber Takip Platformu - Yeni Önemli Haberler", html)
                for s in tetiklenen:
                    bekleyenler[s] = []
            except Exception as e:  # noqa: BLE001
                siniflandirma_hatalari.append(f"E-posta gönderilemedi: {e}")
        elif not aliciler:
            pass  # bildirim e-postasi tanimli degil, sessizce atla
        elif not email_client.yapilandirilmis_mi():
            siniflandirma_hatalari.append("GMAIL_ADDRESS / GMAIL_APP_PASSWORD tanımlı değil, e-posta gönderilemedi.")

    try:
        redis_store.sayaclari_kaydet(bekleyenler)
    except Exception as e:  # noqa: BLE001
        siniflandirma_hatalari.append(str(e))

    return {
        "ok": True,
        "yeniSayisi": len(yeni_ogeler),
        "islenmeyenYeniSayisi": isleme_alinmayan_sayisi,
        "silinenSayisi": len(silinen_urller),
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


_ui_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static_ui")
app.mount("/", StaticFiles(directory=_ui_dir, html=True), name="static")
