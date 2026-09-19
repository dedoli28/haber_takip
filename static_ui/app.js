"use strict";

/* ===================== Ayarlar ===================== */
const KAYDEDILEN_KEY = "kaydedilen_haberler_v1";
const SON_GUN_OZETI_KEY = "son_gun_ozeti_v1";
const OTOMATIK_YENILEME_MS = 60 * 1000; // arka plandaki gercek tarama sunucuda (cron) calisir; burada sadece depoyu tazeleriz
const HABER_ISTEK_ZAMAN_ASIMI_MS = 20 * 1000;
const TARAMA_GECIKME_UYARISI_DK = 45;

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
const SINIF_ONEM_SIRA = { cok_onemli: 0, onemli: 1, bakmaya_deger: 2, onemsiz: 3 };
const ULKE_ETIKET = { TR: "Türkiye", US: "ABD", DE: "Almanya", CN: "Çin" };
const ULKE_SIRA = ["TR", "US", "DE", "CN"];

/* ===================== Helpers ===================== */
function $(id) { return document.getElementById(id); }

function depoOku(anahtar) {
  try { return localStorage.getItem(anahtar); } catch (e) { return null; }
}
function depoYaz(anahtar, deger) {
  try { localStorage.setItem(anahtar, deger); return true; } catch (e) { return false; }
}

// "ulke" alanı eklenmeden önce kaydedilmiş eski haberler (sunucudaki depoda
// ve tarayıcıdaki Kaydedilenler'de) hep Finviz'den geldi, yani ABD'dir.
function ulkeKodu(h) {
  return h && h.ulke ? String(h.ulke).toUpperCase() : "US";
}
function ulkeEtiketi(kod) {
  return ULKE_ETIKET[kod] || kod;
}
// CSS sınıfı yalnızca bilinen kodlardan üretilir (sunucu verisi sınıf adına
// serbestçe karışmasın).
function ulkeSinifi(kod) {
  return ULKE_ETIKET[kod] ? `ulke-tag ulke-${kod}` : "ulke-tag ulke-diger";
}

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

function goreliZaman(tarih) {
  const dk = Math.max(0, Math.round((Date.now() - tarih.getTime()) / 60000));
  if (dk < 1) return "az önce";
  if (dk < 60) return `${dk} dk önce`;
  const saat = Math.floor(dk / 60);
  if (saat < 24) return `${saat} sa önce`;
  return `${Math.floor(saat / 24)} gün önce`;
}

function kisaZaman(tarih) {
  return tarih.toLocaleString("tr-TR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

// Hızlı, kriptografik olmayan özet: liste değişmediyse (otomatik yenileme) kartları
// yeniden çizmeyip klavye odağını ve kaydırma konumunu koruruz.
function listeImzasi(ogeler) {
  let h = 5381;
  const s = ogeler.map((o) => `${o.url}|${o.sinif}|${o.baslikTr || ""}|${o.saat || ""}`).join("\n");
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return `${ogeler.length}:${h}`;
}

function butonOlustur(metin, sinif, tikla) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = sinif;
  b.textContent = metin;
  b.addEventListener("click", tikla);
  return b;
}

/* ===================== Modal yönetimi (odak, Escape, erişilebilirlik) ===================== */
const ODAKLANABILIR = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
const acikModaller = [];

function arkaPlaniKilitle(kilit) {
  document.querySelectorAll(".app-header, .app-main").forEach((el) => {
    if (kilit) el.setAttribute("inert", ""); else el.removeAttribute("inert");
  });
}

function modalAc(overlayId, secenek = {}) {
  const overlay = $(overlayId);
  if (overlay.classList.contains("is-open")) return;
  const tetik = secenek.tetikleyici || document.activeElement;
  acikModaller.push({
    overlay,
    tetik,
    tetikUrl: tetik && tetik.dataset ? tetik.dataset.url : null,
    kapaninca: secenek.kapaninca,
  });
  overlay.classList.add("is-open");
  arkaPlaniKilitle(true);
  const govde = overlay.querySelector(".modal");
  if (govde && !govde.hasAttribute("tabindex")) govde.tabIndex = -1;
  const hedef = (secenek.ilkOdak && $(secenek.ilkOdak)) || overlay.querySelector(ODAKLANABILIR) || govde;
  requestAnimationFrame(() => { if (hedef) hedef.focus(); });
}

function modalKapat(overlayId) {
  const i = acikModaller.findIndex((m) => m.overlay.id === overlayId);
  if (i < 0) return;
  const [kayit] = acikModaller.splice(i, 1);
  kayit.overlay.classList.remove("is-open");
  if (acikModaller.length === 0) arkaPlaniKilitle(false);
  if (kayit.kapaninca) kayit.kapaninca();

  // Odağı modalı açan öğeye geri ver (liste yenilenip öğe DOM'dan düştüyse aynı haberin kartına).
  let hedef = kayit.tetik;
  if ((!hedef || !hedef.isConnected) && kayit.tetikUrl) {
    hedef = [...document.querySelectorAll(".item-card")].find((k) => k.dataset.url === kayit.tetikUrl);
  }
  if (!hedef || !hedef.isConnected) hedef = $("main");
  if (hedef && hedef.focus) hedef.focus();
}

document.addEventListener("keydown", (e) => {
  if (acikModaller.length === 0) return;
  const ust = acikModaller[acikModaller.length - 1];
  if (e.key === "Escape") {
    e.preventDefault();
    modalKapat(ust.overlay.id);
    return;
  }
  if (e.key !== "Tab") return;
  const odaklar = [...ust.overlay.querySelectorAll(ODAKLANABILIR)].filter((el) => el.offsetParent !== null);
  if (odaklar.length === 0) { e.preventDefault(); return; }
  const ilk = odaklar[0];
  const son = odaklar[odaklar.length - 1];
  const aktif = document.activeElement;
  if (!ust.overlay.contains(aktif)) { e.preventDefault(); ilk.focus(); }
  else if (e.shiftKey && aktif === ilk) { e.preventDefault(); son.focus(); }
  else if (!e.shiftKey && aktif === son) { e.preventDefault(); ilk.focus(); }
});

function modalBagla(overlayId, kapatBtnId) {
  $(kapatBtnId).addEventListener("click", () => modalKapat(overlayId));
  $(overlayId).addEventListener("click", (e) => {
    if (e.target === $(overlayId)) modalKapat(overlayId);
  });
}

/* ===================== Tema ===================== */
function temaBaslat() {
  const kayitli = depoOku("tema") || "dark";
  temaUygula(kayitli, false);
  $("themeToggle").addEventListener("click", () => {
    const simdiki = document.documentElement.getAttribute("data-theme") || "dark";
    temaUygula(simdiki === "dark" ? "light" : "dark", true);
  });
}
function temaUygula(tema, bildir) {
  document.documentElement.setAttribute("data-theme", tema);
  depoYaz("tema", tema);
  $("themeIconMoon").style.display = tema === "dark" ? "block" : "none";
  $("themeIconSun").style.display = tema === "light" ? "block" : "none";
  $("themeToggle").setAttribute("aria-label", tema === "dark" ? "Açık temaya geç" : "Koyu temaya geç");
  // Grafikler CSS değişkenlerinden renk aldığı için yeni temayla yeniden çizilir.
  if (bildir && typeof PiyasaGorunumu !== "undefined") PiyasaGorunumu.temaDegisti();
}

/* ===================== Sekmeler ===================== */
function tabsThumbGuncelle() {
  const aktifBtn = document.querySelector(".tab-btn.is-active");
  const thumb = $("tabsThumb");
  if (!aktifBtn || !thumb) return;
  thumb.style.width = `${aktifBtn.offsetWidth}px`;
  thumb.style.transform = `translateX(${aktifBtn.offsetLeft}px)`;
}

function sekmeBaslat(sekmeDegistiCallback) {
  const butonlar = [...document.querySelectorAll(".tab-btn")];

  function sec(btn) {
    butonlar.forEach((b) => {
      const aktif = b === btn;
      b.classList.toggle("is-active", aktif);
      b.setAttribute("aria-selected", aktif ? "true" : "false");
      b.tabIndex = aktif ? 0 : -1;
    });
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("is-active"));
    $(`view-${btn.dataset.tab}`).classList.add("is-active");
    tabsThumbGuncelle();
    if (sekmeDegistiCallback) sekmeDegistiCallback(btn.dataset.tab);
  }

  butonlar.forEach((btn) => btn.addEventListener("click", () => sec(btn)));
  $("tabNav").addEventListener("keydown", (e) => {
    const i = butonlar.indexOf(document.activeElement);
    if (i < 0) return;
    let yeni = -1;
    if (e.key === "ArrowRight") yeni = (i + 1) % butonlar.length;
    else if (e.key === "ArrowLeft") yeni = (i - 1 + butonlar.length) % butonlar.length;
    else if (e.key === "Home") yeni = 0;
    else if (e.key === "End") yeni = butonlar.length - 1;
    if (yeni >= 0) {
      e.preventDefault();
      butonlar[yeni].focus();
      sec(butonlar[yeni]);
    }
  });

  tabsThumbGuncelle();
  window.addEventListener("resize", tabsThumbGuncelle);
}

/* ===================== Çoklu / tekli seçim çip grupları ===================== */
function cokluSecimGrubuBaslat(chips, attr, degisinceCB) {
  const secili = new Set();

  function gorunumGuncelle() {
    chips.forEach((chip) => {
      const deger = chip.dataset[attr];
      const aktif = deger ? secili.has(deger) : secili.size === 0;
      chip.classList.toggle("is-active", aktif);
      chip.setAttribute("aria-pressed", aktif ? "true" : "false");
    });
  }

  chips.forEach((chip) => {
    chip.addEventListener("click", () => {
      const deger = chip.dataset[attr];
      if (!deger) {
        secili.clear();
      } else if (secili.has(deger)) {
        secili.delete(deger);
      } else {
        secili.add(deger);
      }
      gorunumGuncelle();
      degisinceCB();
    });
  });

  gorunumGuncelle();
  return {
    secili,
    gorunumGuncelle,
    sil: (deger) => { secili.delete(deger); gorunumGuncelle(); },
    temizle: () => { secili.clear(); gorunumGuncelle(); },
    etiket: (deger) => {
      const chip = [...chips].find((c) => c.dataset[attr] === deger);
      return chip ? chip.textContent.trim() : deger;
    },
  };
}

function tekliSecimGrubuBaslat(chips, attr, degisinceCB) {
  const durum = { deger: "" };

  function gorunumGuncelle() {
    chips.forEach((chip) => {
      const aktif = (chip.dataset[attr] || "") === durum.deger;
      chip.classList.toggle("is-active", aktif);
      chip.setAttribute("aria-pressed", aktif ? "true" : "false");
    });
  }

  chips.forEach((chip) => {
    chip.addEventListener("click", () => {
      durum.deger = chip.dataset[attr] || "";
      gorunumGuncelle();
      degisinceCB();
    });
  });

  gorunumGuncelle();
  return {
    durum,
    gorunumGuncelle,
    temizle: () => { durum.deger = ""; gorunumGuncelle(); },
    etiket: (deger) => {
      const chip = [...chips].find((c) => (c.dataset[attr] || "") === deger);
      return chip ? chip.textContent.trim() : deger;
    },
  };
}

/* ===================== Filtreler popover ===================== */
function filtrePopoverBaslat(btnId, popoverId) {
  const btn = $(btnId);
  const popover = $(popoverId);

  function ayarla(ac, odagiGeriVer) {
    popover.classList.toggle("is-open", ac);
    btn.setAttribute("aria-expanded", ac ? "true" : "false");
    if (!ac && odagiGeriVer) btn.focus();
  }

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const aciliyor = !popover.classList.contains("is-open");
    document.querySelectorAll(".filter-popover.is-open").forEach((p) => {
      if (p !== popover) p.classList.remove("is-open");
    });
    ayarla(aciliyor, false);
  });
  popover.addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", () => ayarla(false, false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && popover.classList.contains("is-open") && acikModaller.length === 0) ayarla(false, true);
  });
}

function filtreRozetGuncelle(rozetId, sayac) {
  const rozet = $(rozetId);
  if (sayac > 0) {
    rozet.textContent = String(sayac);
    rozet.style.display = "inline-flex";
  } else {
    rozet.style.display = "none";
  }
}

/* ===================== Ayarlar modal (bildirim e-postaları) ===================== */
let mevcutAyarlar = { alicilar: [] };

function ayarDurumu(tur, mesaj, tekrarFn) {
  const kutu = $("ayarlarDurum");
  kutu.textContent = "";
  kutu.className = "ayar-durum" + (tur ? ` ${tur}` : "");
  if (!mesaj) { kutu.hidden = true; return; }
  kutu.hidden = false;
  kutu.appendChild(document.createTextNode(mesaj));
  if (tekrarFn) {
    kutu.appendChild(document.createTextNode(" "));
    kutu.appendChild(butonOlustur("Tekrar dene", "btn btn-outline btn-sm", tekrarFn));
  }
}

function aliciListesiIskeleti() {
  const kutu = $("epostaListesi");
  kutu.innerHTML = "";
  kutu.setAttribute("aria-busy", "true");
  for (let i = 0; i < 2; i++) {
    const s = document.createElement("div");
    s.className = "alici-iskelet";
    kutu.appendChild(s);
  }
}

function aliciListesiCiz() {
  const kutu = $("epostaListesi");
  kutu.innerHTML = "";
  kutu.setAttribute("aria-busy", "false");

  if (mevcutAyarlar.alicilar.length === 0) {
    const bos = document.createElement("p");
    bos.className = "modal-hint";
    bos.style.margin = "0";
    bos.textContent = "Henüz bildirim e-postası eklenmedi.";
    kutu.appendChild(bos);
    return;
  }

  mevcutAyarlar.alicilar.forEach((alici, index) => {
    const satir = document.createElement("div");
    satir.className = "alici-esik-satir";

    const bas = document.createElement("div");
    bas.className = "alici-esik-bas";

    const adres = document.createElement("span");
    adres.className = "alici-esik-adres";
    adres.textContent = alici.eposta;
    bas.appendChild(adres);

    const silBtn = document.createElement("button");
    silBtn.type = "button";
    silBtn.className = "alici-sil-btn";
    silBtn.textContent = "✕";
    silBtn.title = "Kaldır";
    silBtn.setAttribute("aria-label", `${alici.eposta} adresini kaldır`);
    silBtn.addEventListener("click", () => {
      mevcutAyarlar.alicilar.splice(index, 1);
      ayarlariKaydet("E-posta kaldırıldı.");
    });
    bas.appendChild(silBtn);

    satir.appendChild(bas);
    kutu.appendChild(satir);
  });
}

async function ayarlarYukle() {
  ayarDurumu("yukleniyor", "Ayarlar yükleniyor…");
  aliciListesiIskeleti();
  try {
    const resp = await fetch("/api/ayarlar");
    const yanit = await resp.json();
    if (!yanit.ok) throw new Error("http");

    mevcutAyarlar = { alicilar: yanit.ayarlar.alicilar || [] };
    aliciListesiCiz();
    ayarDurumu("", "");
  } catch (e) {
    $("epostaListesi").setAttribute("aria-busy", "false");
    $("epostaListesi").innerHTML = "";
    ayarDurumu("hata", "Ayarlar alınamadı. Bağlantınızı kontrol edip tekrar deneyin.", ayarlarYukle);
  }
}

async function ayarlariKaydet(basariMesaji) {
  ayarDurumu("yukleniyor", "Kaydediliyor…");
  $("epostaEkleBtn").disabled = true;
  try {
    const resp = await fetch("/api/ayarlar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(mevcutAyarlar),
    });
    const yanit = await resp.json();
    if (yanit.ok) {
      mevcutAyarlar = yanit.ayarlar;
      aliciListesiCiz();
      ayarDurumu("ok", basariMesaji || "Kaydedildi.");
      if (basariMesaji) toast(basariMesaji, "ok");
    } else {
      ayarDurumu("hata", "Kaydedilemedi. Lütfen tekrar deneyin.", () => ayariTekrarKaydet(basariMesaji));
      toast("Ayarlar kaydedilemedi.", "error");
    }
  } catch (e) {
    ayarDurumu("hata", "Kaydedilemedi: bağlantı kurulamadı.", () => ayariTekrarKaydet(basariMesaji));
    toast("Ayarlar kaydedilemedi.", "error");
  } finally {
    $("epostaEkleBtn").disabled = false;
  }
}
function ayariTekrarKaydet(mesaj) { ayarlariKaydet(mesaj); }

function epostaGecerliMi(eposta) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(eposta);
}

function ayarlarBaslat() {
  $("openSettings").addEventListener("click", (e) => {
    modalAc("settingsOverlay", { tetikleyici: e.currentTarget, ilkOdak: "epostaInput" });
    ayarlarYukle();
  });
  modalBagla("settingsOverlay", "closeSettings");

  function epostaEkle() {
    const input = $("epostaInput");
    const deger = input.value.trim();
    if (!deger) return;
    if (!epostaGecerliMi(deger)) { ayarDurumu("hata", "Geçerli bir e-posta adresi girin."); input.focus(); return; }
    if (mevcutAyarlar.alicilar.some((a) => a.eposta === deger)) { ayarDurumu("hata", "Bu e-posta zaten ekli."); return; }

    mevcutAyarlar.alicilar.push({ eposta: deger });
    ayarlariKaydet("E-posta eklendi.");
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
  try { localStorage.setItem(KAYDEDILEN_KEY, JSON.stringify(liste)); } catch (e) { toast("Tarayıcı depolaması kullanılamıyor; kayıt yapılamadı.", "error"); }
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

function detayGoster(h, tetikleyici) {
  suankiOge = h;
  $("detailBadge").textContent = SINIF_ETIKET[h.sinif] || h.sinif;
  $("detailBadge").className = `badge badge-${h.sinif}`;

  const ulke = ulkeKodu(h);
  $("detailUlke").textContent = ulkeEtiketi(ulke);
  $("detailUlke").className = ulkeSinifi(ulke);

  const katEl = $("detailKategori");
  if (h.kategori && KATEGORI_ETIKET[h.kategori]) {
    katEl.textContent = KATEGORI_ETIKET[h.kategori];
    katEl.style.display = "";
  } else {
    katEl.style.display = "none";
  }

  $("detailMeta").textContent = `${h.saat} · ${h.kaynak}`;
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
  modalAc("detailOverlay", {
    tetikleyici,
    kapaninca: () => { if (typeof PiyasaGorunumu !== "undefined") PiyasaGorunumu.detayTemizle(); },
  });
  if (typeof PiyasaGorunumu !== "undefined") PiyasaGorunumu.detayGoster(h);
}

function detayModalBaslat(kayitliPanelYenile) {
  modalBagla("detailOverlay", "closeDetail");

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
        const limit = resp.status === 429;
        text.textContent = yanit.hata || (limit ? "Çok fazla analiz isteği gönderildi; biraz sonra tekrar deneyin." : "Analiz alınamadı.");
        btn.textContent = oncekiMetin;
        toast(text.textContent, limit ? "warn" : "error");
      }
    } catch (e) {
      text.textContent = "Bağlantı kurulamadı. Lütfen tekrar deneyin.";
      btn.textContent = oncekiMetin;
    } finally {
      btn.disabled = false;
    }
  });
}

/* ===================== Gün özeti modal ===================== */
// Modal her açıldığında artar; kapanan/eski bir modalın istek ve yoklama sonuçları yok sayılır.
let ozetOturum = 0;
const OZET_YOKLAMA_MS = 4000;
const OZET_YOKLAMA_AZAMI = 16;
const OZET_HATA_METNI = {
  anahtar_yok: "Özet servisi bu sunucuda yapılandırılmamış.",
  haber_yok: "Bugün için özetlenecek haber henüz yok.",
  gemini_kota: "Yapay zekâ servisinin günlük kullanım sınırına ulaşıldı.",
  gemini_limit: "Yapay zekâ servisi geçici olarak istek sınırına ulaştı.",
  gemini_zaman_asimi: "Özet oluşturulurken zaman aşımı oldu.",
  gemini_yogun: "Yapay zekâ servisi şu anda yoğun.",
  zaman_asimi: "Özet oluşturma beklenenden uzun sürdü.",
  depo_hatasi: "Veri deposuna ulaşılamadı.",
  bilinmeyen: "Özet oluşturulamadı.",
};

function gunOzetiModalBaslat() {
  modalBagla("daySummaryOverlay", "closeDaySummary");
}

function gunOzetiEylemleri(...butonlar) {
  const kutu = $("daySummaryActions");
  kutu.textContent = "";
  const liste = butonlar.filter(Boolean);
  liste.forEach((b) => kutu.appendChild(b));
  kutu.hidden = liste.length === 0;
}

// Modal gövdesine tek bir mesaj yazar. Metinler textContent ile eklenir (XSS'e karşı).
function gunOzetiMesajGoster(metin) {
  const govde = $("daySummaryBody");
  govde.textContent = "";
  const p = document.createElement("p");
  p.className = "detail-summary";
  p.textContent = metin;
  govde.appendChild(p);
  $("daySummaryFooter").textContent = "";
  gunOzetiEylemleri();
}

function sonBasariliOzeti() {
  try {
    const ham = depoOku(SON_GUN_OZETI_KEY);
    return ham ? JSON.parse(ham) : null;
  } catch (e) {
    return null;
  }
}

function ozetIcerikVarMi(y) {
  return !!(y && (y.genelOzet || (y.kategoriler && y.kategoriler.length)));
}

function ozetHataMetni(kod) {
  return OZET_HATA_METNI[kod] || OZET_HATA_METNI.bilinmeyen;
}

function ozetTarihMetni(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!m) return iso || "";
  return new Date(+m[1], +m[2] - 1, +m[3]).toLocaleDateString("tr-TR", { day: "numeric", month: "long" });
}

// Sunucu yanıtından modalın üstünde gösterilecek durum notu (yoksa null).
function ozetDurumNotu(y, yenileniyor) {
  if (yenileniyor || y.durum === "generating") return { tur: "bilgi", metin: "Özet şu anda güncelleniyor…" };
  if (y.durum === "failed") {
    let m = `Özet güncellenemedi: ${ozetHataMetni(y.hataKodu)}`;
    if (y.sonrakiDenemeSn) m += ` Yeni deneme yaklaşık ${Math.ceil(y.sonrakiDenemeSn / 60)} dk sonra mümkün.`;
    else m += ` Sonraki otomatik güncelleme: ${y.sonrakiGuncelleme}.`;
    return { tur: "uyari", metin: m };
  }
  if (y.gecmisGun && y.gosterilenTarih) {
    return {
      tur: "uyari",
      metin: `Bugünün özeti henüz hazır değil; ${ozetTarihMetni(y.gosterilenTarih)} tarihli son özet gösteriliyor. Sonraki otomatik güncelleme: ${y.sonrakiGuncelleme}.`,
    };
  }
  if (!y.guncelMi) {
    return { tur: "bilgi", metin: `Bu özet son planlı güncellemeden önce hazırlandı. Sonraki otomatik güncelleme: ${y.sonrakiGuncelleme}.` };
  }
  return null;
}

function gunOzetiIcerikCiz(y, not) {
  const govde = $("daySummaryBody");
  govde.textContent = "";

  if (not) {
    const u = document.createElement("p");
    u.className = "day-summary-uyari";
    u.setAttribute("role", "status");
    u.textContent = not.metin;
    govde.appendChild(u);
  }

  const kategoriler = [...(y.kategoriler || [])].sort(
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
  genelOzet.textContent = y.genelOzet || "";
  govde.appendChild(genelOzet);

  // Özetin ne zaman hazırlandığı (kullanıcı ne kadar güncel olduğunu görsün).
  const uretim = y.olusturulmaZamani ? new Date(y.olusturulmaZamani) : null;
  let alt = uretim && !isNaN(uretim.getTime())
    ? `Özet hazırlanma zamanı: ${uretim.toLocaleString("tr-TR")}`
    : "Özet hazırlanma zamanı bilinmiyor.";
  if (Number.isFinite(y.haberSayisi)) alt += ` · ${y.haberSayisi} habere dayanır`;
  $("daySummaryFooter").textContent = alt + planliSaatMetni(y);
}

function planliSaatMetni(y) {
  return y && y.planliSaatler && y.planliSaatler.length
    ? ` · Otomatik güncelleme: ${y.planliSaatler.join(", ")} (Türkiye saati)`
    : "";
}

// Sunucu yanıtına göre modalı çizer; içerik yoksa duruma uygun açıklama + eylem gösterir.
function gunOzetiGoster(y, yenileniyor = false) {
  const not = ozetDurumNotu(y, yenileniyor);
  if (ozetIcerikVarMi(y)) {
    gunOzetiIcerikCiz(y, not);
    if (y.durum === "failed") gunOzetiEylemleri(butonOlustur("Tekrar dene", "btn btn-primary btn-sm", gunOzetiYukle));
    else gunOzetiEylemleri();
    return;
  }

  let mesaj;
  if (yenileniyor || y.durum === "generating") {
    mesaj = "Bugünün özeti hazırlanıyor. Bu birkaç saniye sürebilir…";
  } else if (y.durum === "failed") {
    mesaj = `Bugünün özeti oluşturulamadı: ${ozetHataMetni(y.hataKodu)}`;
    if (y.sonrakiDenemeSn) mesaj += ` Yeni deneme yaklaşık ${Math.ceil(y.sonrakiDenemeSn / 60)} dk sonra mümkün.`;
  } else {
    const saatler = (y.planliSaatler || []).join(", ");
    mesaj = `Bugünün özeti henüz hazırlanmadı. Özet her gün ${saatler} saatlerinde (Türkiye saati) otomatik oluşturulur. Sonraki güncelleme: ${y.sonrakiGuncelleme}.`;
  }
  gunOzetiMesajGoster(mesaj);
  $("daySummaryFooter").textContent = planliSaatMetni(y).replace(/^ · /, "");
  if (!(yenileniyor || y.durum === "generating")) {
    gunOzetiEylemleri(butonOlustur(y.durum === "failed" ? "Tekrar dene" : "Yeniden kontrol et", "btn btn-primary btn-sm", gunOzetiYukle));
  }
}

async function ozetIstegi(yol, yontem, zamanAsimiMs) {
  const ac = new AbortController();
  const zaman = setTimeout(() => ac.abort(), zamanAsimiMs);
  try {
    const resp = await fetch(yol, { method: yontem, signal: ac.signal, headers: { Accept: "application/json" } });
    let yanit = null;
    try { yanit = await resp.json(); } catch (e) { yanit = null; } // JSON olmayan yanıt (ör. platform hata sayfası)
    if (!resp.ok || !yanit || yanit.ok === false) {
      const hata = new Error("http");
      hata.durum = resp.status;
      throw hata;
    }
    return yanit;
  } finally {
    clearTimeout(zaman);
  }
}

// Ağ/sunucu hatası: açıklama + "Tekrar dene" + (varsa) tarayıcıda saklı son başarılı özet.
function gunOzetiAgHatasi(e) {
  const zamanAsimi = e && e.name === "AbortError";
  const mesaj = zamanAsimi
    ? "Sunucu zamanında yanıt vermedi."
    : e && e.durum
      ? "Günün özeti şu anda alınamıyor. Lütfen biraz sonra tekrar deneyin."
      : "Bağlantı kurulamadı. İnternet bağlantınızı kontrol edin.";
  gunOzetiMesajGoster(mesaj);
  const son = sonBasariliOzeti();
  const butonlar = [butonOlustur("Tekrar dene", "btn btn-primary btn-sm", gunOzetiYukle)];
  if (son && son.yanit) {
    const zaman = son.alindi ? new Date(son.alindi) : null;
    const gecerli = zaman && !isNaN(zaman.getTime());
    butonlar.push(butonOlustur(gecerli ? `Son başarılı özeti göster (${kisaZaman(zaman)})` : "Son başarılı özeti göster", "btn btn-outline btn-sm", () => {
      gunOzetiIcerikCiz(son.yanit, { tur: "uyari", metin: "Bu, bu cihazda daha önce başarıyla alınmış son özettir; güncel olmayabilir." });
      gunOzetiEylemleri(butonOlustur("Yeniden dene", "btn btn-primary btn-sm", gunOzetiYukle));
    }));
    if (gecerli) $("daySummaryFooter").textContent = `Son başarılı özet zamanı: ${zaman.toLocaleString("tr-TR")}`;
  }
  gunOzetiEylemleri(...butonlar);
}

// Başka bir istek/cron özeti üretirken durumu kısa aralıklarla yoklar.
function gunOzetiYokla(oturum, deneme) {
  setTimeout(async () => {
    if (oturum !== ozetOturum) return;
    try {
      const y = await ozetIstegi("/api/gun-ozeti", "GET", 15000);
      if (oturum !== ozetOturum) return;
      if (y.durum === "generating" && deneme + 1 < OZET_YOKLAMA_AZAMI) {
        gunOzetiGoster(y);
        gunOzetiYokla(oturum, deneme + 1);
        return;
      }
      if (y.durum === "generating") {
        gunOzetiGoster({ ...y, durum: "missing" });
        toast("Özet hâlâ hazırlanıyor; biraz sonra tekrar deneyin.", "warn");
        return;
      }
      gunOzetiSonuc(y);
    } catch (e) {
      if (oturum === ozetOturum && deneme + 1 < OZET_YOKLAMA_AZAMI) gunOzetiYokla(oturum, deneme + 1);
    }
  }, OZET_YOKLAMA_MS);
}

function gunOzetiSonuc(y) {
  gunOzetiGoster(y);
  if (ozetIcerikVarMi(y)) depoYaz(SON_GUN_OZETI_KEY, JSON.stringify({ yanit: y, alindi: new Date().toISOString() }));
}

// Özet planlı saate göre bayatsa sunucudan KONTROLLÜ yenileme ister (Gemini çağrısı sunucuda,
// kilit + bekleme kurallarıyla sınırlıdır); bu sırada mevcut özet gösterilmeye devam eder.
async function gunOzetiYenile(oturum, onceki) {
  gunOzetiGoster(onceki, true);
  try {
    const y = await ozetIstegi("/api/gun-ozeti/yenile", "POST", 45000);
    if (oturum !== ozetOturum) return;
    if (y.durum === "generating") {
      gunOzetiGoster(y);
      gunOzetiYokla(oturum, 0);
      return;
    }
    gunOzetiSonuc(y);
  } catch (e) {
    if (oturum !== ozetOturum) return;
    gunOzetiGoster(onceki);
    toast("Özet güncellemesi başlatılamadı. Mevcut özet gösteriliyor.", "warn");
  }
}

// "Günü Özetle": modal açıldığında hazır özeti okur. Gemini'yi yalnızca sunucu,
// planlı saatlerde (10:00/14:00/18:00) ya da kontrollü yenilemede çağırır.
async function gunOzetiYukle() {
  const oturum = ++ozetOturum;
  const govde = $("daySummaryBody");
  govde.setAttribute("aria-busy", "true");
  gunOzetiMesajGoster("Yükleniyor...");
  try {
    const y = await ozetIstegi("/api/gun-ozeti", "GET", 20000);
    if (oturum !== ozetOturum) return;
    gunOzetiSonuc(y);
    if (y.yenilemeGerekli) {
      await gunOzetiYenile(oturum, y);
    } else if (y.durum === "generating") {
      gunOzetiYokla(oturum, 0);
    }
  } catch (e) {
    if (oturum === ozetOturum) gunOzetiAgHatasi(e);
  } finally {
    if (oturum === ozetOturum) govde.setAttribute("aria-busy", "false");
  }
}

function gunuOzetle(tetikleyici) {
  modalAc("daySummaryOverlay", {
    tetikleyici,
    ilkOdak: "closeDaySummary",
    kapaninca: () => { ozetOturum++; },
  });
  gunOzetiYukle();
}

/* ===================== Ortak kart/liste yardımcıları ===================== */
function kartOlustur(h, index, tiklaninca) {
  const kart = document.createElement("div");
  kart.className = `item-card c-${h.sinif}`;
  kart.style.animationDelay = `${Math.min(index * 25, 300)}ms`;
  kart.tabIndex = 0;
  kart.setAttribute("role", "button");
  kart.dataset.url = h.url;

  const serit = document.createElement("div");
  serit.className = "item-stripe";
  kart.appendChild(serit);

  const govde = document.createElement("div");
  govde.className = "item-body";

  // Rozet sırası her yerde aynı: önem → ülke → haber türü → zaman/kaynak.
  const metaRow = document.createElement("div");
  metaRow.className = "item-meta-row";
  const rozet = document.createElement("span");
  rozet.className = "badge";
  rozet.textContent = SINIF_ETIKET[h.sinif] || h.sinif;
  metaRow.appendChild(rozet);

  const ulke = ulkeKodu(h);
  const ulkeTag = document.createElement("span");
  ulkeTag.className = ulkeSinifi(ulke);
  ulkeTag.textContent = ulkeEtiketi(ulke);
  metaRow.appendChild(ulkeTag);

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
  kart.addEventListener("click", () => tiklaninca(h, kart));
  kart.addEventListener("keydown", (e) => {
    if (e.target !== kart) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      tiklaninca(h, kart);
    }
  });
  return kart;
}

const BOS_IKON = '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5v-13Z" stroke="currentColor" stroke-width="1.4"/><path d="M7.5 8.5h9M7.5 12h9M7.5 15.5h5.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>';

// mesaj: düz metin (satır sonu için "\n"); eylemler: [{metin, tikla, birincil}]
function bosDurumCiz(konteynerId, mesaj, eylemler = []) {
  const kutu = $(konteynerId);
  kutu.innerHTML = "";
  kutu.setAttribute("aria-busy", "false");
  const bos = document.createElement("div");
  bos.className = "empty-state";
  bos.innerHTML = BOS_IKON;
  const p = document.createElement("p");
  mesaj.split("\n").forEach((satir, i) => {
    if (i > 0) p.appendChild(document.createElement("br"));
    p.appendChild(document.createTextNode(satir));
  });
  p.setAttribute("role", "status");
  bos.appendChild(p);
  eylemler.forEach((e) => bos.appendChild(butonOlustur(e.metin, e.birincil ? "btn btn-primary btn-sm" : "btn btn-outline btn-sm", e.tikla)));
  kutu.appendChild(bos);
}

function kartIskeletleriCiz(konteynerId, adet = 9) {
  const kutu = $(konteynerId);
  kutu.innerHTML = "";
  kutu.setAttribute("aria-busy", "true");
  for (let i = 0; i < adet; i++) {
    const s = document.createElement("div");
    s.className = "skeleton-card";
    s.setAttribute("aria-hidden", "true");
    kutu.appendChild(s);
  }
}

function aktifFiltreEtiketiOlustur(metin, kaldirFn) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "filter-tag";
  b.setAttribute("aria-label", `${metin} filtresini kaldır`);
  b.appendChild(document.createTextNode(metin));
  const x = document.createElement("span");
  x.className = "filter-tag-x";
  x.setAttribute("aria-hidden", "true");
  x.textContent = "✕";
  b.appendChild(x);
  b.addEventListener("click", kaldirFn);
  return b;
}

/* ===================== Haberler paneli (filtreleme + manuel tarama) ===================== */
function saatSecimKutulariniDoldur() {
  [$("haberSaatBaslangic"), $("haberSaatBitis")].forEach((sel) => {
    if (!sel || sel.dataset.dolduruldu) return;
    for (let s = 0; s < 24; s++) {
      const deger = String(s).padStart(2, "0");
      const opt = document.createElement("option");
      opt.value = deger;
      opt.textContent = deger + ":00";
      sel.appendChild(opt);
    }
    sel.dataset.dolduruldu = "1";
  });
}

function haberHatasiBilgisi(e, yanit) {
  if (e && e.name === "AbortError") return { mesaj: "Sunucu zamanında yanıt vermedi. Lütfen tekrar deneyin.", tur: "error" };
  if (e && e.durum === 429) return { mesaj: "Çok fazla istek gönderildi. Kısa bir süre sonra tekrar deneyin.", tur: "warn" };
  if (e && e.durum >= 500) return { mesaj: "Sunucu şu anda haber deposuna ulaşamıyor. Birazdan tekrar deneyin.", tur: "error" };
  if (e && e.durum) return { mesaj: "Haberler alınamadı. Lütfen tekrar deneyin.", tur: "error" };
  return { mesaj: "Bağlantı kurulamadı. İnternet bağlantınızı kontrol edip tekrar deneyin.", tur: "error" };
}

function haberPaneliOlustur() {
  const state = {
    tumOgeler: [],
    arama: "",
    sonTarama: null,
    yuklendi: false,
    imza: "",
    gosterilen: 0,
    hata: null,
    iptal: null,
  };

  const tarih = tekliSecimGrubuBaslat(
    document.querySelectorAll("#haberTarihChips .chip"), "tarih", () => { rozetGuncelle(); ciz(); }
  );
  const kategori = cokluSecimGrubuBaslat(
    document.querySelectorAll("#haberKategoriChips .chip"), "kategori", () => { rozetGuncelle(); ciz(); }
  );
  const ulke = cokluSecimGrubuBaslat(
    document.querySelectorAll("#haberUlkeChips .chip"), "ulke", () => { rozetGuncelle(); ciz(); }
  );
  const sinif = cokluSecimGrubuBaslat(
    document.querySelectorAll("#haberChips .chip"), "sinif", () => { rozetGuncelle(); ciz(); }
  );

  filtrePopoverBaslat("haberFiltreBtn", "haberFiltrePopover");

  saatSecimKutulariniDoldur();
  const saatBaslangic = $("haberSaatBaslangic");
  const saatBitis = $("haberSaatBitis");
  const siralama = $("haberSiralama");
  const arama = $("haberArama");

  function saatSeciliMi() { return saatBaslangic.value !== "" || saatBitis.value !== ""; }

  function rozetGuncelle() {
    const sayac =
      (tarih.durum.deger ? 1 : 0) + kategori.secili.size + ulke.secili.size + sinif.secili.size + (saatSeciliMi() ? 1 : 0);
    filtreRozetGuncelle("haberFiltreRozet", sayac);
  }

  function tumFiltreleriTemizle() {
    tarih.temizle();
    kategori.temizle();
    ulke.temizle();
    sinif.temizle();
    saatBaslangic.value = "";
    saatBitis.value = "";
    arama.value = "";
    state.arama = "";
    rozetGuncelle();
    ciz();
  }

  saatBaslangic.addEventListener("change", () => { rozetGuncelle(); ciz(); });
  saatBitis.addEventListener("change", () => { rozetGuncelle(); ciz(); });
  if (siralama) siralama.addEventListener("change", ciz);

  $("haberFiltreTemizleBtn").addEventListener("click", tumFiltreleriTemizle);

  function filtrele() {
    let gorulen = state.tumOgeler;
    if (sinif.secili.size > 0) gorulen = gorulen.filter((h) => sinif.secili.has(h.sinif));
    if (kategori.secili.size > 0) gorulen = gorulen.filter((h) => kategori.secili.has(h.kategori));
    if (ulke.secili.size > 0) gorulen = gorulen.filter((h) => ulke.secili.has(ulkeKodu(h)));
    if (tarih.durum.deger === "bugun") gorulen = gorulen.filter((h) => h.tarih === bugununTarihi());
    if (tarih.durum.deger === "dun") gorulen = gorulen.filter((h) => h.tarih === dununTarihi());
    if (saatSeciliMi()) {
      const bas = saatBaslangic.value !== "" ? parseInt(saatBaslangic.value, 10) : 0;
      const bit = saatBitis.value !== "" ? parseInt(saatBitis.value, 10) : 23;
      gorulen = gorulen.filter((h) => {
        // "saat" alani sunucuda Istanbul saatine cevrilmis "HH:MM" bicimindeyse
        // guvenilir sekilde filtrelenebilir; eski/saatsiz kayitlarda (ör. "Aug-19")
        // bu bicimde olmaz, onlar saat araligi seciliyken listeden cikarilir.
        const m = /^(\d{2}):\d{2}$/.exec(h.saat || "");
        if (!m) return false;
        const saat = parseInt(m[1], 10);
        return bas <= bit ? (saat >= bas && saat <= bit) : (saat >= bas || saat <= bit);
      });
    }
    if (state.arama) {
      const q = state.arama.toLowerCase();
      gorulen = gorulen.filter(
        (h) =>
          (h.baslikTr || "").toLowerCase().includes(q) ||
          (h.baslik || "").toLowerCase().includes(q) ||
          (h.ai_ozet || "").toLowerCase().includes(q)
      );
    }

    const mod = siralama ? siralama.value : "tarih_yeni";
    gorulen = gorulen.slice().sort((a, b) => {
      const ta = a.ilkGorulme ? new Date(a.ilkGorulme).getTime() : 0;
      const tb = b.ilkGorulme ? new Date(b.ilkGorulme).getTime() : 0;
      if (mod === "onem") {
        const oa = SINIF_ONEM_SIRA[a.sinif] ?? 99;
        const ob = SINIF_ONEM_SIRA[b.sinif] ?? 99;
        if (oa !== ob) return oa - ob;
        return tb - ta;
      }
      return mod === "tarih_eski" ? ta - tb : tb - ta;
    });

    return gorulen;
  }

  /* --- Aktif filtre etiketleri --- */
  function aktifFiltreleriCiz() {
    const kutu = $("haberAktifFiltreler");
    kutu.textContent = "";
    const etiketler = [];
    const ekle = (metin, kaldir) => etiketler.push(aktifFiltreEtiketiOlustur(metin, () => { kaldir(); rozetGuncelle(); ciz(); }));

    if (state.arama) ekle(`Arama: ${state.arama}`, () => { arama.value = ""; state.arama = ""; });
    if (tarih.durum.deger) ekle(`Tarih: ${tarih.etiket(tarih.durum.deger)}`, () => tarih.temizle());
    [...kategori.secili].forEach((d) => ekle(`Tür: ${kategori.etiket(d)}`, () => kategori.sil(d)));
    [...ulke.secili].forEach((d) => ekle(`Ülke: ${ulke.etiket(d)}`, () => ulke.sil(d)));
    [...sinif.secili].forEach((d) => ekle(`Önem: ${sinif.etiket(d)}`, () => sinif.sil(d)));
    if (saatSeciliMi()) {
      const bas = saatBaslangic.value !== "" ? `${saatBaslangic.value}:00` : "00:00";
      const bit = saatBitis.value !== "" ? `${saatBitis.value}:00` : "23:59";
      ekle(`Saat: ${bas}–${bit}`, () => { saatBaslangic.value = ""; saatBitis.value = ""; });
    }
    etiketler.forEach((e) => kutu.appendChild(e));
    if (etiketler.length >= 2) {
      const temizle = butonOlustur("Tümünü temizle", "filter-tag filter-tag-temizle", tumFiltreleriTemizle);
      kutu.appendChild(temizle);
    }
    kutu.hidden = etiketler.length === 0;
  }

  /* --- Durum satırı: son tarama, sayılar, veri kaynağı dağılımı --- */
  function durumSatiriYaz() {
    const kutu = $("haberSonTarama");
    kutu.textContent = "";
    const chip = (etiket, deger, ekSinif) => {
      const c = document.createElement("span");
      c.className = "status-chip" + (ekSinif ? ` ${ekSinif}` : "");
      c.appendChild(document.createTextNode(`${etiket} `));
      if (deger != null) {
        const b = document.createElement("b");
        b.textContent = deger;
        c.appendChild(b);
      }
      return c;
    };

    if (state.sonTarama) {
      const t = new Date(state.sonTarama);
      const ilk = document.createElement("span");
      ilk.appendChild(document.createTextNode("Son tarama: "));
      const zaman = document.createElement("time");
      zaman.dateTime = t.toISOString();
      zaman.textContent = kisaZaman(t);
      ilk.appendChild(zaman);
      ilk.appendChild(document.createTextNode(` (${goreliZaman(t)})`));
      kutu.appendChild(ilk);
      const gecikmeDk = (Date.now() - t.getTime()) / 60000;
      if (gecikmeDk > TARAMA_GECIKME_UYARISI_DK) {
        kutu.appendChild(chip("Tarama gecikmiş olabilir", null, "status-uyari"));
      }
    } else if (state.yuklendi) {
      kutu.appendChild(document.createTextNode("Henüz tarama yapılmadı."));
    }

    if (state.yuklendi) {
      const toplam = state.tumOgeler.length;
      kutu.appendChild(chip("Gösterilen", state.gosterilen === toplam ? String(toplam) : `${state.gosterilen} / ${toplam}`));
      const say = {};
      state.tumOgeler.forEach((h) => { const k = ulkeKodu(h); say[k] = (say[k] || 0) + 1; });
      [...ULKE_SIRA, ...Object.keys(say).filter((k) => !ULKE_SIRA.includes(k))].forEach((k) => {
        if (say[k]) kutu.appendChild(chip(ulkeEtiketi(k), String(say[k])));
      });
    }

    if (state.hata) {
      const c = chip("Güncelleme başarısız:", null, "status-uyari");
      c.appendChild(document.createTextNode(state.hata.mesaj + " "));
      c.appendChild(butonOlustur("Tekrar dene", "filter-tag filter-tag-temizle", () => yenile(false)));
      kutu.appendChild(c);
    }
  }

  function ciz() {
    const kutu = $("haberListe");
    aktifFiltreleriCiz();

    if (!state.yuklendi) return;
    const gorulen = filtrele();
    state.gosterilen = gorulen.length;
    durumSatiriYaz();
    $("haberSonuc").textContent = `${gorulen.length} haber gösteriliyor.`;

    $("haberGunOzetBtn").disabled = state.tumOgeler.length === 0;

    kutu.innerHTML = "";
    kutu.setAttribute("aria-busy", "false");
    if (gorulen.length === 0) {
      if (state.tumOgeler.length) {
        bosDurumCiz("haberListe", "Seçilen filtre/aramayla eşleşen içerik yok.", [
          { metin: "Filtreleri temizle", tikla: tumFiltreleriTemizle, birincil: true },
        ]);
      } else {
        bosDurumCiz("haberListe", 'Henüz haber yok. Arka plan taraması ilk sonuçları getirdiğinde burada görünecek.\n"Yenile"ye basarak da kontrol edebilirsin.', [
          { metin: "Yenile", tikla: () => yenile(false), birincil: true },
        ]);
      }
      return;
    }
    const parca = document.createDocumentFragment();
    gorulen.forEach((h, i) => parca.appendChild(kartOlustur(h, i, detayGoster)));
    kutu.appendChild(parca);
  }

  let aramaZamanlayici = null;
  arama.addEventListener("input", () => {
    state.arama = arama.value.trim();
    clearTimeout(aramaZamanlayici);
    aramaZamanlayici = setTimeout(ciz, 120);
  });

  $("haberGunOzetBtn").addEventListener("click", (e) => gunuOzetle(e.currentTarget));

  async function yenile(sessiz = false) {
    if (state.iptal) state.iptal.abort();
    const ac = new AbortController();
    state.iptal = ac;
    const zaman = setTimeout(() => ac.abort(), HABER_ISTEK_ZAMAN_ASIMI_MS);

    if (!sessiz) ustProgres(true);
    if (!state.yuklendi) kartIskeletleriCiz("haberListe");
    try {
      const resp = await fetch("/api/haberler", { signal: ac.signal });
      let yanit = null;
      try { yanit = await resp.json(); } catch (e) { yanit = null; }
      if (!resp.ok || !yanit || !yanit.ok) throw { durum: resp.status || 500 };

      state.tumOgeler = yanit.haberler || [];
      state.sonTarama = yanit.sonTarama || null;
      state.hata = null;
      const imza = listeImzasi(state.tumOgeler);
      const ilk = !state.yuklendi;
      state.yuklendi = true;
      if (ilk || imza !== state.imza || !sessiz) {
        state.imza = imza;
        ciz();
      } else {
        durumSatiriYaz(); // liste aynı: kartlara dokunma (odak/kaydırma korunur), yalnızca zaman bilgisi tazelenir
      }
    } catch (e) {
      if (state.iptal !== ac) return; // daha yeni bir istek bunun yerine geçti
      const bilgi = haberHatasiBilgisi(e);
      if (!state.yuklendi) {
        bosDurumCiz("haberListe", bilgi.mesaj, [{ metin: "Tekrar dene", tikla: () => yenile(false), birincil: true }]);
        $("haberSonTarama").textContent = "Haberler alınamadı.";
      } else {
        state.hata = bilgi;
        durumSatiriYaz();
      }
      if (!sessiz) toast(bilgi.mesaj, bilgi.tur);
    } finally {
      clearTimeout(zaman);
      if (!sessiz) ustProgres(false);
    }
  }

  $("haberYenileBtn").addEventListener("click", () => yenile(false));

  return yenile;
}

/* ===================== Kaydedilenler paneli ===================== */
function kayitliPaneliOlustur() {
  const state = { arama: "" };

  const kategori = cokluSecimGrubuBaslat(
    document.querySelectorAll("#kayitliKategoriChips .chip"), "kategori", () => { rozetGuncelle(); ciz(); }
  );
  const ulke = cokluSecimGrubuBaslat(
    document.querySelectorAll("#kayitliUlkeChips .chip"), "ulke", () => { rozetGuncelle(); ciz(); }
  );
  const sinif = cokluSecimGrubuBaslat(
    document.querySelectorAll("#kayitliChips .chip"), "sinif", () => { rozetGuncelle(); ciz(); }
  );

  filtrePopoverBaslat("kayitliFiltreBtn", "kayitliFiltrePopover");

  function rozetGuncelle() {
    filtreRozetGuncelle("kayitliFiltreRozet", kategori.secili.size + ulke.secili.size + sinif.secili.size);
  }

  function filtreleriTemizle() {
    kategori.temizle();
    ulke.temizle();
    sinif.temizle();
    rozetGuncelle();
    ciz();
  }
  $("kayitliFiltreTemizleBtn").addEventListener("click", filtreleriTemizle);

  const aramaInput = $("kayitliArama");

  function ciz() {
    const liste = kaydedilenleriYukle();
    const kutu = $("kayitliListe");
    kutu.innerHTML = "";

    let gorulen = liste;
    if (sinif.secili.size > 0) gorulen = gorulen.filter((h) => sinif.secili.has(h.sinif));
    if (kategori.secili.size > 0) gorulen = gorulen.filter((h) => kategori.secili.has(h.kategori));
    if (ulke.secili.size > 0) gorulen = gorulen.filter((h) => ulke.secili.has(ulkeKodu(h)));
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
      if (liste.length) {
        bosDurumCiz("kayitliListe", "Seçilen filtre/aramayla eşleşen kayıt yok.", [
          { metin: "Filtreleri temizle", tikla: () => { aramaInput.value = ""; state.arama = ""; filtreleriTemizle(); }, birincil: true },
        ]);
      } else {
        bosDurumCiz("kayitliListe", 'Henüz kaydedilen bir haber yok.\nBir habere tıklayıp açılan pencereden "Kaydet" diyebilirsin.');
      }
      return;
    }
    gorulen.forEach((h, i) => kutu.appendChild(kartOlustur(h, i, detayGoster)));
  }

  if (aramaInput) {
    aramaInput.addEventListener("input", () => {
      state.arama = aramaInput.value.trim();
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

  return ciz;
}

/* ===================== Başlat ===================== */
function baslat() {
  $("toastStack").setAttribute("aria-live", "polite");
  temaBaslat();
  ayarlarBaslat();
  gunOzetiModalBaslat();

  const kayitliYenile = kayitliPaneliOlustur();
  detayModalBaslat(kayitliYenile);

  sekmeBaslat((sekme) => {
    if (sekme === "kaydedilenler") kayitliYenile();
    if (typeof PiyasaGorunumu !== "undefined") PiyasaGorunumu.gorunurluk(sekme === "haberler");
  });

  if (typeof PiyasaGorunumu !== "undefined") PiyasaGorunumu.baslat();

  const haberYenile = haberPaneliOlustur();
  haberYenile(false);

  setInterval(() => { if (!document.hidden) haberYenile(true); }, OTOMATIK_YENILEME_MS);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) haberYenile(true); });
}

baslat();
