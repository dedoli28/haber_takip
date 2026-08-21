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

from finviz_scraper import finviz_haberlerini_cek
from gemini_client import gemini_json_iste, siniflandirma_prompt_olustur, siniflandirma_schema_olustur

app = FastAPI(title="Haber Takip Platformu")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/haberler")
def haberler(gun: str = "bugun"):
    try:
        tum_haberler = finviz_haberlerini_cek()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "hata": f"Finviz'den veri çekilemedi: {e}"}, status_code=502)

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


_ui_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static_ui")
app.mount("/", StaticFiles(directory=_ui_dir, html=True), name="static")
