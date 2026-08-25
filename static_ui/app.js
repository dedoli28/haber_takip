"use strict";

/* ===================== Ayarlar ===================== */
const KAYDEDILEN_KEY = "kaydedilen_haberler_v1";
const OTOMATIK_YENILEME_MS = 60 * 1000; // arka plandaki gercek tarama sunucuda (cron) calisir; burada sadece depoyu tazeleriz

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
const KATEGORI_SIRA = ["ana", "hisse", "etf", "kripto", "pazar_nabzi", "blog"];

/* ===================== Helpers ===================== */
function $(id) { return document.getElementById(id); }

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

function yerelTarihIso(d) {
  const yy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${yy}-${mm}-${dd}`;
}

function bugununTarihi() { return yerelTarihIso(new Date()); }
function dununTarihi() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return yerelTarihIso(d);
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

/* ===================== Ayarlar modal (bildirim e-postaları) ===================== */
function epostaChipCiz(epostalar) {
  const kutu = $("epostaListesi");
  kutu.innerHTML = "";
  if (epostalar.length === 0) {
    const bos = document.createElement("p");
    bos.className = "modal-hint";
    bos.style.margin = "0";
    bos.textContent = "Henüz bildirim e-postası eklenmedi.";
    kutu.appendChild(bos);
    return;
  }
  epostalar.forEach((eposta) => {
    const chip = document.createElement("span");
    chip.className = "chip is-active";
    chip.style.cursor = "default";
    chip.style.display = "inline-flex";
    chip.style.alignItems = "center";
    chip.style.gap = "8px";
    chip.textContent = eposta;

    const sil = document.createElement("span");
    sil.textContent = "✕";
    sil.style.cursor = "pointer";
    sil.style.opacity = "0.85";
    sil.addEventListener("click", async (e) => {
      e.stopPropagation();
      await epostaListesiGuncelle(epostalar.filter((x) => x !== eposta));
    });
    chip.appendChild(sil);
    kutu.appendChild(chip);
  });
}

async function epostaAyarlariYukle() {
  try {
    const resp = await fetch("/api/ayarlar");
    const yanit = await resp.json();
    if (yanit.ok) {
      epostaChipCiz(yanit.ayarlar.bildirim_epostalari || []);
    } else {
      toast(yanit.hata || "Ayarlar alınamadı.", "error");
    }
  } catch (e) {
    toast("Ayarlar alınamadı: " + e, "error");
  }
}

async function epostaListesiGuncelle(yeniListe) {
  try {
    const resp = await fetch("/api/ayarlar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bildirim_epostalari: yeniListe }),
    });
    const yanit = await resp.json();
    if (yanit.ok) {
      epostaChipCiz(yanit.ayarlar.bildirim_epostalari || []);
      toast("Bildirim e-postaları güncellendi.", "ok");
    } else {
      toast(yanit.hata || "Kaydedilemedi.", "error");
    }
  } catch (e) {
    toast("Kaydedilemedi: " + e, "error");
  }
}

function epostaGecerliMi(eposta) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(eposta);
}

function ayarlarBaslat() {
  $("openSettings").addEventListener("click", () => {
    $("settingsOverlay").classList.add("is-open");
    epostaAyarlariYukle();
  });
  $("closeSettings").addEventListener("click", () => $("settingsOverlay").classList.remove("is-open"));
  $("settingsOverlay").addEventListener("click", (e) => {
    if (e.target === $("settingsOverlay")) $("settingsOverlay").classList.remove("is-open");
  });

  async function epostaEkle() {
    const input = $("epostaInput");
    const deger = input.value.trim();
    if (!deger) return;
    if (!epostaGecerliMi(deger)) { toast("Geçerli bir e-posta adresi gir.", "warn"); return; }

    const resp = await fetch("/api/ayarlar");
    const yanit = await resp.json();
    const mevcut = yanit.ok ? (yanit.ayarlar.bildirim_epostalari || []) : [];
    if (mevcut.includes(deger)) { toast("Bu e-posta zaten ekli.", "warn"); return; }

    await epostaListesiGuncelle([...mevcut, deger]);
    input.value = "";
  }

  $("epostaEkleBtn").addEventListener("click", epostaEkle);
  $("epostaInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") epostaEkle();
  });
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
  const bugun = bugununTarihi();
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

/* ===================== Detay modal (haber/kayıtlı ortak) ===================== */
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

async function gunuOzetle() {
  $("daySummaryBody").innerHTML = '<p class="detail-summary">Hazırlanıyor...</p>';
  $("daySummaryOverlay").classList.add("is-open");

  try {
    const resp = await fetch("/api/gun-ozeti", { method: "POST" });
    const yanit = await resp.json();
    if (!yanit.ok) {
      $("daySummaryBody").innerHTML = `<p class="detail-summary">${yanit.hata || "Özet alınamadı."}</p>`;
      toast(yanit.hata || "Özet alınamadı.", "error");
      return;
    }

    const govde = $("daySummaryBody");
    govde.innerHTML = "";

    const kategoriler = [...(yanit.kategoriler || [])].sort(
      (a, b) => KATEGORI_SIRA.indexOf(a.kategori) - KATEGORI_SIRA.indexOf(b.kategori)
    );

    kategoriler.forEach((k) => {
      const baslik = document.createElement("p");
      baslik.className = "modal-section-title";
      baslik.style.marginTop = "14px";
      baslik.textContent = KATEGORI_ETIKET[k.kategori] || k.kategori;
      govde.appendChild(baslik);

      const ozet = document.createElement("p");
      ozet.className = "detail-summary";
      ozet.textContent = k.ozet;
      govde.appendChild(ozet);
    });

    const ayrac = document.createElement("div");
    ayrac.className = "modal-divider";
    ayrac.style.margin = "18px 0";
    govde.appendChild(ayrac);

    const genelBaslik = document.createElement("p");
    genelBaslik.className = "modal-section-title";
    genelBaslik.textContent = "Genel Özet";
    govde.appendChild(genelBaslik);

    const genelOzet = document.createElement("p");
    genelOzet.className = "detail-summary";
    genelOzet.textContent = yanit.genelOzet || "";
    govde.appendChild(genelOzet);
  } catch (e) {
    $("daySummaryBody").innerHTML = `<p class="detail-summary">Beklenmeyen hata: ${e}</p>`;
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

/* ===================== Haberler paneli (yalnizca filtreleme, cekme yok) ===================== */
function haberPaneliOlustur() {
  const state = { tumOgeler: [], sinifFiltre: "", kategoriFiltre: "", tarihFiltre: "", arama: "" };

  const sinifChips = document.querySelectorAll("#haberChips .chip");
  const kategoriChips = document.querySelectorAll("#haberKategoriChips .chip");
  const tarihChips = document.querySelectorAll("#haberTarihChips .chip");
  const arama = $("haberArama");

  function filtrele() {
    let gorulen = state.tumOgeler;
    if (state.sinifFiltre) gorulen = gorulen.filter((h) => h.sinif === state.sinifFiltre);
    if (state.kategoriFiltre) gorulen = gorulen.filter((h) => h.kategori === state.kategoriFiltre);
    if (state.tarihFiltre === "bugun") gorulen = gorulen.filter((h) => h.tarih === bugununTarihi());
    if (state.tarihFiltre === "dun") gorulen = gorulen.filter((h) => h.tarih === dununTarihi());
    if (state.arama) {
      const q = state.arama.toLowerCase();
      gorulen = gorulen.filter(
        (h) =>
          (h.baslikTr || "").toLowerCase().includes(q) ||
          (h.baslik || "").toLowerCase().includes(q) ||
          (h.ai_ozet || "").toLowerCase().includes(q)
      );
    }
    return gorulen;
  }

  function chipSayilariGuncelle() {
    const sayilar = {};
    state.tumOgeler.forEach((h) => { sayilar[h.sinif] = (sayilar[h.sinif] || 0) + 1; });
    sinifChips.forEach((chip) => {
      const sinif = chip.dataset.sinif;
      if (!sinif) { chip.textContent = `Tümü (${state.tumOgeler.length})`; return; }
      chip.textContent = `${SINIF_ETIKET[sinif]} (${sayilar[sinif] || 0})`;
    });

    const katSayilar = {};
    state.tumOgeler.forEach((h) => { if (h.kategori) katSayilar[h.kategori] = (katSayilar[h.kategori] || 0) + 1; });
    kategoriChips.forEach((chip) => {
      const kat = chip.dataset.kategori;
      if (!kat) { chip.textContent = `Tüm Türler (${state.tumOgeler.length})`; return; }
      chip.textContent = `${KATEGORI_ETIKET[kat] || kat} (${katSayilar[kat] || 0})`;
    });

    const bugun = bugununTarihi();
    const dun = dununTarihi();
    const bugunSayisi = state.tumOgeler.filter((h) => h.tarih === bugun).length;
    const dunSayisi = state.tumOgeler.filter((h) => h.tarih === dun).length;
    tarihChips.forEach((chip) => {
      const t = chip.dataset.tarih;
      if (!t) { chip.textContent = `Tüm Tarihler (${state.tumOgeler.length})`; return; }
      chip.textContent = t === "bugun" ? `Bugün (${bugunSayisi})` : `Dün (${dunSayisi})`;
    });
  }

  function ciz() {
    const kutu = $("haberListe");
    kutu.innerHTML = "";
    const gorulen = filtrele();

    $("haberKaydetBtn").disabled = state.tumOgeler.length === 0;
    $("haberGunOzetBtn").disabled = state.tumOgeler.length === 0;

    if (gorulen.length === 0) {
      bosDurumCiz(
        "haberListe",
        state.tumOgeler.length
          ? "Seçilen filtre/aramayla eşleşen içerik yok."
          : 'Henüz haber yok. Arka plan taraması ilk sonuçları getirdiğinde burada görünecek.<br>"Yenile"ye basarak da kontrol edebilirsin.'
      );
      return;
    }
    gorulen.forEach((h, i) => kutu.appendChild(kartOlustur(h, i, detayGoster)));
  }

  sinifChips.forEach((chip) => {
    chip.addEventListener("click", () => {
      state.sinifFiltre = chip.dataset.sinif;
      sinifChips.forEach((c) => c.classList.remove("is-active"));
      chip.classList.add("is-active");
      ciz();
    });
  });

  kategoriChips.forEach((chip) => {
    chip.addEventListener("click", () => {
      state.kategoriFiltre = chip.dataset.kategori;
      kategoriChips.forEach((c) => c.classList.remove("is-active"));
      chip.classList.add("is-active");
      ciz();
    });
  });

  tarihChips.forEach((chip) => {
    chip.addEventListener("click", () => {
      state.tarihFiltre = chip.dataset.tarih;
      tarihChips.forEach((c) => c.classList.remove("is-active"));
      chip.classList.add("is-active");
      ciz();
    });
  });

  arama.addEventListener("input", () => {
    state.arama = arama.value.trim();
    ciz();
  });

  $("haberKaydetBtn").addEventListener("click", () => {
    indirMarkdown(filtrele(), "Finviz Haberleri", "haberler");
  });

  $("haberGunOzetBtn").addEventListener("click", gunuOzetle);

  async function yenile(sessiz = false) {
    if (!sessiz) {
      ustProgres(true);
      $("haberYenileBtn").disabled = true;
    }
    try {
      const resp = await fetch("/api/haberler");
      const yanit = await resp.json();
      if (!yanit.ok) {
        if (!sessiz) toast(yanit.hata || "Haberler alınamadı.", "error");
        return;
      }
      state.tumOgeler = yanit.haberler || [];
      chipSayilariGuncelle();
      ciz();

      if (yanit.sonTarama) {
        const tarih = new Date(yanit.sonTarama);
        $("haberSonTarama").textContent = `Sistem arka planda otomatik olarak Finviz'i tarıyor. Son tarama: ${tarih.toLocaleString("tr-TR")}`;
      }
    } catch (e) {
      if (!sessiz) toast("Beklenmeyen hata: " + e, "error");
    } finally {
      if (!sessiz) {
        ustProgres(false);
        $("haberYenileBtn").disabled = false;
      }
    }
  }

  $("haberYenileBtn").addEventListener("click", () => yenile(false));

  return yenile;
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

  const haberYenile = haberPaneliOlustur();
  haberYenile(false);

  setInterval(() => haberYenile(true), OTOMATIK_YENILEME_MS);
}

baslat();
