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

/* ===================== Çoklu / tekli seçim çip grupları ===================== */
function cokluSecimGrubuBaslat(chips, attr, degisinceCB) {
  const secili = new Set();

  function gorunumGuncelle() {
    chips.forEach((chip) => {
      const deger = chip.dataset[attr];
      chip.classList.toggle("is-active", deger ? secili.has(deger) : secili.size === 0);
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
  return { secili, gorunumGuncelle, temizle: () => { secili.clear(); gorunumGuncelle(); } };
}

function tekliSecimGrubuBaslat(chips, attr, degisinceCB) {
  const durum = { deger: "" };

  function gorunumGuncelle() {
    chips.forEach((chip) => chip.classList.toggle("is-active", (chip.dataset[attr] || "") === durum.deger));
  }

  chips.forEach((chip) => {
    chip.addEventListener("click", () => {
      durum.deger = chip.dataset[attr] || "";
      gorunumGuncelle();
      degisinceCB();
    });
  });

  gorunumGuncelle();
  return { durum, gorunumGuncelle, temizle: () => { durum.deger = ""; gorunumGuncelle(); } };
}

/* ===================== Filtreler popover ===================== */
function filtrePopoverBaslat(btnId, popoverId) {
  const btn = $(btnId);
  const popover = $(popoverId);

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const aciliyor = !popover.classList.contains("is-open");
    document.querySelectorAll(".filter-popover.is-open").forEach((p) => p.classList.remove("is-open"));
    if (aciliyor) popover.classList.add("is-open");
  });
  popover.addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", () => popover.classList.remove("is-open"));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") popover.classList.remove("is-open");
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
/* ===================== Bildirim e-postaları + esikler ===================== */
const SINIF_ESIK_SIRA = [
  ["cok_onemli", "Çok Önemli"],
  ["onemli", "Önemli"],
  ["bakmaya_deger", "Bakmaya Değer"],
];
const VARSAYILAN_ORTAK_ESIK = {
  cok_onemli: { aktif: true, esik: 15 },
  onemli: { aktif: true, esik: 60 },
  bakmaya_deger: { aktif: true, esik: 180 },
};
let mevcutAyarlar = { ortak_esik: JSON.parse(JSON.stringify(VARSAYILAN_ORTAK_ESIK)), alicilar: [] };

function esikGirdileriniOku(kapsam) {
  const esik = {};
  kapsam.querySelectorAll("input[data-sinif]").forEach((inp) => {
    const sinif = inp.dataset.sinif;
    const aktifCB = kapsam.querySelector(`input[data-sinif-aktif="${sinif}"]`);
    esik[sinif] = { aktif: aktifCB ? aktifCB.checked : true, esik: Math.max(1, parseInt(inp.value, 10) || 1) };
  });
  return esik;
}

function esikInputSatiriOlustur(deger) {
  const satir = document.createElement("div");
  satir.className = "esik-input-row";
  SINIF_ESIK_SIRA.forEach(([sinif, etiket]) => {
    const alan = document.createElement("div");
    alan.className = "esik-input-field";

    const lbl = document.createElement("label");
    lbl.className = "esik-aktif-toggle";
    const aktifCB = document.createElement("input");
    aktifCB.type = "checkbox";
    aktifCB.checked = deger[sinif].aktif !== false;
    aktifCB.dataset.sinifAktif = sinif;
    lbl.appendChild(aktifCB);
    lbl.appendChild(document.createTextNode(" " + etiket));
    alan.appendChild(lbl);

    const inp = document.createElement("input");
    inp.type = "number";
    inp.min = "1";
    inp.value = deger[sinif].esik;
    inp.dataset.sinif = sinif;
    inp.disabled = !aktifCB.checked;
    aktifCB.addEventListener("change", () => { inp.disabled = !aktifCB.checked; });
    alan.appendChild(inp);

    satir.appendChild(alan);
  });
  return satir;
}

function esikOzetMetni(esik) {
  return SINIF_ESIK_SIRA.map(([sinif, etiket]) => {
    const veri = esik[sinif];
    return `${etiket}: ${veri.aktif !== false ? veri.esik : "Kapalı"}`;
  }).join(", ");
}

function ortakEsikInputlariniDoldur() {
  $("ortakAktifCokOnemli").checked = mevcutAyarlar.ortak_esik.cok_onemli.aktif !== false;
  $("ortakEsikCokOnemli").value = mevcutAyarlar.ortak_esik.cok_onemli.esik;
  $("ortakEsikCokOnemli").disabled = !$("ortakAktifCokOnemli").checked;
  $("ortakAktifOnemli").checked = mevcutAyarlar.ortak_esik.onemli.aktif !== false;
  $("ortakEsikOnemli").value = mevcutAyarlar.ortak_esik.onemli.esik;
  $("ortakEsikOnemli").disabled = !$("ortakAktifOnemli").checked;
  $("ortakAktifBakmayaDeger").checked = mevcutAyarlar.ortak_esik.bakmaya_deger.aktif !== false;
  $("ortakEsikBakmayaDeger").value = mevcutAyarlar.ortak_esik.bakmaya_deger.esik;
  $("ortakEsikBakmayaDeger").disabled = !$("ortakAktifBakmayaDeger").checked;
}

function aliciListesiCiz() {
  const kutu = $("epostaListesi");
  kutu.innerHTML = "";

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

    const ozelLabel = document.createElement("label");
    ozelLabel.className = "ozel-esik-toggle";
    const ozelCheckbox = document.createElement("input");
    ozelCheckbox.type = "checkbox";
    ozelCheckbox.checked = !!alici.esik;
    ozelLabel.appendChild(ozelCheckbox);
    ozelLabel.appendChild(document.createTextNode(" Özel eşik"));
    bas.appendChild(ozelLabel);

    const silBtn = document.createElement("span");
    silBtn.className = "alici-sil-btn";
    silBtn.textContent = "✕";
    silBtn.title = "Kaldır";
    silBtn.addEventListener("click", () => {
      mevcutAyarlar.alicilar.splice(index, 1);
      ayarlariKaydet("E-posta kaldırıldı.");
    });
    bas.appendChild(silBtn);

    satir.appendChild(bas);

    const girdiSatiri = esikInputSatiriOlustur(alici.esik || mevcutAyarlar.ortak_esik);
    girdiSatiri.style.display = alici.esik ? "flex" : "none";
    girdiSatiri.style.marginTop = "10px";
    satir.appendChild(girdiSatiri);

    const ortakBilgi = document.createElement("p");
    ortakBilgi.className = "modal-hint";
    ortakBilgi.style.margin = "8px 0 0";
    ortakBilgi.style.display = alici.esik ? "none" : "block";
    ortakBilgi.textContent = `Ortak ayarı kullanıyor: ${esikOzetMetni(mevcutAyarlar.ortak_esik)}`;
    satir.appendChild(ortakBilgi);

    const kaydetBtn = document.createElement("button");
    kaydetBtn.className = "btn btn-outline btn-sm";
    kaydetBtn.style.marginTop = "8px";
    kaydetBtn.style.display = alici.esik ? "inline-flex" : "none";
    kaydetBtn.textContent = "Kaydet";
    kaydetBtn.addEventListener("click", () => {
      mevcutAyarlar.alicilar[index].esik = esikGirdileriniOku(girdiSatiri);
      ayarlariKaydet("Eşik güncellendi.");
    });
    satir.appendChild(kaydetBtn);

    ozelCheckbox.addEventListener("change", () => {
      if (ozelCheckbox.checked) {
        girdiSatiri.style.display = "flex";
        kaydetBtn.style.display = "inline-flex";
        ortakBilgi.style.display = "none";
      } else {
        girdiSatiri.style.display = "none";
        kaydetBtn.style.display = "none";
        ortakBilgi.style.display = "block";
        mevcutAyarlar.alicilar[index].esik = null;
        ayarlariKaydet("Ortak ayara geçildi.");
      }
    });

    kutu.appendChild(satir);
  });
}

async function ayarlarYukle() {
  try {
    const resp = await fetch("/api/ayarlar");
    const yanit = await resp.json();
    if (!yanit.ok) { toast(yanit.hata || "Ayarlar alınamadı.", "error"); return; }

    mevcutAyarlar = {
      ortak_esik: yanit.ayarlar.ortak_esik || JSON.parse(JSON.stringify(VARSAYILAN_ORTAK_ESIK)),
      alicilar: yanit.ayarlar.alicilar || [],
    };

    ortakEsikInputlariniDoldur();
    aliciListesiCiz();

    if (yanit.ayarlar.sonTestEpostasi) {
      const bitis = new Date(yanit.ayarlar.sonTestEpostasi).getTime() + TEST_EPOSTA_BEKLEME_MS;
      testEpostaBekleyenBitis = Math.max(testEpostaBekleyenBitis, bitis);
    }
    testEpostaDurumGuncelle();
  } catch (e) {
    toast("Ayarlar alınamadı: " + e, "error");
  }
}

async function ayarlariKaydet(basariMesaji) {
  try {
    const resp = await fetch("/api/ayarlar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(mevcutAyarlar),
    });
    const yanit = await resp.json();
    if (yanit.ok) {
      mevcutAyarlar = yanit.ayarlar;
      ortakEsikInputlariniDoldur();
      aliciListesiCiz();
      if (basariMesaji) toast(basariMesaji, "ok");
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

/* ===================== Deneme e-postası (iki adımlı onay + 10 dk bekleme) ===================== */
const TEST_EPOSTA_BEKLEME_MS = 10 * 60 * 1000;
let testEpostaBekleyenBitis = 0;

function testEpostaDurumGuncelle() {
  const simdi = Date.now();
  const btn = $("testEpostaBtn");
  const durum = $("testEpostaDurum");
  if (testEpostaBekleyenBitis > simdi) {
    btn.disabled = true;
    const kalanDk = Math.ceil((testEpostaBekleyenBitis - simdi) / 60000);
    durum.textContent = `Tekrar göndermek için ${kalanDk} dakika bekleyin.`;
    durum.style.display = "block";
  } else {
    btn.disabled = false;
    durum.style.display = "none";
  }
}

function testEpostaBaslat() {
  $("testEpostaBtn").addEventListener("click", () => {
    if ($("testEpostaBtn").disabled) return;
    $("testEpostaBtn").style.display = "none";
    $("testEpostaOnay").style.display = "block";
  });

  $("testEpostaVazgecBtn").addEventListener("click", () => {
    $("testEpostaOnay").style.display = "none";
    $("testEpostaBtn").style.display = "inline-flex";
  });

  $("testEpostaIlerleBtn").addEventListener("click", async () => {
    const ilerleBtn = $("testEpostaIlerleBtn");
    ilerleBtn.disabled = true;
    ilerleBtn.textContent = "Gönderiliyor...";

    try {
      const resp = await fetch("/api/test-eposta", { method: "POST" });
      const yanit = await resp.json();
      if (yanit.ok) {
        toast(`Deneme e-postası ${yanit.gonderilenSayisi} adrese gönderildi.`, "ok");
        testEpostaBekleyenBitis = Date.now() + TEST_EPOSTA_BEKLEME_MS;
      } else {
        toast(yanit.hata || "Deneme e-postası gönderilemedi.", "error");
        if (yanit.kalanSaniye) testEpostaBekleyenBitis = Date.now() + yanit.kalanSaniye * 1000;
      }
    } catch (e) {
      toast("Beklenmeyen hata: " + e, "error");
    } finally {
      ilerleBtn.disabled = false;
      ilerleBtn.textContent = "İlerle";
      $("testEpostaOnay").style.display = "none";
      $("testEpostaBtn").style.display = "inline-flex";
      testEpostaDurumGuncelle();
    }
  });
}

function ayarlarBaslat() {
  $("openSettings").addEventListener("click", () => {
    $("settingsOverlay").classList.add("is-open");
    ayarlarYukle();
  });
  $("closeSettings").addEventListener("click", () => $("settingsOverlay").classList.remove("is-open"));

  $("ortakAktifCokOnemli").addEventListener("change", () => { $("ortakEsikCokOnemli").disabled = !$("ortakAktifCokOnemli").checked; });
  $("ortakAktifOnemli").addEventListener("change", () => { $("ortakEsikOnemli").disabled = !$("ortakAktifOnemli").checked; });
  $("ortakAktifBakmayaDeger").addEventListener("change", () => { $("ortakEsikBakmayaDeger").disabled = !$("ortakAktifBakmayaDeger").checked; });
  $("settingsOverlay").addEventListener("click", (e) => {
    if (e.target === $("settingsOverlay")) $("settingsOverlay").classList.remove("is-open");
  });

  function epostaEkle() {
    const input = $("epostaInput");
    const deger = input.value.trim();
    if (!deger) return;
    if (!epostaGecerliMi(deger)) { toast("Geçerli bir e-posta adresi gir.", "warn"); return; }
    if (mevcutAyarlar.alicilar.some((a) => a.eposta === deger)) { toast("Bu e-posta zaten ekli.", "warn"); return; }

    mevcutAyarlar.alicilar.push({ eposta: deger, esik: null });
    ayarlariKaydet("E-posta eklendi.");
    input.value = "";
  }

  $("epostaEkleBtn").addEventListener("click", epostaEkle);
  $("epostaInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") epostaEkle();
  });

  $("ortakEsikKaydetBtn").addEventListener("click", () => {
    mevcutAyarlar.ortak_esik = {
      cok_onemli: {
        aktif: $("ortakAktifCokOnemli").checked,
        esik: Math.max(1, parseInt($("ortakEsikCokOnemli").value, 10) || VARSAYILAN_ORTAK_ESIK.cok_onemli.esik),
      },
      onemli: {
        aktif: $("ortakAktifOnemli").checked,
        esik: Math.max(1, parseInt($("ortakEsikOnemli").value, 10) || VARSAYILAN_ORTAK_ESIK.onemli.esik),
      },
      bakmaya_deger: {
        aktif: $("ortakAktifBakmayaDeger").checked,
        esik: Math.max(1, parseInt($("ortakEsikBakmayaDeger").value, 10) || VARSAYILAN_ORTAK_ESIK.bakmaya_deger.esik),
      },
    };
    ayarlariKaydet("Ortak eşik güncellendi.");
  });

  testEpostaBaslat();
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

/* ===================== Haberler paneli (filtreleme + manuel tarama) ===================== */
function haberPaneliOlustur() {
  const state = { tumOgeler: [], arama: "" };

  const tarih = tekliSecimGrubuBaslat(
    document.querySelectorAll("#haberTarihChips .chip"), "tarih", () => { rozetGuncelle(); ciz(); }
  );
  const kategori = cokluSecimGrubuBaslat(
    document.querySelectorAll("#haberKategoriChips .chip"), "kategori", () => { rozetGuncelle(); ciz(); }
  );
  const sinif = cokluSecimGrubuBaslat(
    document.querySelectorAll("#haberChips .chip"), "sinif", () => { rozetGuncelle(); ciz(); }
  );

  filtrePopoverBaslat("haberFiltreBtn", "haberFiltrePopover");

  function rozetGuncelle() {
    const sayac = (tarih.durum.deger ? 1 : 0) + kategori.secili.size + sinif.secili.size;
    filtreRozetGuncelle("haberFiltreRozet", sayac);
  }

  $("haberFiltreTemizleBtn").addEventListener("click", () => {
    tarih.temizle();
    kategori.temizle();
    sinif.temizle();
    rozetGuncelle();
    ciz();
  });

  const arama = $("haberArama");

  function filtrele() {
    let gorulen = state.tumOgeler;
    if (sinif.secili.size > 0) gorulen = gorulen.filter((h) => sinif.secili.has(h.sinif));
    if (kategori.secili.size > 0) gorulen = gorulen.filter((h) => kategori.secili.has(h.kategori));
    if (tarih.durum.deger === "bugun") gorulen = gorulen.filter((h) => h.tarih === bugununTarihi());
    if (tarih.durum.deger === "dun") gorulen = gorulen.filter((h) => h.tarih === dununTarihi());
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
          : 'Henüz haber yok. Arka plan taraması ilk sonuçları getirdiğinde burada görünecek.<br>"Şimdi Tara" ya da "Yenile"ye basarak da kontrol edebilirsin.'
      );
      return;
    }
    gorulen.forEach((h, i) => kutu.appendChild(kartOlustur(h, i, detayGoster)));
  }

  arama.addEventListener("input", () => {
    state.arama = arama.value.trim();
    ciz();
  });

  $("haberKaydetBtn").addEventListener("click", () => {
    indirMarkdown(filtrele(), "Finviz Haberleri", "haberler");
  });

  $("haberGunOzetBtn").addEventListener("click", gunuOzetle);

  async function yenile(sessiz = false) {
    if (!sessiz) ustProgres(true);
    try {
      const resp = await fetch("/api/haberler");
      const yanit = await resp.json();
      if (!yanit.ok) {
        if (!sessiz) toast(yanit.hata || "Haberler alınamadı.", "error");
        return;
      }
      state.tumOgeler = yanit.haberler || [];
      ciz();

      if (yanit.sonTarama) {
        const t = new Date(yanit.sonTarama);
        $("haberSonTarama").textContent = `Sistem arka planda otomatik olarak Finviz'i tarıyor. Son tarama: ${t.toLocaleString("tr-TR")}`;
      }
    } catch (e) {
      if (!sessiz) toast("Beklenmeyen hata: " + e, "error");
    } finally {
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
  const sinif = cokluSecimGrubuBaslat(
    document.querySelectorAll("#kayitliChips .chip"), "sinif", () => { rozetGuncelle(); ciz(); }
  );

  filtrePopoverBaslat("kayitliFiltreBtn", "kayitliFiltrePopover");

  function rozetGuncelle() {
    filtreRozetGuncelle("kayitliFiltreRozet", kategori.secili.size + sinif.secili.size);
  }

  $("kayitliFiltreTemizleBtn").addEventListener("click", () => {
    kategori.temizle();
    sinif.temizle();
    rozetGuncelle();
    ciz();
  });

  const aramaInput = $("kayitliArama");

  function ciz() {
    const liste = kaydedilenleriYukle();
    const kutu = $("kayitliListe");
    kutu.innerHTML = "";

    $("kayitliKaydetBtn").disabled = liste.length === 0;

    let gorulen = liste;
    if (sinif.secili.size > 0) gorulen = gorulen.filter((h) => sinif.secili.has(h.sinif));
    if (kategori.secili.size > 0) gorulen = gorulen.filter((h) => kategori.secili.has(h.kategori));
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
