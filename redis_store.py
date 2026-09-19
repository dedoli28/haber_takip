"""Upstash Redis (REST API) uzerinden kalici veri deposu.

Vercel'in serverless dosya sistemi kalici olmadigi icin, haberlerin/blog
yazilarinin onbellegi, bildirim ayarlari ve esik sayaclari burada, gercek
kalici bir depoda (Upstash Redis - ucretsiz katmani var) tutulur.

Gerekli ortam degiskenleri:
  UPSTASH_REDIS_REST_URL
  UPSTASH_REDIS_REST_TOKEN
"""

from __future__ import annotations

import json
import os

import requests

_BASE_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "").rstrip("/")
_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

REPO_KEY = "htp:repo"
AYARLAR_KEY = "htp:ayarlar"
SAYAC_KEY = "htp:sayaclar"
SON_TARAMA_KEY = "htp:son_tarama"
SON_HATALAR_KEY = "htp:son_hatalar"
SON_GONDERIM_KEY = "htp:son_gonderim"
BEKLEYEN_SINIFLANDIRMA_KEY = "htp:bekleyen_siniflandirma"
SON_SINIFLANDIRMA_KEY = "htp:son_siniflandirma"
# Onceden uretilmis "Gunun Ozeti": {kategoriler, genelOzet, olusturulmaZamani,
# haberSayisi}. "Gunu Ozetle" butonu Gemini'yi cagirmaz, sadece bunu okur.
GUN_OZETI_KEY = "htp:gun_ozeti"

SINIF_ESIK_LISTESI = ["cok_onemli", "onemli", "bakmaya_deger"]

# Esikler artik sabit (app.py'deki ESIKLER); kullanicinin ayarlayabildigi
# tek sey bildirim e-postalari listesi.
# alicilar: [{"eposta": str}, ...]
VARSAYILAN_AYARLAR = {"alicilar": []}

# Esik sayaclari aslinda alici basina "bekleyen" (henuz o aliciya e-postayla
# bildirilmemis) haberlerin URL listesidir: {eposta: {sinif: [url, ...]}}.
# Her alici kendi esigine ulastikca yalnizca kendi sayaci sifirlanir.
VARSAYILAN_SAYAC: dict = {}


def yapilandirilmis_mi() -> bool:
    return bool(_BASE_URL and _TOKEN)


def _basliklar() -> dict:
    return {"Authorization": f"Bearer {_TOKEN}"}


def _get_ham(key: str) -> str | None:
    if not yapilandirilmis_mi():
        raise RuntimeError("Upstash Redis yapılandırılmamış (UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN eksik).")
    resp = requests.get(f"{_BASE_URL}/get/{key}", headers=_basliklar(), timeout=10)
    resp.raise_for_status()
    return resp.json().get("result")


def _set_ham(key: str, deger: str) -> None:
    if not yapilandirilmis_mi():
        raise RuntimeError("Upstash Redis yapılandırılmamış (UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN eksik).")
    resp = requests.post(f"{_BASE_URL}/set/{key}", headers=_basliklar(), data=deger.encode("utf-8"), timeout=10)
    resp.raise_for_status()


def _get_json(key: str, varsayilan):
    ham = _get_ham(key)
    if ham is None:
        return varsayilan
    try:
        return json.loads(ham)
    except Exception:
        return varsayilan


def _set_json(key: str, deger) -> None:
    _set_ham(key, json.dumps(deger, ensure_ascii=False))


def depo_yukle() -> dict:
    """URL -> haber sozlugu seklinde tum onbellegi dondurur."""
    return _get_json(REPO_KEY, {})


def depo_kaydet(depo: dict) -> None:
    _set_json(REPO_KEY, depo)


def ayarlar_yukle() -> dict:
    ayarlar = _get_json(AYARLAR_KEY, None)
    if not ayarlar:
        return dict(VARSAYILAN_AYARLAR)
    return {**VARSAYILAN_AYARLAR, **ayarlar}


def ayarlar_kaydet(ayarlar: dict) -> None:
    _set_json(AYARLAR_KEY, ayarlar)


def sayaclari_yukle() -> dict:
    sayaclar = _get_json(SAYAC_KEY, None)
    if not sayaclar:
        return dict(VARSAYILAN_SAYAC)
    return {**VARSAYILAN_SAYAC, **sayaclar}


def sayaclari_kaydet(sayaclar: dict) -> None:
    _set_json(SAYAC_KEY, sayaclar)


def son_tarama_yukle() -> str | None:
    return _get_ham(SON_TARAMA_KEY)


def son_tarama_kaydet(iso_zaman: str) -> None:
    _set_ham(SON_TARAMA_KEY, iso_zaman)


def son_hatalar_yukle() -> list[str]:
    return _get_json(SON_HATALAR_KEY, [])


def son_hatalar_kaydet(hatalar: list[str]) -> None:
    _set_json(SON_HATALAR_KEY, hatalar)


def son_gonderim_yukle() -> dict:
    """Alici basina en son esik e-postasi gonderim zamani: {eposta: iso_zaman}.
    Ardisik gonderimler arasinda asgari bekleme suresini uygulamak icin
    kullanilir (spam hissi vermesin diye)."""
    return _get_json(SON_GONDERIM_KEY, {})


def son_gonderim_kaydet(son_gonderim: dict) -> None:
    _set_json(SON_GONDERIM_KEY, son_gonderim)


def bekleyen_siniflandirma_yukle() -> dict:
    """Finviz'den cekilmis ama henuz Gemini ile siniflandirilmamis haberlerin
    URL -> ham oge sozlugu (bir bekleme kuyrugu). Tarama (hizli, /api/haber-cek)
    ve siniflandirma (yavas, Gemini'ye bagli, /api/haber-siniflandir) ayri ayri
    tetiklenebilsin ve birbirini zaman asimina ugratmasin diye ayrilmistir."""
    return _get_json(BEKLEYEN_SINIFLANDIRMA_KEY, {})


def bekleyen_siniflandirma_kaydet(kuyruk: dict) -> None:
    _set_json(BEKLEYEN_SINIFLANDIRMA_KEY, kuyruk)


def son_siniflandirma_yukle() -> str | None:
    return _get_ham(SON_SINIFLANDIRMA_KEY)


def son_siniflandirma_kaydet(iso_zaman: str) -> None:
    _set_ham(SON_SINIFLANDIRMA_KEY, iso_zaman)


def gun_ozeti_yukle() -> dict | None:
    """Son hazir gun ozetini dondurur; hic uretilmemisse None."""
    ozet = _get_json(GUN_OZETI_KEY, None)
    return ozet if isinstance(ozet, dict) else None


def gun_ozeti_kaydet(ozet: dict) -> None:
    _set_json(GUN_OZETI_KEY, ozet)


def _pipeline(komutlar: list[list]) -> list[dict]:
    """Upstash REST /pipeline: birden fazla Redis komutunu TEK istekte calistirir.
    Her komut icin {"result": ...} ya da {"error": ...} iceren bir liste doner."""
    if not yapilandirilmis_mi():
        raise RuntimeError("Upstash Redis yapılandırılmamış (UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN eksik).")
    resp = requests.post(f"{_BASE_URL}/pipeline", headers=_basliklar(), json=komutlar, timeout=10)
    resp.raise_for_status()
    return resp.json()


def istek_siniri_asildi_mi(anahtar: str, azami_istek: int, pencere_sn: int) -> bool:
    """Dagitik (tum sunucu ornekleri arasinda paylasilan) sabit pencereli istek
    siniri: 'anahtar' icin sayaci INCR ile artirir; sayac ilk kez olustugunda
    pencere_sn saniyelik EXPIRE baslatir. Sayac azami_istek'i asarsa True
    doner (istek reddedilmeli).

    EXPIRE ... NX, INCR ile ayni pipeline'da calisir ve yalnizca anahtarin
    henuz bir omru yoksa uygulanir: bir onceki cagrida EXPIRE hic
    calisamamis olsa bile (ör. ag kesintisi) sayac kalici olarak takili
    kalip o kullaniciyi sonsuza dek engellemez, sonraki cagrida omur atanir."""
    yanit = _pipeline([["INCR", anahtar], ["EXPIRE", anahtar, pencere_sn, "NX"]])
    sayac = int(yanit[0]["result"])
    if "error" in yanit[1]:
        # Sunucu EXPIRE'in NX secenegini desteklemiyorsa (eski Redis surumu):
        # yalnizca sayac ilk olustugunda klasik EXPIRE ile omur ata.
        if sayac == 1:
            resp = requests.post(
                f"{_BASE_URL}/expire/{anahtar}/{pencere_sn}", headers=_basliklar(), timeout=10
            )
            resp.raise_for_status()
    return sayac > azami_istek
