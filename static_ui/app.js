"use strict";

/* ===================== Ayarlar ===================== */
const BATCH_SIZE = 20;
const ES_ZAMANLI_ISTEK = 2;

const SINIF_SIRA = ["cok_onemli", "onemli", "bakmaya_deger", "onemsiz"];
const SINIF_ETIKET = {
  cok_onemli: "Çok Önemli",
  onemli: "Önemli",
  bakmaya_deger: "Bakmaya Değer",
  onemsiz: "Önemsiz",
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
function sekmeBaslat() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("is-active"));
      document.querySelectorAll(".view").forEach((v) => v.classList.remove("is-active"));
      btn.classList.add("is-active");
      $(`view-${btn.dataset.tab}`).classList.add("is-active");
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

/* ===================== Detay modal (haber/blog ortak) ===================== */
function detayGoster(h) {
  $("detailBadge").textContent = SINIF_ETIKET[h.sinif] || h.sinif;
  $("detailBadge").className = `badge badge-${h.sinif}`;
  $("detailMeta").textContent = `${h.saat} · ${h.kaynak}`;
  $("detailTitle").textContent = h.baslik;
  $("detailSummary").textContent = h.ai_ozet || "Bu içerik için henüz bir özet yok.";
  $("detailOpenLink").href = h.url;
  $("detailOverlay").classList.add("is-open");
}

function detayModalBaslat() {
  $("closeDetail").addEventListener("click", () => $("detailOverlay").classList.remove("is-open"));
  $("detailOverlay").addEventListener("click", (e) => {
    if (e.target === $("detailOverlay")) $("detailOverlay").classList.remove("is-open");
  });
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
  const meta = document.createElement("span");
  meta.className = "meta-text";
  meta.textContent = `${h.saat} · ${h.kaynak}`;
  metaRow.appendChild(rozet);
  metaRow.appendChild(meta);

  const baslik = document.createElement("p");
  baslik.className = "item-title";
  baslik.textContent = h.baslik;

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

/* ===================== Panel (Haberler / Bloglar ortak mantığı) ===================== */
function panelOlustur(cfg) {
  // cfg: { tur, prefix, bosMesaj, varsayilanBosMesaj }
  const state = { ogeler: [], filtre: "", calisiyor: false };

  const el = {
    model: $(`${cfg.prefix}Model`),
    kategori: cfg.kategoriId ? $(cfg.kategoriId) : null,
    gun: $(`${cfg.prefix}Gun`),
    btn: $(`${cfg.prefix}Btn`),
    kaydetBtn: $(`${cfg.prefix}KaydetBtn`),
    durum: $(`${cfg.prefix}Durum`),
    progress: $(`${cfg.prefix}Progress`),
    chips: document.querySelectorAll(`#${cfg.prefix}Chips .chip`),
    liste: `${cfg.prefix}Liste`,
  };

  function ciz() {
    const kutu = $(el.liste);
    kutu.innerHTML = "";
    const gorulen = state.filtre ? state.ogeler.filter((h) => h.sinif === state.filtre) : state.ogeler;

    if (gorulen.length === 0) {
      bosDurumCiz(el.liste, state.ogeler.length ? "Seçilen filtreyle eşleşen içerik yok." : cfg.varsayilanBosMesaj);
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
  }

  async function getir() {
    if (state.calisiyor) return;

    const apiKey = anahtarAl();
    if (!apiKey) {
      toast("Önce Ayarlar'dan bir Gemini API anahtarı girin.", "warn");
      $("settingsOverlay").classList.add("is-open");
      return;
    }

    state.calisiyor = true;
    state.ogeler = [];
    iskeletCiz(el.liste, 5);
    el.kaydetBtn.disabled = true;
    el.btn.disabled = true;
    const orijinalBtnMetni = el.btn.textContent.trim();
    el.btn.textContent = "Çalışıyor...";
    el.progress.style.width = "0%";
    el.durum.textContent = "Finviz'den içerikler çekiliyor...";
    ustProgres(true);

    const model = el.model.value;
    const gun = el.gun.value;
    const kategori = el.kategori ? el.kategori.value : "ana";

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
      if (tumler.length === 0) {
        el.durum.textContent = gun === "aktif" ? "Şu an gösterilecek içerik bulunamadı." : `${cekSonuc.tarih} tarihine ait içerik bulunamadı.`;
        bosDurumCiz(el.liste, "Seçilen filtre için içerik bulunamadı.");
        return;
      }

      el.durum.textContent = `${tumler.length} içerik bulundu. Sınıflandırılıyor...`;

      const sonucMap = new Map(tumler.map((h) => [h.id, { ...h, sinif: "bakmaya_deger", ai_ozet: "" }]));
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
                if (mevcut) { mevcut.sinif = r.sinif; mevcut.ai_ozet = r.ozet; }
              });
            } else if (!ilkHata) {
              ilkHata = yanit.hata || "Sınıflandırma başarısız.";
            }
          } catch (e) {
            if (!ilkHata) ilkHata = String(e);
          }
          tamamlanan += grup.length;
          el.progress.style.width = `${Math.round((tamamlanan / tumler.length) * 100)}%`;
          el.durum.textContent = `Sınıflandırılıyor... ${tamamlanan}/${tumler.length}`;
          await gecikme(300);
        }
      }

      const isciSayisi = Math.min(ES_ZAMANLI_ISTEK, gruplar.length);
      await Promise.all(Array.from({ length: isciSayisi }, (_v, i) => isci(i)));

      state.ogeler = Array.from(sonucMap.values());
      chipSayilariGuncelle();
      ciz();
      el.kaydetBtn.disabled = false;

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
      el.btn.textContent = orijinalBtnMetni;
      ustProgres(false);
    }
  }

  function indir() {
    if (state.ogeler.length === 0) return;
    const bugun = new Date().toISOString().slice(0, 10);
    const baslikMetni = cfg.tur === "blog" ? "Finviz Blog Yazıları" : "Finviz Borsa Haberleri";
    const satirlar = [`# ${baslikMetni} - ${bugun}`, ""];

    SINIF_SIRA.forEach((sinif) => {
      const grup = state.ogeler.filter((h) => h.sinif === sinif);
      if (grup.length === 0) return;
      satirlar.push(`## ${SINIF_ETIKET[sinif]} (${grup.length})`);
      satirlar.push("");
      grup.forEach((h) => {
        const ozetStr = h.ai_ozet ? ` — _${h.ai_ozet}_` : "";
        satirlar.push(`- **[${h.saat}] [${h.baslik}](${h.url})** (${h.kaynak})${ozetStr}`);
      });
      satirlar.push("");
    });

    const blob = new Blob([satirlar.join("\n")], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${cfg.tur}_${bugun}.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast("Markdown dosyası indirildi.", "ok");
  }

  chipBaslat();
  el.btn.addEventListener("click", getir);
  el.kaydetBtn.addEventListener("click", indir);
}

/* ===================== Başlat ===================== */
function baslat() {
  temaBaslat();
  sekmeBaslat();
  ayarlarBaslat();
  detayModalBaslat();

  panelOlustur({
    tur: "haber",
    prefix: "haber",
    kategoriId: "haberKategori",
    varsayilanBosMesaj: 'Henüz haber yok.<br>"Haberleri Getir ve Sınıflandır" butonuna tıkla.',
  });

  panelOlustur({
    tur: "blog",
    prefix: "blog",
    kategoriId: null,
    varsayilanBosMesaj: 'Henüz blog yazısı yok.<br>"Blogları Getir ve Sınıflandır" butonuna tıkla.',
  });

  durumYukle();
}

baslat();
