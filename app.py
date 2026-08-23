"""
Haber Takip Platformu - Vercel icin web surumu.

Vercel bu dosyadaki `app` FastAPI nesnesini otomatik olarak algilar ve
Vercel Function olarak calistirir. Statik arayuz (static_ui/) ayni uygulama
uzerinden servis edilir.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from finviz_scraper import finviz_bloglarini_cek, finviz_haberlerini_cek
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


@app.get("/api/haberler")
def haberler(gun: str = "bugun", kategori: str = "ana", tur: str = "haber"):
    try:
        if tur == "blog":
            tum_haberler = finviz_bloglarini_cek()
        else:
            tum_haberler = finviz_haberlerini_cek(kategori=kategori)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": f"Finviz'den veri çekilemedi: {e}"}, status_code=502)

    if gun == "aktif":
        # Finviz'in kendi sayfasinda gosterdigi gibi, tarihe gore filtrelemeden
        # tum cekilen ogeleri (bugun + varsa dunun/gecmisin bir kismi) dondur.
        return {"ok": True, "haberler": tum_haberler, "tarih": "aktif"}

    bugun = datetime.now().date()
    hedef_tarih = bugun if gun == "bugun" else bugun - timedelta(days=1)
    secilenler = [h for h in tum_haberler if h["tarih"] == hedef_tarih.isoformat()]

    return {"ok": True, "haberler": secilenler, "tarih": hedef_tarih.isoformat()}


@app.post("/api/siniflandir")
async def siniflandir(request: Request):
    body = await request.json()
    items = body.get("items") or []
    api_key = (body.get("apiKey") or "").strip()
    model = (body.get("model") or "gemini-flash-lite-latest").strip()

    if not api_key:
        return JSONResponse({"ok": False, "hata": "Gemini API anahtarı gerekli."}, status_code=400)
    if not items:
        return {"ok": True, "results": []}

    prompt = siniflandirma_prompt_olustur(items)
    schema = siniflandirma_schema_olustur()

    try:
        sonuc = gemini_json_iste(prompt, schema, api_key, model)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    return {"ok": True, "results": sonuc.get("results", [])}


@app.post("/api/gun-ozeti")
async def gun_ozeti(request: Request):
    body = await request.json()
    ogeler = body.get("haberler") or []
    api_key = (body.get("apiKey") or "").strip()
    model = (body.get("model") or "gemini-flash-lite-latest").strip()

    if not api_key:
        return JSONResponse({"ok": False, "hata": "Gemini API anahtarı gerekli."}, status_code=400)
    if not ogeler:
        return JSONResponse({"ok": False, "hata": "Özetlenecek haber yok."}, status_code=400)

    prompt = gun_ozeti_prompt_olustur(ogeler)
    schema = gun_ozeti_schema_olustur()

    try:
        sonuc = gemini_json_iste(prompt, schema, api_key, model)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    return {"ok": True, "ozet": sonuc.get("ozet", "")}


@app.post("/api/analiz")
async def analiz(request: Request):
    body = await request.json()
    baslik = (body.get("baslik") or "").strip()
    ozet = (body.get("ozet") or "").strip()
    kaynak_ozeti = (body.get("kaynakOzeti") or "").strip()
    api_key = (body.get("apiKey") or "").strip()
    model = (body.get("model") or "gemini-flash-lite-latest").strip()

    if not api_key:
        return JSONResponse({"ok": False, "hata": "Gemini API anahtarı gerekli."}, status_code=400)
    if not baslik:
        return JSONResponse({"ok": False, "hata": "Analiz edilecek haber bulunamadı."}, status_code=400)

    prompt = analiz_prompt_olustur(baslik, ozet, kaynak_ozeti)
    schema = analiz_schema_olustur()

    try:
        sonuc = gemini_json_iste(prompt, schema, api_key, model)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": str(e)}, status_code=502)

    return {"ok": True, "analiz": sonuc.get("analiz", "")}


_ui_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static_ui")
app.mount("/", StaticFiles(directory=_ui_dir, html=True), name="static")
