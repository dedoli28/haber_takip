"""Gemini API ile JSON tabanlı sınıflandırma/özetleme isteği. Vercel'de
sunucu tarafında (api rotasında) çalışır; API anahtarı sunucunun ortam
değişkeninden (GEMINI_API_KEY) gelir."""

from __future__ import annotations

import json
import time

import requests

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SINIF_SIRA = ["cok_onemli", "onemli", "bakmaya_deger", "onemsiz"]

# Haber kaynağı ülkeleri. Eski kayıtlarda "ulke" alanı yoktur; onlar ABD (Finviz)
# haberleridir.
ULKE_ETIKET = {"US": "ABD", "TR": "Türkiye", "DE": "Almanya", "CN": "Çin"}
VARSAYILAN_ULKE = "US"


def _ulke_kodu(oge: dict) -> str:
    return (oge.get("ulke") or VARSAYILAN_ULKE).upper()


def gemini_json_iste(
    prompt: str,
    response_schema: dict,
    api_key: str,
    model: str,
    timeout: float = 10,
    deneme_sayisi: int = 2,
) -> dict:
    """Gemini'den şemaya uygun JSON ister. 'timeout' her denemenin saniye
    cinsinden zaman aşımı, 'deneme_sayisi' geçici hatalarda toplam deneme
    sayısıdır. Varsayılanlar (10 sn, 2 deneme) sınıflandırma akışının
    cron-job.org'un 30 sn'lik sınırında kalması için ayarlıdır; uzun süren
    tek seferlik istekler (gün özeti) daha uzun timeout + tek deneme ister."""
    deneme_sayisi = max(1, int(deneme_sayisi))
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": response_schema,
            "temperature": 0.1,
        },
    }
    url = GEMINI_ENDPOINT.format(model=model)
    son_hata: Exception | None = None

    def gizle(metin: str) -> str:
        # requests'in baglanti hatalari istek URL'ini ("...?key=<API_KEY>")
        # icerir ve bu metin API yanitiyla istemciye donebilir; anahtari sakla.
        return metin.replace(api_key, "***") if api_key else metin

    def bekle(deneme: int, katsayi: float = 1) -> None:
        # Son denemeden sonra beklemenin anlami yok (hata hemen firlatilir).
        if deneme < deneme_sayisi - 1:
            time.sleep(katsayi * (deneme + 1))

    # 429 (istek limiti) ve 5xx (gecici sunucu yogunlugu/erisilemezligi, ör.
    # "high demand" 503) gecici sayilir ve tekrar denenir; digerleri kalicidir.
    # Siniflandirma cron-job.org'un sabit 30 saniyelik siniri icinde kalmasi
    # gereken /api/haber-siniflandir uzerinden (tek seferde tek grup)
    # tetikleniyor; bu yuzden varsayilan timeout dar tutulur - en kotu
    # ihtimalle (2 deneme + 1s bekleme) ~21 saniye, geri kalan pay redis/JSON
    # islemleri icin birakilir.
    GECICI_HATA_KODLARI = {500, 502, 503, 504}

    for deneme in range(deneme_sayisi):
        try:
            resp = requests.post(url, params={"key": api_key}, json=body, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            son_hata = RuntimeError(gizle(str(e)))
            bekle(deneme)
            continue

        if resp.status_code == 429:
            if "PerDay" in resp.text:
                raise RuntimeError(
                    f"Gemini ücretsiz günlük kota sınırı aşıldı ({model}). "
                    "Kota genelde 24 saatte sıfırlanır; farklı bir API anahtarı/model de deneyebilirsiniz."
                )
            son_hata = RuntimeError(f"Gemini istek limitine ulaşıldı (429): {resp.text[:200]}")
            bekle(deneme)
            continue

        if resp.status_code in GECICI_HATA_KODLARI:
            son_hata = RuntimeError(f"Gemini geçici olarak yoğun/erişilemez ({resp.status_code}): {resp.text[:200]}")
            bekle(deneme)
            continue

        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            data = resp.json()
            metin = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(metin)
        except Exception as e:  # noqa: BLE001
            son_hata = RuntimeError(gizle(str(e)))
            bekle(deneme, 1.5)

    raise RuntimeError(str(son_hata) if son_hata else "Bilinmeyen hata")


def siniflandirma_prompt_olustur(ogeler: list[dict]) -> str:
    girdi_listesi = []
    for o in ogeler:
        satir = f"id={o['id']} | ulke={_ulke_kodu(o)} | saat={o.get('saat', '')} | baslik={o['baslik']}"
        kaynak_ozeti = o.get("kaynakOzeti", "")
        if kaynak_ozeti and kaynak_ozeti != o["baslik"]:
            satir += f" | kaynak_ozeti={kaynak_ozeti}"
        girdi_listesi.append(satir)

    return f"""Sen deneyimli bir borsa/finans analistisin ve iyi bir
çevirmensin. Aşağıda ABD, Türkiye, Almanya ve Çin kaynaklarından çekilmiş
finans/ekonomi haber başlıklarının bir listesi var. Başlıklar İngilizce,
Türkçe ya da Almanca olabilir. Her haberin geldiği ülke "ulke" alanında
belirtilmiştir (US=ABD, TR=Türkiye, DE=Almanya, CN=Çin). Her haber için üç
şey yap:

1) Başlığı doğal, akıcı TÜRKÇE'ye çevir ("baslik_tr" alanı): kelimesi
kelimesine değil, bir Türkçe haber başlığı gibi doğal dursun. Başlık zaten
Türkçeyse anlamını değiştirmeden düzgün bir haber başlığı olarak bırak.
Şirket/kişi adları, kurum adları ve ticker sembollerini olduğu gibi bırak.

2) Haberin ÖNEM DERECESİNİ, haberin ilgili olduğu ülkenin piyasaları (ör.
ABD için Wall Street ve dolar, Türkiye için Borsa İstanbul ve TL, Almanya
için DAX ve euro bölgesi, Çin için Şanghay/Shenzhen/Hong Kong borsaları ve
yuan) ile küresel piyasalar üzerindeki etkisi açısından değerlendirip
aşağıdaki 4 sınıftan birine ata. Önemi yalnızca ABD piyasası açısından
ölçme: bir ülkenin kendi piyasası için büyük etki taşıyan haber (ör. TCMB
faiz kararı, DAX'ı sarsan bir gelişme, Çin'in bir teşvik paketi), küresel
etkisi sınırlı olsa bile yüksek sınıf alabilir.
- "cok_onemli": Piyasaları geniş çapta hareket ettirebilecek haberler
  (Fed/ECB/TCMB/PBoC gibi merkez bankası kararları, faiz, enflasyon/istihdam
  gibi kritik makro veriler, büyük jeopolitik/savaş gelişmeleri, büyük şirket
  iflası/skandalı, büyük M&A anlaşmaları, endeksleri etkileyen ani şok
  haberler).
- "onemli": Belirli bir sektörü veya büyük şirketleri etkileyen, takip
  edilmesi gereken haberler (büyük şirket bilançoları, guidance
  değişiklikleri, önemli düzenleme/politika değişiklikleri, emtia/petrol
  fiyat hareketleri).
- "bakmaya_deger": Doğrudan piyasa hareketi yaratması beklenmeyen ama ilgili
  yatırımcının göz atabileceği haberler (küçük/orta ölçekli şirket haberleri,
  analiz/yorum yazıları, arka plan haberleri).
- "onemsiz": Borsayla ilgisi zayıf veya yok denecek kadar az olan haberler
  (magazin, spor, genel gündem, doğrudan finansal etkisi olmayan haberler).

3) Haberi 1-2 cümleyle TÜRKÇE olarak özetle ("ozet" alanı): haberin ne
hakkında olduğunu, kim/ne/neden açısından kısaca açıkla. Bu bir gerekçe
değil, haberin kendisinin kısa bir özeti olmalı.

Haber listesi:
{chr(10).join(girdi_listesi)}

Her haber için id, baslik_tr, sinif (yukarıdaki 4 değerden biri) ve ozet
alanlarını içeren JSON dön. Sadece JSON dön."""


def siniflandirma_schema_olustur() -> dict:
    return {
        "type": "OBJECT",
        "properties": {
            "results": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "id": {"type": "STRING"},
                        "baslik_tr": {"type": "STRING"},
                        "sinif": {"type": "STRING", "enum": SINIF_SIRA},
                        "ozet": {"type": "STRING"},
                    },
                    "required": ["id", "baslik_tr", "sinif", "ozet"],
                },
            }
        },
        "required": ["results"],
    }


KATEGORI_ETIKET = {
    "ana": "Piyasa Haberleri",
    "hisse": "Hisse Senedi Haberleri",
    "etf": "ETF Haberleri",
    "kripto": "Kripto Haberleri",
    "pazar_nabzi": "Pazar Nabzı",
    "blog": "Bloglar",
}


def gun_ozeti_prompt_olustur(kategorili_ogeler: dict[str, list[dict]]) -> str:
    bolumler = []
    for kategori, ogeler in kategorili_ogeler.items():
        if not ogeler:
            continue
        etiket = KATEGORI_ETIKET.get(kategori, kategori)
        satirlar = []
        for o in ogeler:
            # Depoda Türkçe başlık "baslikTr", AI özeti "ai_ozet" alanlarında
            # tutulur; eski adlar (baslik_tr/ozet) geriye dönük uyumluluk için.
            baslik = o.get("baslikTr") or o.get("baslik_tr") or o.get("baslik", "")
            ozet = o.get("ai_ozet") or o.get("ozet", "")
            ulke = ULKE_ETIKET.get(_ulke_kodu(o), _ulke_kodu(o))
            satirlar.append(f"  [{o.get('sinif', '')}] [{ulke}] {baslik} — {ozet}")
        bolumler.append(f'Kategori anahtari: "{kategori}" ({etiket})\n' + "\n".join(satirlar))

    return f"""Sen deneyimli bir borsa/finans analistisin. Aşağıda bugünün
sınıflandırılmış ve özetlenmiş haberleri, haber TÜRÜNE göre gruplanmış halde
listelenmiş. Haberler ABD, Türkiye, Almanya ve Çin kaynaklarından gelir;
her satırda ilk köşeli parantez önem derecesini, ikincisi haberin ülkesini
gösterir.

Şunu yap:
1) Her haber türü için ayrı bir özet üret ("kategoriler" listesi). Her öge
   {{kategori, ozet}} olsun: "kategori" alanına yukarıda verilen kategori
   anahtarını (ör. "ana", "hisse") aynen yaz; "ozet" alanına o türdeki
   haberlere dayanarak 2-4 cümlelik TÜRKÇE bir özet yaz, özellikle
   "cok_onemli" ve "onemli" olanlara odaklan; haberler birden fazla ülkeden
   geliyorsa hangi gelişmenin hangi ülkeye ait olduğunu belirt. Hiç haberi
   olmayan türler için öge üretme.
2) En sonda tüm türleri kapsayan, günün genel piyasa görünümünü özetleyen
   4-6 cümlelik TÜRKÇE bir "genel_ozet" yaz: günün baskın temasını/yönünü,
   öne çıkan sektörleri, genel risk iştahını vurgula; ülkeler/bölgeler
   arasında belirgin bir ayrışma varsa (ör. ABD yükselirken Çin düşüyor)
   bunu da ekle.

Haberler:
{chr(10).join(bolumler)}

Sadece "kategoriler" (liste) ve "genel_ozet" alanlarını içeren JSON dön."""


def gun_ozeti_schema_olustur() -> dict:
    return {
        "type": "OBJECT",
        "properties": {
            "kategoriler": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "kategori": {"type": "STRING"},
                        "ozet": {"type": "STRING"},
                    },
                    "required": ["kategori", "ozet"],
                },
            },
            "genel_ozet": {"type": "STRING"},
        },
        "required": ["kategoriler", "genel_ozet"],
    }


def analiz_prompt_olustur(baslik: str, ozet: str, kaynak_ozeti: str = "") -> str:
    ek = f"\nEk bilgi: {kaynak_ozeti}" if kaynak_ozeti and kaynak_ozeti != baslik else ""
    return f"""Sen deneyimli bir borsa/finans analistisin. Aşağıdaki haberi
oku ve bu haberin şirketler/hisseler üzerindeki OLASI etkisini analiz et:
hangi şirketler, hisseler ya da sektörler bu haberden dolayı değer
kazanabilir, hangileri değer kaybedebilir? Mümkünse somut şirket/hisse adı
ver; haber belirli bir şirketten bahsetmiyorsa hangi sektörlerin
etkilenebileceğini yaz.

Kısa ve öz ol (en fazla 4-5 cümle ya da madde). Bu, haberin içeriğine dayalı
genel bir değerlendirmedir; analizin sonunda kısaca bunun bir yatırım
tavsiyesi olmadığını belirt.

Haber başlığı: {baslik}
Özet: {ozet}{ek}

Sadece "analiz" alanını içeren JSON dön."""


def analiz_schema_olustur() -> dict:
    return {
        "type": "OBJECT",
        "properties": {"analiz": {"type": "STRING"}},
        "required": ["analiz"],
    }
