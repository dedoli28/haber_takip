"use strict";

/* ===================== Ayarlar ===================== */
const BATCH_SIZE = 20;
const ES_ZAMANLI_ISTEK = 2;
const KAYDEDILEN_KEY = "kaydedilen_haberler_v1";
const TUM_HABER_KATEGORILERI = ["ana", "hisse", "etf", "kripto", "pazar_nabzi"];

const SINIF_SIRA = ["cok_onemli", "onemli", "bakmaya_deger", "onemsiz"];
const SINIF_ETIKET = {
  cok_onemli: "Çok Önemli",
  onemli: "Önemli",
  bakmaya_deger: "Bakmaya Değer",
  onemsiz: "Önemsiz",
};
const KATEGORI_ETIKET = {
  ana: "Piyasa",
  hisse: "Hisse",
  etf: "ETF",
  kripto: "Kripto",
  pazar_nabzi: "Pazar Nabzı",
  blog: "Blog",
};

/* ===================== Helpers ===================== */
function $(id) { return document.getElementById(id); }

function gecikme(ms) { return new Promise((r) => setTimeout(r, ms)); }

function toast(mesaj, tur = "ok") {
  const el = document.createElement("div");
  el.className = `toast toast-${tur}`;
  el.textContent = mesaj;
  $("toastStack").appendChild(el);
  setTimeout(() => el.remove(), 5200);
}

function ustProgres(aktif) {
  const bar = $("topProgress");
  if (aktif) {
    bar.classList.add("is-active");
    bar.style.width = "10%";
  } else {
    bar.classList.remove("is-active");
    bar.style.width = "100%";
    setTimeout(() => { bar.style.width = "0%"; }, 300);
  }
}

function maskele(anahtar) {
  if (anahtar.length <= 8) return "*".repeat(anahtar.length);
  return anahtar.slice(0, 4) + "*".repeat(anahtar.length - 8) + anahtar.slice(-4);
}

/* ===================== API anahtarı (tarayıcıda saklanır) ===================== */
function anahtarAl() { return localStorage.getItem("gemini_api_key") || ""; }
function anahtarKaydet(k) { localStorage.setItem("gemini_api_key", k); }
function modelAl() { return localStorage.getItem("gemini_model") || "gemini-flash-lite-latest"; }
function modelKaydet(m) { localStorage.setItem("gemini_model", m); }

function apiAnahtariGerekliUyar() {
  toast("Önce Ayarlar'dan bir Gemini API anahtarı girin.", "warn");
  $("settingsOverlay").classList.add("is-open");
}

/* ===================== Tema ===================== */
function temaBaslat() {
  const kayitli = localStorage.getItem("tema") || "dark";
  temaUygula(kayitli);
  $("themeToggle").addEventListener("click", () => {
    const simdiki = document.documentElement.getAttribute("data-theme") || "dark";
    temaUygula(simdiki === "dark" ? "light" : "dark");
  });
}
function temaUygula(tema) {
  document.documentElement.setAttribute("data-theme", tema);
  localStorage.setItem("tema", tema);
  $("themeIconMoon").style.display = tema === "dark" ? "block" : "none";
  $("themeIconSun").style.display = tema === "light" ? "block" : "none";
}

/* ===================== Sekmeler ===================== */
function sekmeBaslat(sekmeDegistiCallback) {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("is-active"));
      document.querySelectorAll(".view").forEach((v) => v.classList.remove("is-active"));
      btn.classList.add("is-active");
      $(`view-${btn.dataset.tab}`).classList.add("is-active");
      if (sekmeDegistiCallback) sekmeDegistiCallback(btn.dataset.tab);
    });
  });
}

/* ===================== Ayarlar modal ===================== */
function ayarlarBaslat() {
  $("openSettings").addEventListener("click", () => $("settingsOverlay").classList.add("is-open"));
  $("closeSettings").addEventListener("click", () => $("settingsOverlay").classList.remove("is-open"));
  $("settingsOverlay").addEventListener("click", (e) => {
    if (e.target === $("settingsOverlay")) $("settingsOverlay").classList.remove("is-open");
  });

  $("geminiKeyToggle").addEventListener("click", () => sifreGosterGizle("geminiKeyInput"));

  $("geminiKeySave").addEventListener("click", () => {
    const deger = $("geminiKeyInput").value.trim();
    if (!deger) { toast("Boş anahtar girildi.", "warn"); return; }
    anahtarKaydet(deger);
    $("geminiKeyStatus").textContent = `Kayıtlı: ${maskele(deger)}`;
    $("geminiKeyInput").value = "";
    toast("Gemini API anahtarı bu tarayıcıya kaydedildi.", "ok");
  });

  $("haberModel").addEventListener("change", () => modelKaydet($("haberModel").value));
  $("blogModel").addEventListener("change", () => modelKaydet($("blogModel").value));
}
function sifreGosterGizle(inputId) {
  const el = $(inputId);
  el.type = el.type === "password" ? "text" : "password";
}

function durumYukle() {
  const anahtar = anahtarAl();
  if (anahtar) {
    $("geminiKeyStatus").textContent = `Kayıtlı: ${maskele(anahtar)}`;
  } else {
    $("geminiKeyStatus").textContent = "Tanımlı değil";
    toast("Başlamak için Ayarlar'dan bir Gemini API anahtarı girmen gerekiyor.", "warn");
    $("settingsOverlay").classList.add("is-open");
  }
  const model = modelAl();
  $("haberModel").value = model;
  $("blogModel").value = model;
}

/* ===================== Kaydedilenler (localStorage) ===================== */
function kaydedilenleriYukle() {
  try {
    const ham = localStorage.getItem(KAYDEDILEN_KEY);
    return ham ? JSON.parse(ham) : [];
  } catch (e) {
    return [];
  }
}
function kaydedilenleriKaydet(liste) {
  localStorage.setItem(KAYDEDILEN_KEY, JSON.stringify(liste));
}
function kayitliMi(url) {
  return kaydedilenleriYukle().some((o) => o.url === url);
}
function kaydedilenEkleGuncelle(oge) {
  const liste = kaydedilenleriYukle();
  const idx = liste.findIndex((o) => o.url === oge.url);
  const kayit = { ...oge, kaydedilmeTarihi: new Date().toISOString() };
  if (idx >= 0) liste[idx] = kayit; else liste.unshift(kayit);
  kaydedilenleriKaydet(liste);
}
function kaydedilenKaldir(url) {
  kaydedilenleriKaydet(kaydedilenleriYukle().filter((o) => o.url !== url));
}

/* ===================== Markdown indirme (ortak) ===================== */
function indirMarkdown(ogeler, baslikMetni, dosyaOnEki) {
  if (ogeler.length === 0) return;
  const bugun = new Date().toISOString().slice(0, 10);
  const satirlar = [`# ${baslikMetni} - ${bugun}`, ""];

  SINIF_SIRA.forEach((sinif) => {
    const grup = ogeler.filter((h) => h.sinif === sinif);
    if (grup.length === 0) return;
    satirlar.push(`## ${SINIF_ETIKET[sinif]} (${grup.length})`);
    satirlar.push("");
    grup.forEach((h) => {
      const baslikGoster = h.baslikTr || h.baslik;
      const ozetStr = h.ai_ozet ? ` — _${h.ai_ozet}_` : "";
      const analizStr = h.analiz ? `\n  - **Analiz:** ${h.analiz}` : "";
      satirlar.push(`- **[${h.saat}] [${baslikGoster}](${h.url})** (${h.kaynak})${ozetStr}${analizStr}`);
    });
    satirlar.push("");
  });

  const blob = new Blob([satirlar.join("\n")], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${dosyaOnEki}_${bugun}.md`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  toast("Markdown dosyası indirildi.", "ok");
}

/* ===================== Detay modal (haber/blog/kayıtlı ortak) ===================== */
let suankiOge = null;

function kaydetButonuGuncelle() {
  const btn = $("detailKaydetBtn");
  const etiket = $("detailKaydetLabel");
  if (!suankiOge) return;
  if (kayitliMi(suankiOge.url)) {
    btn.classList.add("is-saved");
    etiket.textContent = "Kaydedildi ✓ (Kaldır)";
  } else {
    btn.classList.remove("is-saved");
    etiket.textContent = "Kaydet";
  }
}

function detayGoster(h) {
  suankiOge = h;
  $("detailBadge").textContent = SINIF_ETIKET[h.sinif] || h.sinif;
  $("detailBadge").className = `badge badge-${h.sinif}`;
  const katEtiket = h.kategori && KATEGORI_ETIKET[h.kategori] ? ` · ${KATEGORI_ETIKET[h.kategori]}` : "";
  $("detailMeta").textContent = `${h.saat} · ${h.kaynak}${katEtiket}`;
  $("detailTitle").textContent = h.baslikTr || h.baslik;

  const origEl = $("detailOriginalTitle");
  if (h.baslikTr && h.baslik && h.baslikTr !== h.baslik) {
    origEl.textContent = `Orijinal: ${h.baslik}`;
    origEl.style.display = "block";
  } else {
    origEl.style.display = "none";
  }

  $("detailSummary").textContent = h.ai_ozet || "Bu içerik için henüz bir özet yok.";
  $("detailOpenLink").href = h.url;

  const analizText = $("detailAnalizText");
  const analizBtn = $("detailAnalizBtn");
  analizBtn.disabled = false;
  if (h.analiz) {
    analizText.textContent = h.analiz;
    analizText.style.display = "block";
    analizBtn.textContent = "Tekrar Analiz Et";
  } else {
    analizText.style.display = "none";
    analizText.textContent = "";
    analizBtn.textContent = "Analiz Et";
  }

  kaydetButonuGuncelle();
  $("detailOverlay").classList.add("is-open");
}

function detayModalBaslat(kayitliPanelYenile) {
  $("closeDetail").addEventListener("click", () => $("detailOverlay").classList.remove("is-open"));
  $("detailOverlay").addEventListener("click", (e) => {
    if (e.target === $("detailOverlay")) $("detailOverlay").classList.remove("is-open");
  });

  $("detailKaydetBtn").addEventListener("click", () => {
    if (!suankiOge) return;
    if (kayitliMi(suankiOge.url)) {
      kaydedilenKaldir(suankiOge.url);
      toast("Kaydedilenlerden kaldırıldı.", "ok");
    } else {
      kaydedilenEkleGuncelle(suankiOge);
      toast("Kaydedildi. \"Kaydedilenler\" sekmesinden ulaşabilirsin.", "ok");
    }
    kaydetButonuGuncelle();
    if (kayitliPanelYenile) kayitliPanelYenile();
  });

  $("detailAnalizBtn").addEventListener("click", async () => {
    if (!suankiOge) return;
    const apiKey = anahtarAl();
    if (!apiKey) { apiAnahtariGerekliUyar(); return; }

    const btn = $("detailAnalizBtn");
    const text = $("detailAnalizText");
    const oncekiMetin = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Analiz ediliyor...";
    text.style.display = "block";
    text.textContent = "Analiz hazırlanıyor...";

    try {
      const resp = await fetch("/api/analiz", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          baslik: suankiOge.baslikTr || suankiOge.baslik,
          ozet: suankiOge.ai_ozet,
          kaynakOzeti: suankiOge.kaynakOzeti,
          apiKey,
          model: modelAl(),
        }),
      });
      const yanit = await resp.json();
      if (yanit.ok) {
        suankiOge.analiz = yanit.analiz;
        text.textContent = yanit.analiz;
        btn.textContent = "Tekrar Analiz Et";
        if (kayitliMi(suankiOge.url)) {
          kaydedilenEkleGuncelle(suankiOge);
          if (kayitliPanelYenile) kayitliPanelYenile();
        }
      } else {
        text.textContent = yanit.hata || "Analiz alınamadı.";
        btn.textContent = oncekiMetin;
        toast(yanit.hata || "Analiz alınamadı.", "error");
      }
    } catch (e) {
      text.textContent = "Beklenmeyen hata: " + e;
      btn.textContent = oncekiMetin;
    } finally {
      btn.disabled = false;
    }
  });
}

/* ===================== Gün özeti modal ===================== */
function gunOzetiModalBaslat() {
  $("closeDaySummary").addEventListener("click", () => $("daySummaryOverlay").classList.remove("is-open"));
  $("daySummaryOverlay").addEventListener("click", (e) => {
    if (e.target === $("daySummaryOverlay")) $("daySummaryOverlay").classList.remove("is-open");
  });
}

async function gunuOzetle(ogeler, model) {
  const apiKey = anahtarAl();
  if (!apiKey) { apiAnahtariGerekliUyar(); return; }
  if (ogeler.length === 0) return;

  $("daySummaryText").textContent = "Hazırlanıyor...";
  $("daySummaryOverlay").classList.add("is-open");

  try {
    const resp = await fetch("/api/gun-ozeti", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        haberler: ogeler.map((h) => ({ sinif: h.sinif, baslik: h.baslikTr || h.baslik, ozet: h.ai_ozet })),
        apiKey,
        model,
      }),
    });
    const yanit = await resp.json();
    if (yanit.ok) {
      $("daySummaryText").textContent = yanit.ozet;
    } else {
      $("daySummaryText").textContent = yanit.hata || "Özet alınamadı.";
      toast(yanit.hata || "Özet alınamadı.", "error");
    }
  } catch (e) {
    $("daySummaryText").textContent = "Beklenmeyen hata: " + e;
  }
}

/* ===================== Ortak kart/liste yardımcıları ===================== */
function kartOlustur(h, index, tiklaninca) {
  const kart = document.createElement("div");
  kart.className = `item-card c-${h.sinif}`;
  kart.style.animationDelay = `${Math.min(index * 25, 300)}ms`;

  const serit = document.createElement("div");
  serit.className = "item-stripe";
  kart.appendChild(serit);

  const govde = document.createElement("div");
  govde.className = "item-body";

  const metaRow = document.createElement("div");
  metaRow.className = "item-meta-row";
  const rozet = document.createElement("span");
  rozet.className = "badge";
  rozet.textContent = SINIF_ETIKET[h.sinif] || h.sinif;
  metaRow.appendChild(rozet);

  if (h.kategori && KATEGORI_ETIKET[h.kategori]) {
    const katTag = document.createElement("span");
    katTag.className = "kategori-tag";
    katTag.textContent = KATEGORI_ETIKET[h.kategori];
    metaRow.appendChild(katTag);
  }

  const meta = document.createElement("span");
  meta.className = "meta-text";
  meta.textContent = `${h.saat} · ${h.kaynak}`;
  metaRow.appendChild(meta);

  const baslik = document.createElement("p");
  baslik.className = "item-title";
  baslik.textContent = h.baslikTr || h.baslik;

  govde.appendChild(metaRow);
  govde.appendChild(baslik);

  if (h.ai_ozet) {
    const ozet = document.createElement("p");
    ozet.className = "item-summary";
    ozet.textContent = h.ai_ozet;
    govde.appendChild(ozet);
  }

  kart.appendChild(govde);
  kart.addEventListener("click", () => tiklaninca(h));
  return kart;
}

function bosDurumCiz(konteynerId, mesaj) {
  const kutu = $(konteynerId);
  kutu.innerHTML = "";
  const bos = document.createElement("div");
  bos.className = "empty-state";
  bos.innerHTML = `<svg width="40" height="40" viewBox="0 0 24 24" fill="none"><path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5v-13Z" stroke="currentColor" stroke-width="1.4"/><path d="M7.5 8.5h9M7.5 12h9M7.5 15.5h5.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg><p>${mesaj}</p>`;
  kutu.appendChild(bos);
}

function iskeletCiz(konteynerId, adet = 4) {
  const kutu = $(konteynerId);
  kutu.innerHTML = "";
  for (let i = 0; i < adet; i++) {
    const s = document.createElement("div");
    s.className = "skeleton-card";
    kutu.appendChild(s);
  }
}

/* ===================== Ortak: bir grup öğeyi Gemini ile sınıflandır ===================== */
async function topluSiniflandir(tumler, apiKey, model, ilerlemeCB) {
  const sonucMap = new Map(
    tumler.map((h) => [h.id, { ...h, sinif: "bakmaya_deger", ai_ozet: "", baslikTr: h.baslik }])
  );
  const gruplar = [];
  for (let i = 0; i < tumler.length; i += BATCH_SIZE) gruplar.push(tumler.slice(i, i + BATCH_SIZE));

  let tamamlanan = 0;
  let ilkHata = null;
  let siraNo = 0;

  async function isci(isciIndex) {
    await gecikme(isciIndex * 500);
    while (siraNo < gruplar.length) {
      const grup = gruplar[siraNo++];
      try {
        const resp = await fetch("/api/siniflandir", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            items: grup.map((h) => ({ id: h.id, saat: h.saat, baslik: h.baslik, kaynakOzeti: h.kaynakOzeti })),
            apiKey,
            model,
          }),
        });
        const yanit = await resp.json();
        if (yanit.ok) {
          yanit.results.forEach((r) => {
            const mevcut = sonucMap.get(r.id);
            if (mevcut) {
              mevcut.sinif = r.sinif;
              mevcut.ai_ozet = r.ozet;
              if (r.baslik_tr) mevcut.baslikTr = r.baslik_tr;
            }
          });
        } else if (!ilkHata) {
          ilkHata = yanit.hata || "Sınıflandırma başarısız.";
        }
      } catch (e) {
        if (!ilkHata) ilkHata = String(e);
      }
      tamamlanan += grup.length;
      if (ilerlemeCB) ilerlemeCB(tamamlanan, tumler.length);
      await gecikme(300);
    }
  }

  const isciSayisi = Math.min(ES_ZAMANLI_ISTEK, gruplar.length);
  await Promise.all(Array.from({ length: isciSayisi }, (_v, i) => isci(i)));

  return { sonuclar: Array.from(sonucMap.values()), ilkHata };
}

/* ===================== Panel (Haberler / Bloglar ortak mantığı) ===================== */
function panelOlustur(cfg) {
  const state = { ogeler: [], filtre: "", kategoriFiltre: "", arama: "", calisiyor: false };

  const el = {
    model: $(`${cfg.prefix}Model`),
    kategori: cfg.kategoriId ? $(cfg.kategoriId) : null,
    gun: $(`${cfg.prefix}Gun`),
    btn: $(`${cfg.prefix}Btn`),
    tumBtn: cfg.tumBtnId ? $(cfg.tumBtnId) : null,
    kaydetBtn: $(`${cfg.prefix}KaydetBtn`),
    gunOzetBtn: $(`${cfg.prefix}GunOzetBtn`),
    durum: $(`${cfg.prefix}Durum`),
    progress: $(`${cfg.prefix}Progress`),
    chips: document.querySelectorAll(`#${cfg.prefix}Chips .chip`),
    kategoriChips: cfg.kategoriChipsId ? document.querySelectorAll(`#${cfg.kategoriChipsId} .chip`) : null,
    arama: cfg.aramaId ? $(cfg.aramaId) : null,
    liste: `${cfg.prefix}Liste`,
  };

  function ciz() {
    const kutu = $(el.liste);
    kutu.innerHTML = "";
    let gorulen = state.ogeler;
    if (state.filtre) gorulen = gorulen.filter((h) => h.sinif === state.filtre);
    if (state.kategoriFiltre) gorulen = gorulen.filter((h) => h.kategori === state.kategoriFiltre);
    if (state.arama) {
      const q = state.arama.toLowerCase();
      gorulen = gorulen.filter(
        (h) =>
          (h.baslikTr || "").toLowerCase().includes(q) ||
          (h.baslik || "").toLowerCase().includes(q) ||
          (h.ai_ozet || "").toLowerCase().includes(q)
      );
    }

    if (gorulen.length === 0) {
      bosDurumCiz(el.liste, state.ogeler.length ? "Seçilen filtre/aramayla eşleşen içerik yok." : cfg.varsayilanBosMesaj);
      return;
    }
    gorulen.forEach((h, i) => kutu.appendChild(kartOlustur(h, i, detayGoster)));
  }

  function chipSayilariGuncelle() {
    const sayilar = {};
    state.ogeler.forEach((h) => { sayilar[h.sinif] = (sayilar[h.sinif] || 0) + 1; });
    el.chips.forEach((chip) => {
      const sinif = chip.dataset.sinif;
      if (!sinif) { chip.textContent = `Tümü (${state.ogeler.length})`; return; }
      chip.textContent = `${SINIF_ETIKET[sinif]} (${sayilar[sinif] || 0})`;
    });

    if (el.kategoriChips) {
      const katSayilar = {};
      state.ogeler.forEach((h) => { if (h.kategori) katSayilar[h.kategori] = (katSayilar[h.kategori] || 0) + 1; });
      el.kategoriChips.forEach((chip) => {
        const kat = chip.dataset.kategori;
        if (!kat) { chip.textContent = `Tüm Türler (${state.ogeler.length})`; return; }
        chip.textContent = `${KATEGORI_ETIKET[kat] || kat} (${katSayilar[kat] || 0})`;
      });
    }
  }

  function chipBaslat() {
    el.chips.forEach((chip) => {
      chip.addEventListener("click", () => {
        state.filtre = chip.dataset.sinif;
        el.chips.forEach((c) => c.classList.remove("is-active"));
        chip.classList.add("is-active");
        ciz();
      });
    });

    if (el.kategoriChips) {
      el.kategoriChips.forEach((chip) => {
        chip.addEventListener("click", () => {
          state.kategoriFiltre = chip.dataset.kategori;
          el.kategoriChips.forEach((c) => c.classList.remove("is-active"));
          chip.classList.add("is-active");
          ciz();
        });
      });
    }

    if (el.arama) {
      el.arama.addEventListener("input", () => {
        state.arama = el.arama.value.trim();
        ciz();
      });
    }
  }

  function sonuclariUygula(sonuclar) {
    state.ogeler = sonuclar;
    chipSayilariGuncelle();
    ciz();
    el.kaydetBtn.disabled = false;
    if (el.gunOzetBtn) el.gunOzetBtn.disabled = false;
  }

  async function getir() {
    if (state.calisiyor) return;

    const apiKey = anahtarAl();
    if (!apiKey) { apiAnahtariGerekliUyar(); return; }

    state.calisiyor = true;
    state.ogeler = [];
    iskeletCiz(el.liste, 5);
    el.kaydetBtn.disabled = true;
    if (el.gunOzetBtn) el.gunOzetBtn.disabled = true;
    el.btn.disabled = true;
    if (el.tumBtn) el.tumBtn.disabled = true;
    const orijinalBtnMetni = el.btn.textContent.trim();
    el.btn.textContent = "Çalışıyor...";
    el.progress.style.width = "0%";
    el.durum.textContent = "Finviz'den içerikler çekiliyor...";
    ustProgres(true);

    const model = el.model.value;
    const gun = el.gun.value;
    const kategori = el.kategori ? el.kategori.value : "ana";
    const kategoriEtiketi = cfg.tur === "blog" ? "blog" : kategori;

    try {
      const parametreler = new URLSearchParams({ gun, tur: cfg.tur, kategori });
      const cekResp = await fetch(`/api/haberler?${parametreler.toString()}`);
      const cekSonuc = await cekResp.json();

      if (!cekSonuc.ok) {
        toast(cekSonuc.hata || "İçerikler alınamadı.", "error");
        el.durum.textContent = "Hata oluştu.";
        return;
      }

      const tumler = cekSonuc.haberler;
      tumler.forEach((h) => { h.kategori = kategoriEtiketi; });

      if (tumler.length === 0) {
        el.durum.textContent = gun === "aktif" ? "Şu an gösterilecek içerik bulunamadı." : `${cekSonuc.tarih} tarihine ait içerik bulunamadı.`;
        bosDurumCiz(el.liste, "Seçilen filtre için içerik bulunamadı.");
        return;
      }

      el.durum.textContent = `${tumler.length} içerik bulundu. Sınıflandırılıyor...`;

      const { sonuclar, ilkHata } = await topluSiniflandir(tumler, apiKey, model, (tamam, top) => {
        el.progress.style.width = `${Math.round((tamam / top) * 100)}%`;
        el.durum.textContent = `Sınıflandırılıyor... ${tamam}/${top}`;
      });

      sonuclariUygula(sonuclar);

      if (ilkHata) {
        el.durum.textContent = "Tamamlandı (bazı gruplar başarısız oldu).";
        toast(ilkHata, "warn");
      } else {
        el.durum.textContent = `Tamamlandı. Toplam ${tumler.length} içerik (${new Date().toLocaleTimeString("tr-TR")}).`;
      }
    } catch (e) {
      toast("Beklenmeyen hata: " + e, "error");
      el.durum.textContent = "Hata oluştu.";
    } finally {
      state.calisiyor = false;
      el.btn.disabled = false;
      if (el.tumBtn) el.tumBtn.disabled = false;
      el.btn.textContent = orijinalBtnMetni;
      ustProgres(false);
    }
  }

  async function tumKategorileriGetir() {
    if (state.calisiyor) return;

    const apiKey = anahtarAl();
    if (!apiKey) { apiAnahtariGerekliUyar(); return; }

    state.calisiyor = true;
    state.ogeler = [];
    iskeletCiz(el.liste, 6);
    el.kaydetBtn.disabled = true;
    if (el.gunOzetBtn) el.gunOzetBtn.disabled = true;
    el.btn.disabled = true;
    el.tumBtn.disabled = true;
    const oncekiTumMetni = el.tumBtn.textContent.trim();
    el.tumBtn.textContent = "Çalışıyor...";
    el.progress.style.width = "0%";
    el.durum.textContent = "Tüm kategoriler Finviz'den çekiliyor...";
    ustProgres(true);

    const model = el.model.value;
    const gun = el.gun.value;

    try {
      const birlesik = [];
      const gorulenUrl = new Set();

      for (const kat of TUM_HABER_KATEGORILERI) {
        try {
          const parametreler = new URLSearchParams({ gun, tur: "haber", kategori: kat });
          const resp = await fetch(`/api/haberler?${parametreler.toString()}`);
          const sonuc = await resp.json();
          if (sonuc.ok) {
            sonuc.haberler.forEach((h) => {
              if (!gorulenUrl.has(h.url)) {
                gorulenUrl.add(h.url);
                h.kategori = kat;
                birlesik.push(h);
              }
            });
          }
        } catch (e) {
          // bu kategori atlanır, diğerlerine devam edilir
        }
      }

      // farklı kategorilerden gelen ogelerin id'leri carpisabilir; birlestirilmis
      // liste icin yeniden, benzersiz id ata.
      birlesik.forEach((h, i) => { h.id = String(i); });

      if (birlesik.length === 0) {
        el.durum.textContent = "Hiçbir kategoride içerik bulunamadı.";
        bosDurumCiz(el.liste, "İçerik bulunamadı.");
        return;
      }

      el.durum.textContent = `${birlesik.length} içerik bulundu (tüm kategoriler). Sınıflandırılıyor...`;

      const { sonuclar, ilkHata } = await topluSiniflandir(birlesik, apiKey, model, (tamam, top) => {
        el.progress.style.width = `${Math.round((tamam / top) * 100)}%`;
        el.durum.textContent = `Sınıflandırılıyor... ${tamam}/${top}`;
      });

      sonuclariUygula(sonuclar);

      if (ilkHata) {
        el.durum.textContent = "Tamamlandı (bazı gruplar başarısız oldu).";
        toast(ilkHata, "warn");
      } else {
        el.durum.textContent = `Tamamlandı. Toplam ${birlesik.length} içerik, tüm kategoriler (${new Date().toLocaleTimeString("tr-TR")}).`;
      }
    } catch (e) {
      toast("Beklenmeyen hata: " + e, "error");
      el.durum.textContent = "Hata oluştu.";
    } finally {
      state.calisiyor = false;
      el.btn.disabled = false;
      el.tumBtn.disabled = false;
      el.tumBtn.textContent = oncekiTumMetni;
      ustProgres(false);
    }
  }

  chipBaslat();
  el.btn.addEventListener("click", getir);
  if (el.tumBtn) el.tumBtn.addEventListener("click", tumKategorileriGetir);
  el.kaydetBtn.addEventListener("click", () => {
    indirMarkdown(state.ogeler, cfg.tur === "blog" ? "Finviz Blog Yazıları" : "Finviz Borsa Haberleri", cfg.tur);
  });
  if (el.gunOzetBtn) {
    el.gunOzetBtn.addEventListener("click", () => gunuOzetle(state.ogeler, el.model.value));
  }
}

/* ===================== Kaydedilenler paneli ===================== */
function kayitliPaneliOlustur() {
  const chips = document.querySelectorAll("#kayitliChips .chip");
  const kategoriChips = document.querySelectorAll("#kayitliKategoriChips .chip");
  const aramaInput = $("kayitliArama");
  let filtre = "";
  let kategoriFiltre = "";
  let arama = "";

  function ciz() {
    const liste = kaydedilenleriYukle();
    const kutu = $("kayitliListe");
    kutu.innerHTML = "";

    const sayilar = {};
    liste.forEach((h) => { sayilar[h.sinif] = (sayilar[h.sinif] || 0) + 1; });
    chips.forEach((chip) => {
      const sinif = chip.dataset.sinif;
      if (!sinif) { chip.textContent = `Tümü (${liste.length})`; return; }
      chip.textContent = `${SINIF_ETIKET[sinif]} (${sayilar[sinif] || 0})`;
    });

    const katSayilar = {};
    liste.forEach((h) => { if (h.kategori) katSayilar[h.kategori] = (katSayilar[h.kategori] || 0) + 1; });
    kategoriChips.forEach((chip) => {
      const kat = chip.dataset.kategori;
      if (!kat) { chip.textContent = `Tüm Türler (${liste.length})`; return; }
      chip.textContent = `${KATEGORI_ETIKET[kat] || kat} (${katSayilar[kat] || 0})`;
    });

    $("kayitliKaydetBtn").disabled = liste.length === 0;

    let gorulen = liste;
    if (filtre) gorulen = gorulen.filter((h) => h.sinif === filtre);
    if (kategoriFiltre) gorulen = gorulen.filter((h) => h.kategori === kategoriFiltre);
    if (arama) {
      const q = arama.toLowerCase();
      gorulen = gorulen.filter(
        (h) =>
          (h.baslikTr || "").toLowerCase().includes(q) ||
          (h.baslik || "").toLowerCase().includes(q) ||
          (h.ai_ozet || "").toLowerCase().includes(q)
      );
    }

    if (gorulen.length === 0) {
      bosDurumCiz(
        "kayitliListe",
        liste.length
          ? "Seçilen filtre/aramayla eşleşen kayıt yok."
          : 'Henüz kaydedilen bir haber yok.<br>Bir habere tıklayıp açılan pencereden "Kaydet" diyebilirsin.'
      );
      return;
    }
    gorulen.forEach((h, i) => kutu.appendChild(kartOlustur(h, i, detayGoster)));
  }

  chips.forEach((chip) => {
    chip.addEventListener("click", () => {
      filtre = chip.dataset.sinif;
      chips.forEach((c) => c.classList.remove("is-active"));
      chip.classList.add("is-active");
      ciz();
    });
  });

  kategoriChips.forEach((chip) => {
    chip.addEventListener("click", () => {
      kategoriFiltre = chip.dataset.kategori;
      kategoriChips.forEach((c) => c.classList.remove("is-active"));
      chip.classList.add("is-active");
      ciz();
    });
  });

  if (aramaInput) {
    aramaInput.addEventListener("input", () => {
      arama = aramaInput.value.trim();
      ciz();
    });
  }

  $("kayitliTemizleBtn").addEventListener("click", () => {
    if (kaydedilenleriYukle().length === 0) return;
    if (!confirm("Tüm kaydedilen haberler silinsin mi?")) return;
    kaydedilenleriKaydet([]);
    ciz();
    toast("Tüm kayıtlar silindi.", "ok");
  });

  $("kayitliKaydetBtn").addEventListener("click", () => {
    indirMarkdown(kaydedilenleriYukle(), "Kaydedilen Haberler", "kaydedilenler");
  });

  return ciz;
}

/* ===================== Başlat ===================== */
function baslat() {
  temaBaslat();
  ayarlarBaslat();
  gunOzetiModalBaslat();

  const kayitliYenile = kayitliPaneliOlustur();
  detayModalBaslat(kayitliYenile);

  sekmeBaslat((sekme) => {
    if (sekme === "kaydedilenler") kayitliYenile();
  });

  panelOlustur({
    tur: "haber",
    prefix: "haber",
    kategoriId: "haberKategori",
    kategoriChipsId: "haberKategoriChips",
    aramaId: "haberArama",
    tumBtnId: "haberTumBtn",
    varsayilanBosMesaj: 'Henüz haber yok.<br>"Haberleri Getir ve Sınıflandır" butonuna tıkla.',
  });

  panelOlustur({
    tur: "blog",
    prefix: "blog",
    kategoriId: null,
    aramaId: "blogArama",
    varsayilanBosMesaj: 'Henüz blog yazısı yok.<br>"Blogları Getir ve Sınıflandır" butonuna tıkla.',
  });

  kayitliYenile();
  durumYukle();
}

baslat();
