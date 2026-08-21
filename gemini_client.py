"""Gemini API ile JSON tabanlı sınıflandırma/özetleme isteği. Vercel'de
sunucu tarafında (api rotasında) çalışır; API anahtarı istemciden gelir,
hiçbir yerde saklanmaz."""

from __future__ import annotations

import json
import time

import requests

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SINIF_SIRA = ["cok_onemli", "onemli", "bakmaya_deger", "onemsiz"]


def gemini_json_iste(prompt: str, response_schema: dict, api_key: str, model: str) -> dict:
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

    for deneme in range(2):
        try:
            resp = requests.post(url, params={"key": api_key}, json=body, timeout=25)
        except Exception as e:  # noqa: BLE001
            son_hata = e
            time.sleep(1.5 * (deneme + 1))
            continue

        if resp.status_code == 429:
            if "PerDay" in resp.text:
                raise RuntimeError(
                    f"Gemini ücretsiz günlük kota sınırı aşıldı ({model}). "
                    "Kota genelde 24 saatte sıfırlanır; farklı bir API anahtarı/model de deneyebilirsiniz."
                )
            son_hata = RuntimeError(f"Gemini istek limitine ulaşıldı (429): {resp.text[:200]}")
            time.sleep(2 * (deneme + 1))
            continue

        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            data = resp.json()
            metin = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(metin)
        except Exception as e:  # noqa: BLE001
            son_hata = e
            time.sleep(1.5 * (deneme + 1))

    raise RuntimeError(str(son_hata) if son_hata else "Bilinmeyen hata")


def siniflandirma_prompt_olustur(ogeler: list[dict]) -> str:
    girdi_listesi = []
    for o in ogeler:
        satir = f"id={o['id']} | saat={o.get('saat', '')} | baslik={o['baslik']}"
        kaynak_ozeti = o.get("kaynakOzeti", "")
        if kaynak_ozeti and kaynak_ozeti != o["baslik"]:
            satir += f" | kaynak_ozeti={kaynak_ozeti}"
        girdi_listesi.append(satir)

    return f"""Sen deneyimli bir borsa/finans analistisin. Aşağıda finviz.com
sitesinden çekilmiş haber başlıklarının bir listesi var. Her haber için iki
şey yap:

1) ABD ve küresel borsalar / piyasalar açısından taşıdığı ÖNEM DERECESİNE
göre aşağıdaki 4 sınıftan birine ata:
- "cok_onemli": Piyasaları geniş çapta hareket ettirebilecek haberler
  (Fed/merkez bankası kararları, faiz, enflasyon/istihdam gibi kritik makro
  veriler, büyük jeopolitik/savaş gelişmeleri, büyük şirket iflası/skandalı,
  büyük M&A anlaşmaları, endeksleri etkileyen ani şok haberler).
- "onemli": Belirli bir sektörü veya büyük şirketleri etkileyen, takip
  edilmesi gereken haberler (büyük şirket bilançoları, guidance
  değişiklikleri, önemli düzenleme/politika değişiklikleri, emtia/petrol
  fiyat hareketleri).
- "bakmaya_deger": Doğrudan piyasa hareketi yaratması beklenmeyen ama ilgili
  yatırımcının göz atabileceği haberler (küçük/orta ölçekli şirket haberleri,
  analiz/yorum yazıları, arka plan haberleri).
- "onemsiz": Borsayla ilgisi zayıf veya yok denecek kadar az olan haberler
  (magazin, spor, genel gündem, doğrudan finansal etkisi olmayan haberler).

2) Haberi 1-2 cümleyle TÜRKÇE olarak özetle ("ozet" alanı): haberin ne
hakkında olduğunu, kim/ne/neden açısından kısaca açıkla. Bu bir gerekçe
değil, haberin kendisinin kısa bir özeti olmalı.

Haber listesi:
{chr(10).join(girdi_listesi)}

Her haber için id, sinif (yukarıdaki 4 değerden biri) ve ozet alanlarını
içeren JSON dön. Sadece JSON dön."""


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
                        "sinif": {"type": "STRING", "enum": SINIF_SIRA},
                        "ozet": {"type": "STRING"},
                    },
                    "required": ["id", "sinif", "ozet"],
                },
            }
        },
        "required": ["results"],
    }
