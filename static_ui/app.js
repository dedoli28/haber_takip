"use strict";

/* ===================== Ayarlar ===================== */
const BATCH_SIZE = 12;
const ES_ZAMANLI_ISTEK = 4;

/* ===================== State ===================== */
const state = {
  haberler: [],
  haberFiltre: "",
  haberCalisiyor: false,
};

const SINIF_SIRA = ["cok_onemli", "onemli", "bakmaya_deger", "onemsiz"];
const SINIF_ETIKET = {
  cok_onemli: "Çok Önemli",
  onemli: "Önemli",
  bakmaya_deger: "Bakmaya Değer",
  onemsiz: "Önemsiz",
};

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
  $("haberModel").value = modelAl();
}

/* ===================== Kart oluşturma ===================== */
function haberKartOlustur(h, index) {
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
  kart.addEventListener("click", () => haberDetayGoster(h));
  return kart;
}

/* ===================== Detay modal ===================== */
function haberDetayGoster(h) {
  $("detailBadge").textContent = SINIF_ETIKET[h.sinif] || h.sinif;
  $("detailBadge").className = `badge badge-${h.sinif}`;
  $("detailMeta").textContent = `${h.saat} · ${h.kaynak}`;
  $("detailTitle").textContent = h.baslik;
  $("detailSummary").textContent = h.ai_ozet || "Bu haber için henüz bir özet yok.";
  $("detailOpenLink").href = h.url;
  $("detailOverlay").classList.add("is-open");
}

function detayModalBaslat() {
  $("closeDetail").addEventListener("click", () => $("detailOverlay").classList.remove("is-open"));
  $("detailOverlay").addEventListener("click", (e) => {
    if (e.target === $("detailOverlay")) $("detailOverlay").classList.remove("is-open");
  });
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

/* ===================== Haberler render + filtre ===================== */
function haberleriCiz() {
  const kutu = $("haberListe");
  kutu.innerHTML = "";
  const gorulen = state.haberFiltre
    ? state.haberler.filter((h) => h.sinif === state.haberFiltre)
    : state.haberler;

  if (gorulen.length === 0) {
    bosDurumCiz("haberListe", state.haberler.length ? "Seçilen filtreyle eşleşen haber yok." : 'Henüz haber yok.<br>"Haberleri Getir ve Sınıflandır" butonuna tıkla.');
    return;
  }
  gorulen.forEach((h, i) => kutu.appendChild(haberKartOlustur(h, i)));
}

function haberChipSayilariGuncelle() {
  const sayilar = {};
  state.haberler.forEach((h) => { sayilar[h.sinif] = (sayilar[h.sinif] || 0) + 1; });
  document.querySelectorAll("#haberChips .chip").forEach((chip) => {
    const sinif = chip.dataset.sinif;
    if (!sinif) { chip.textContent = `Tümü (${state.haberler.length})`; return; }
    chip.textContent = `${SINIF_ETIKET[sinif]} (${sayilar[sinif] || 0})`;
  });
}

function haberChipBaslat() {
  document.querySelectorAll("#haberChips .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.haberFiltre = chip.dataset.sinif;
      document.querySelectorAll("#haberChips .chip").forEach((c) => c.classList.remove("is-active"));
      chip.classList.add("is-active");
      haberleriCiz();
    });
  });
}

/* ===================== Haberleri getir + sınıflandır ===================== */
async function haberleriGetir() {
  if (state.haberCalisiyor) return;

  const apiKey = anahtarAl();
  if (!apiKey) {
    toast("Önce Ayarlar'dan bir Gemini API anahtarı girin.", "warn");
    $("settingsOverlay").classList.add("is-open");
    return;
  }

  state.haberCalisiyor = true;
  state.haberler = [];
  iskeletCiz("haberListe", 5);
  $("haberKaydetBtn").disabled = true;
  $("haberBtn").disabled = true;
  $("haberBtn").textContent = "Çalışıyor...";
  $("haberProgress").style.width = "0%";
  $("haberDurum").textContent = "Finviz'den haberler çekiliyor...";
  ustProgres(true);

  const model = $("haberModel").value;
  const gun = $("haberGun").value;

  try {
    const cekResp = await fetch(`/api/haberler?gun=${encodeURIComponent(gun)}`);
    const cekSonuc = await cekResp.json();

    if (!cekSonuc.ok) {
      toast(cekSonuc.hata || "Haberler alınamadı.", "error");
      $("haberDurum").textContent = "Hata oluştu.";
      return;
    }

    const tumler = cekSonuc.haberler;
    if (tumler.length === 0) {
      $("haberDurum").textContent = `${cekSonuc.tarih} tarihine ait haber bulunamadı.`;
      bosDurumCiz("haberListe", "Seçilen tarih için haber bulunamadı.");
      return;
    }

    $("haberDurum").textContent = `${tumler.length} haber bulundu. Sınıflandırılıyor...`;

    const sonucMap = new Map(tumler.map((h) => [h.id, { ...h, sinif: "bakmaya_deger", ai_ozet: "" }]));
    const gruplar = [];
    for (let i = 0; i < tumler.length; i += BATCH_SIZE) gruplar.push(tumler.slice(i, i + BATCH_SIZE));

    let tamamlanan = 0;
    let ilkHata = null;
    let siraNo = 0;

    async function isci() {
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
        $("haberProgress").style.width = `${Math.round((tamamlanan / tumler.length) * 100)}%`;
        $("haberDurum").textContent = `Sınıflandırılıyor... ${tamamlanan}/${tumler.length}`;
      }
    }

    const isciSayisi = Math.min(ES_ZAMANLI_ISTEK, gruplar.length);
    await Promise.all(Array.from({ length: isciSayisi }, () => isci()));

    state.haberler = Array.from(sonucMap.values());
    haberChipSayilariGuncelle();
    haberleriCiz();
    $("haberKaydetBtn").disabled = false;

    if (ilkHata) {
      $("haberDurum").textContent = `Tamamlandı (bazı gruplar başarısız oldu).`;
      toast(ilkHata, "warn");
    } else {
      $("haberDurum").textContent = `Tamamlandı. Toplam ${tumler.length} haber (${new Date().toLocaleTimeString("tr-TR")}).`;
    }
  } catch (e) {
    toast("Beklenmeyen hata: " + e, "error");
    $("haberDurum").textContent = "Hata oluştu.";
  } finally {
    state.haberCalisiyor = false;
    $("haberBtn").disabled = false;
    $("haberBtn").textContent = "Haberleri Getir ve Sınıflandır";
    ustProgres(false);
  }
}

/* ===================== Markdown indir ===================== */
function haberleriIndir() {
  if (state.haberler.length === 0) return;

  const bugun = new Date().toISOString().slice(0, 10);
  const satirlar = [`# Finviz Borsa Haberleri - ${bugun}`, ""];

  SINIF_SIRA.forEach((sinif) => {
    const grup = state.haberler.filter((h) => h.sinif === sinif);
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
  a.download = `rapor_${bugun}.md`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  toast("Markdown dosyası indirildi.", "ok");
}

/* ===================== Başlat ===================== */
function baslat() {
  temaBaslat();
  ayarlarBaslat();
  detayModalBaslat();
  haberChipBaslat();

  $("haberBtn").addEventListener("click", haberleriGetir);
  $("haberKaydetBtn").addEventListener("click", haberleriIndir);

  durumYukle();
}

baslat();
