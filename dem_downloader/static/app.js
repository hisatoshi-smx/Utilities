"use strict";

// ============================================================================
// ユーティリティ
// ============================================================================
const $ = (id) => document.getElementById(id);

async function api(url, opts) {
  const res = await fetch(url, opts);
  let data = {};
  try { data = await res.json(); } catch (_) { /* 本文なし */ }
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}
const post = (url, body) =>
  api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
function fmtDur(sec) {
  if (sec == null || !isFinite(sec)) return "--";
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const mm = String(m).padStart(2, "0"), ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}
function fmtBytes(b) {
  if (b < 1e6) return `${(b / 1e3).toFixed(0)} KB`;
  if (b < 1e9) return `${(b / 1e6).toFixed(1)} MB`;
  return `${(b / 1e9).toFixed(2)} GB`;
}
function fmtNum(v) { return Number(v).toPrecision(4).replace(/\.?0+$/, ""); }
function store(key, val) { try { localStorage.setItem("demdl." + key, val); } catch (_) {} }
function load(key, def) { try { return localStorage.getItem("demdl." + key) ?? def; } catch (_) { return def; } }
function setInfo(el, text, kind) {
  el.textContent = text;
  el.classList.toggle("error", kind === "error");
  el.classList.toggle("warn", kind === "warn");
}

// ============================================================================
// 状態
// ============================================================================
const state = {
  roi: null,            // {west, south, east, north}
  ref: null,            // /api/inspect の結果
  crs: { value: null, label: "", auto: true, unit: "m", geographic: false },
  est: null,            // 直近の見積もり結果
  config: null,
  jobId: null,
  logNext: 0,
  pollTimer: null,
};

// ============================================================================
// 地図
// ============================================================================
const gsiAttr = '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank">地理院タイル</a>';
const gsiLayer = (name, ext, native) =>
  L.tileLayer(`https://cyberjapandata.gsi.go.jp/xyz/${name}/{z}/{x}/{y}.${ext}`,
    { attribution: gsiAttr, maxZoom: 19, maxNativeZoom: native });

const baseLayers = {
  "地理院 標準地図": gsiLayer("std", "png", 18),
  "地理院 空中写真": gsiLayer("seamlessphoto", "jpg", 18),
  "地理院 色別標高図": gsiLayer("relief", "png", 15),
  "地理院 淡色地図": gsiLayer("pale", "png", 18),
  "OpenStreetMap": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    { attribution: "© OpenStreetMap contributors", maxZoom: 19 }),
};

const map = L.map("map", { layers: [baseLayers["地理院 標準地図"]] }).setView([36.2, 138.2], 6);
L.control.layers(baseLayers, null, { position: "topright" }).addTo(map);
L.control.scale({ imperial: false }).addTo(map);

const drawn = new L.FeatureGroup().addTo(map);
let refLayer = null;
const roiStyle = { color: "#e11d48", weight: 2, fillOpacity: 0.12 };
const drawOptions = { rectangle: { showArea: false, shapeOptions: roiStyle } };

const drawControl = new L.Control.Draw({
  position: "topleft",
  draw: { polygon: false, polyline: false, circle: false, marker: false, circlemarker: false, ...drawOptions },
  edit: { featureGroup: drawn, remove: true },
});
map.addControl(drawControl);

map.on(L.Draw.Event.CREATED, (e) => {
  drawn.clearLayers();
  drawn.addLayer(e.layer);
  setRoiFromBounds(e.layer.getBounds(), { fromMap: true });
});
map.on(L.Draw.Event.EDITED, () => {
  const layers = drawn.getLayers();
  if (layers.length) setRoiFromBounds(layers[0].getBounds(), { fromMap: true });
});
map.on(L.Draw.Event.DELETED, () => { if (!drawn.getLayers().length) clearRoi(); });

$("drawRect").onclick = () => new L.Draw.Rectangle(map, drawOptions.rectangle).enable();

// ============================================================================
// ROI
// ============================================================================
const roiInputs = ["north", "west", "east", "south"];

function setRoi(roi, { fit = false, fromMap = false } = {}) {
  state.roi = roi;
  const r = (v) => Number(v.toFixed(7));
  $("west").value = r(roi.west); $("east").value = r(roi.east);
  $("south").value = r(roi.south); $("north").value = r(roi.north);
  if (!fromMap) {
    drawn.clearLayers();
    L.rectangle([[roi.south, roi.west], [roi.north, roi.east]], roiStyle).addTo(drawn);
  }
  if (fit) map.fitBounds([[roi.south, roi.west], [roi.north, roi.east]], { padding: [40, 40], maxZoom: 17 });
  suggestCrs();
  scheduleEstimate();
}
function setRoiFromBounds(b, opts) {
  const clampLon = (x) => Math.max(-180, Math.min(180, x));
  setRoi({ west: clampLon(b.getWest()), east: clampLon(b.getEast()), south: b.getSouth(), north: b.getNorth() }, opts);
}
function clearRoi() {
  state.roi = null;
  drawn.clearLayers();
  roiInputs.forEach((id) => ($(id).value = ""));
  scheduleEstimate();
}
function readRoiInputs() {
  const v = {};
  for (const id of roiInputs) {
    const x = parseFloat($(id).value);
    if (!isFinite(x)) return null;
    v[id] = x;
  }
  return v;
}
roiInputs.forEach((id) => $(id).addEventListener("change", () => {
  const v = readRoiInputs();
  if (!v) { state.roi = null; scheduleEstimate(); return; }
  if (v.west >= v.east || v.south >= v.north) {
    state.roi = null;
    setInfo($("estimate"), "ROI の値が不正です（西 < 東、南 < 北 になるよう入力してください）。", "error");
    $("runBtn").disabled = true;
    return;
  }
  setRoi(v, { fit: true });
}));
$("roiClear").onclick = clearRoi;
$("roiFromRef").onclick = () => {
  if (!state.ref) return alert("先に参照ファイルを読み込んでください。");
  const [w, s, e, n] = state.ref.bounds_wgs84;
  setRoi({ west: w, south: s, east: e, north: n }, { fit: true });
};

// --- 地名検索 (Nominatim 経由) ---
const placeSearch = $("placeSearch"), placeList = $("placeList");
let placeReq = 0, placeItems = [], placeActive = -1;

function renderPlaceList(items) {
  placeList.textContent = "";
  placeItems = [];
  if (!items.length) {
    const d = document.createElement("div");
    d.className = "combo-empty";
    d.textContent = "該当する場所がありません";
    placeList.appendChild(d);
    return;
  }
  for (const it of items) {
    const d = document.createElement("div");
    d.className = "combo-item place-item";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = it.label;
    d.append(name);
    d.title = it.label;
    d.addEventListener("mousedown", (ev) => { ev.preventDefault(); choosePlace(it); });
    placeList.appendChild(d);
    placeItems.push({ it, el: d });
  }
  placeActive = -1;
}

async function searchPlaces(q) {
  const my = ++placeReq;
  try {
    const items = await api("/api/geocode?q=" + encodeURIComponent(q) + "&limit=8");
    if (my !== placeReq) return;
    renderPlaceList(items);
    placeList.hidden = false;
  } catch (e) {
    placeList.textContent = "";
    const d = document.createElement("div");
    d.className = "combo-empty";
    d.textContent = "検索に失敗しました: " + e.message;
    placeList.appendChild(d);
    placeList.hidden = false;
  }
}
const searchPlacesDebounced = debounce(searchPlaces, 350);

function choosePlace(it) {
  placeList.hidden = true;
  placeSearch.value = it.label;
  const b = it.bounds;
  if ($("useRefGrid").checked) $("useRefGrid").checked = false, applyRefGridMode();
  setRoi({ west: b.west, south: b.south, east: b.east, north: b.north }, { fit: true });
  placeSearch.blur();
}

placeSearch.addEventListener("focus", () => {
  const q = placeSearch.value.trim();
  if (q.length >= 2) searchPlaces(q);
});
placeSearch.addEventListener("input", () => {
  const q = placeSearch.value.trim();
  if (q.length < 2) { placeList.hidden = true; return; }
  searchPlacesDebounced(q);
});
placeSearch.addEventListener("blur", () => { placeList.hidden = true; });
placeSearch.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    ev.preventDefault();
    if (!placeList.hidden && placeItems.length) choosePlace(placeItems[Math.max(0, placeActive)].it);
    else if (placeSearch.value.trim().length >= 2) searchPlaces(placeSearch.value.trim());
    return;
  }
  if (!placeItems.length || placeList.hidden) return;
  if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
    ev.preventDefault();
    placeActive = (placeActive + (ev.key === "ArrowDown" ? 1 : -1) + placeItems.length) % placeItems.length;
    placeItems.forEach((c, i) => c.el.classList.toggle("active", i === placeActive));
    placeItems[placeActive].el.scrollIntoView({ block: "nearest" });
  } else if (ev.key === "Escape") {
    placeList.hidden = true;
  }
});

// ============================================================================
// 座標系コンボボックス（検索付きプルダウン）
// ============================================================================
const crsInput = $("crsInput"), crsList = $("crsList");
let crsItems = [], crsActive = -1, crsReq = 0;

function renderCrsList(items) {
  crsList.textContent = "";
  crsItems = [];
  const extra = [];
  if (state.ref && state.ref.crs_epsg == null) {
    extra.push({
      code: state.ref.crs_value, short: "参照ファイル", name: state.ref.crs_name,
      label: `参照ファイルの座標系: ${state.ref.crs_name}`, group: "参照ファイル",
    });
  }
  const all = extra.concat(items);
  if (!all.length) {
    const d = document.createElement("div");
    d.className = "combo-empty";
    d.textContent = "該当する座標系がありません";
    crsList.appendChild(d);
    return;
  }
  let lastGroup = null;
  for (const it of all) {
    if (it.group && it.group !== lastGroup) {
      const g = document.createElement("div");
      g.className = "combo-group";
      g.textContent = it.group;
      crsList.appendChild(g);
      lastGroup = it.group;
    }
    const d = document.createElement("div");
    d.className = "combo-item";
    const code = document.createElement("span");
    code.className = "code";
    code.textContent = it.short || it.code;
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = it.name;
    d.append(code, name);
    d.title = it.label;
    d.addEventListener("mousedown", (ev) => { ev.preventDefault(); chooseCrs(it); });
    crsList.appendChild(d);
    crsItems.push({ it, el: d });
  }
  crsActive = -1;
}

async function searchCrs(q) {
  const my = ++crsReq;
  try {
    const items = await api("/api/crs/search?q=" + encodeURIComponent(q));
    if (my !== crsReq) return; // 古い応答は捨てる
    renderCrsList(items);
    crsList.hidden = false;
  } catch (e) {
    crsList.textContent = "";
    const d = document.createElement("div");
    d.className = "combo-empty";
    d.textContent = "検索に失敗しました: " + e.message;
    crsList.appendChild(d);
    crsList.hidden = false;
  }
}
const searchCrsDebounced = debounce(searchCrs, 150);

crsInput.addEventListener("focus", () => { crsInput.select(); searchCrs(""); });
crsInput.addEventListener("input", () => searchCrsDebounced(crsInput.value));
crsInput.addEventListener("blur", () => {
  crsList.hidden = true;
  crsInput.value = state.crs.label; // 選択されなかった入力は元に戻す
});
crsInput.addEventListener("keydown", (ev) => {
  if (crsList.hidden && (ev.key === "ArrowDown" || ev.key === "ArrowUp")) { searchCrs(crsInput.value); return; }
  if (!crsItems.length) return;
  if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
    ev.preventDefault();
    crsActive = (crsActive + (ev.key === "ArrowDown" ? 1 : -1) + crsItems.length) % crsItems.length;
    crsItems.forEach((c, i) => c.el.classList.toggle("active", i === crsActive));
    crsItems[crsActive].el.scrollIntoView({ block: "nearest" });
  } else if (ev.key === "Enter") {
    ev.preventDefault();
    chooseCrs(crsItems[Math.max(0, crsActive)].it);
  } else if (ev.key === "Escape") {
    crsInput.blur();
  }
});

async function chooseCrs(item, { manual = true } = {}) {
  crsList.hidden = true;
  await setCrs(item.code, item.label, manual);
  if (document.activeElement === crsInput) crsInput.blur();
}

async function setCrs(code, label, manual) {
  try {
    const info = await api("/api/crs/info?code=" + encodeURIComponent(code));
    state.crs = {
      value: info.value,
      label: label || `${info.epsg ? "EPSG:" + info.epsg + " — " : ""}${info.name}`,
      auto: !manual,
      unit: info.unit,
      geographic: info.is_geographic,
    };
    crsInput.value = state.crs.label;
    $("gsdUnit").textContent = info.unit;
    setInfo($("crsNote"), info.is_geographic
      ? "地理座標系（度単位）です。GSD は度で指定します（例: 0.00005）。"
      : "");
    scheduleEstimate();
  } catch (e) {
    setInfo($("crsNote"), "座標系を設定できません: " + e.message, "error");
  }
}

// ROI の経度から UTM ゾーンを推定し、利用者が座標系を選んでいなければ仮設定する
function suggestCrs() {
  if (!state.crs.auto || !state.roi || state.ref) return;
  const lon = (state.roi.west + state.roi.east) / 2, lat = (state.roi.south + state.roi.north) / 2;
  let zone = Math.floor((lon + 180) / 6) + 1;
  zone = Math.min(60, Math.max(1, zone));
  const code = (lat >= 0 ? 32600 : 32700) + zone;
  const target = `EPSG:${code}`;
  if (state.crs.value === target) return;
  setCrs(target, null, false).then(() => {
    setInfo($("crsNote"), "ROI の位置から UTM ゾーンを仮設定しました。必要に応じて変更してください。");
  });
}

// ============================================================================
// ファイル / フォルダ選択ダイアログ
// ============================================================================
const fs = { mode: "file", path: "~", onSelect: null };

async function fsLoad(path) {
  const q = new URLSearchParams({
    path, dirs_only: fs.mode === "dir" ? "1" : "0",
    all: $("fsAll").checked ? "1" : "0", hidden: $("fsHidden").checked ? "1" : "0",
  });
  let data;
  try { data = await api("/api/fs?" + q); } catch (e) { $("fsList").textContent = e.message; return; }
  fs.path = data.path;
  $("fsPath").value = data.path;
  $("fsUp").disabled = !data.parent;
  $("fsUp").dataset.parent = data.parent || "";

  const sc = $("fsShortcuts");
  sc.textContent = "";
  for (const s of data.shortcuts) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = s.name;
    b.onclick = () => fsLoad(s.path);
    sc.appendChild(b);
  }

  const list = $("fsList");
  list.textContent = "";
  if (data.error) {
    const d = document.createElement("div"); d.className = "fs-msg"; d.textContent = data.error; list.appendChild(d);
  } else if (!data.entries.length) {
    const d = document.createElement("div"); d.className = "fs-msg";
    d.textContent = fs.mode === "dir" ? "サブフォルダはありません。" : "表示できるファイルがありません（「すべてのファイルを表示」を試してください）。";
    list.appendChild(d);
  }
  for (const e of data.entries) {
    const d = document.createElement("div");
    d.className = "fs-item" + (e.is_dir ? " dir" : "");
    const n = document.createElement("span"); n.textContent = (e.is_dir ? "📁 " : "📄 ") + e.name;
    const s = document.createElement("span"); s.className = "size"; s.textContent = e.is_dir ? "" : fmtBytes(e.size);
    d.append(n, s);
    d.onclick = () => {
      const full = data.path.replace(/\/$/, "") + "/" + e.name;
      if (e.is_dir) fsLoad(full);
      else { closeFs(); fs.onSelect(full); }
    };
    list.appendChild(d);
  }
}
function openFs({ mode, start, title, onSelect }) {
  fs.mode = mode; fs.onSelect = onSelect;
  $("fsTitle").textContent = title;
  $("fsChoose").hidden = mode !== "dir";
  $("fsAllWrap").hidden = mode === "dir";
  $("fsMkRow").hidden = mode !== "dir";   // 新規フォルダは保存先の選択時のみ
  closeMk();
  $("fsModal").hidden = false;
  fsLoad(start || "~");
}

// --- 新規フォルダの作成（現在開いているフォルダの中に作る） ---
function setFsMsg(text) {
  $("fsMsg").textContent = text || "";
  $("fsMsg").hidden = !text;
}
function closeMk() {
  $("fsMkForm").hidden = true;
  $("fsMkBtn").hidden = false;
  setFsMsg("");
}
async function doMkdir() {
  const name = $("fsMkName").value.trim();
  if (!name) { setFsMsg("フォルダ名を入力してください。"); return; }
  try {
    const r = await post("/api/fs/mkdir", { parent: fs.path, name });
    closeMk();
    await fsLoad(r.path);        // 作成したフォルダへ移動する（そのまま「このフォルダを選択」できる）
  } catch (e) {
    setFsMsg(e.message);
  }
}
$("fsMkBtn").onclick = () => {
  $("fsMkBtn").hidden = true;
  $("fsMkForm").hidden = false;
  $("fsMkName").value = "";
  setFsMsg("");
  $("fsMkName").focus();
};
$("fsMkOk").onclick = doMkdir;
$("fsMkCancel").onclick = closeMk;
$("fsMkName").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); doMkdir(); }
  else if (e.key === "Escape") { e.stopPropagation(); closeMk(); }   // ダイアログ全体は閉じない
});
function closeFs() { $("fsModal").hidden = true; }

$("fsUp").onclick = () => { const p = $("fsUp").dataset.parent; if (p) fsLoad(p); };
$("fsGo").onclick = () => fsLoad($("fsPath").value);
$("fsPath").addEventListener("keydown", (e) => { if (e.key === "Enter") fsLoad($("fsPath").value); });
$("fsAll").onchange = $("fsHidden").onchange = () => fsLoad(fs.path);
$("fsCancel").onclick = closeFs;
$("fsChoose").onclick = () => { closeFs(); fs.onSelect(fs.path); };
$("fsModal").addEventListener("mousedown", (e) => { if (e.target === $("fsModal")) closeFs(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("fsModal").hidden) closeFs(); });

// ============================================================================
// 参照ファイル
// ============================================================================
function dirOf(p) { const i = p.lastIndexOf("/"); return i > 0 ? p.slice(0, i) : "~"; }

$("refBrowse").onclick = () => openFs({
  mode: "file", title: "参照ファイルを選択（GeoTIFF / Shapefile / GeoJSON / GPKG など）",
  start: $("refPath").value ? dirOf($("refPath").value) : load("refDir", "~"),
  onSelect: (p) => { $("refPath").value = p; store("refDir", dirOf(p)); loadRef(); },
});
$("refLoad").onclick = loadRef;
$("refPath").addEventListener("keydown", (e) => { if (e.key === "Enter") loadRef(); });
$("refClear").onclick = clearRef;

async function loadRef() {
  const path = $("refPath").value.trim();
  if (!path) return;
  setInfo($("refInfo"), "読み込み中…");
  try {
    const info = await post("/api/inspect", { path });
    state.ref = info;
    if (refLayer) map.removeLayer(refLayer);
    refLayer = L.geoJSON(info.geojson, {
      style: { color: "#2563eb", weight: 2, fillOpacity: 0.06 },
      pointToLayer: (_, ll) => L.circleMarker(ll, { radius: 4, color: "#2563eb" }),
    }).addTo(map);

    const res = info.res ? ` / 解像度 ${fmtNum(info.res[0])} ${info.unit}` : "";
    const desc = info.kind === "raster"
      ? `ラスタ ${info.width}×${info.height} px, ${info.bands} バンド${res}`
      : `ベクタ ${info.n_features} 件` + (info.n_displayed < info.n_features ? `（地図には先頭 ${info.n_displayed} 件を表示）` : "");
    setInfo($("refInfo"), `${desc}\n座標系: ${info.crs_epsg ? "EPSG:" + info.crs_epsg + " " : ""}${info.crs_name}`);

    // ROI = ファイルの範囲、座標系 = ファイルの座標系
    const [w, s, e, n] = info.bounds_wgs84;
    await setCrs(info.crs_value, info.crs_epsg ? `EPSG:${info.crs_epsg} — ${info.crs_name}` : `参照ファイルの座標系: ${info.crs_name}`, true);
    setRoi({ west: w, south: s, east: e, north: n }, { fit: true });

    const isRaster = info.kind === "raster";
    $("useRefGrid").disabled = !isRaster;
    $("useRefGrid").checked = isRaster;
    applyRefGridMode();
  } catch (e) {
    state.ref = null;
    setInfo($("refInfo"), e.message, "error");
    $("useRefGrid").disabled = true;
    $("useRefGrid").checked = false;
    applyRefGridMode();
  }
}
function clearRef() {
  state.ref = null;
  if (refLayer) { map.removeLayer(refLayer); refLayer = null; }
  $("refPath").value = "";
  setInfo($("refInfo"), "");
  $("useRefGrid").checked = false;
  $("useRefGrid").disabled = true;
  applyRefGridMode();
}
function applyRefGridMode() {
  const on = $("useRefGrid").checked;
  crsInput.disabled = on;
  $("gsd").disabled = on;
  roiInputs.forEach((id) => ($(id).disabled = on));
  $("drawRect").disabled = on;
  if (on) setInfo($("crsNote"), "参照 GeoTIFF のグリッドを使うため、座標系・範囲・解像度は参照ファイルのものになります。");
  else if (!state.crs.geographic) setInfo($("crsNote"), "");
  scheduleEstimate();
}
$("useRefGrid").onchange = applyRefGridMode;

// ============================================================================
// パラメータ / 見積もり
// ============================================================================
function buildParams() {
  const useRef = $("useRefGrid").checked && state.ref && state.ref.kind === "raster";
  return {
    roi: state.roi,
    crs: state.crs.value,
    gsd: $("gsd").value === "" ? null : parseFloat($("gsd").value),
    source: $("source").value,
    workers: parseInt($("workers").value, 10) || 4,
    use_cache: $("useCache").checked,
    use_reference_grid: !!useRef,
    reference_path: state.ref ? state.ref.path : null,
  };
}

let estSeq = 0;
async function doEstimate() {
  const p = buildParams();
  const ready = p.use_reference_grid || (p.roi && p.crs);
  const running = state.pollTimer != null;
  state.est = null;
  if (!ready) {
    setInfo($("estimate"), p.roi ? "出力座標系を選択してください。" : "ROI を指定してください。");
    $("runBtn").disabled = true;
    return;
  }
  const my = ++estSeq;
  try {
    const e = await post("/api/estimate", p);
    if (my !== estSeq) return;
    state.est = e;
    if (!p.use_reference_grid && p.gsd == null) {
      $("gsd").placeholder = `自動（${fmtNum(e.gsd_x)} ${e.unit}）`;
    }
    const lines = [
      `出力サイズ: ${e.width.toLocaleString()} × ${e.height.toLocaleString()} px / GSD ${fmtNum(e.gsd_x)} ${e.unit}`,
      `取得タイル: 約 ${e.n_tiles.toLocaleString()} 枚（ズーム ${e.zoom}） / 非圧縮 ${fmtBytes(e.est_bytes)}`,
      `所要時間の目安: 約 ${fmtDur(e.est_seconds)}（回線状況・キャッシュで変動）`,
      `座標系: ${e.crs_name}`,
    ];
    let kind;
    if (e.too_many_tiles) {
      lines.push(`⚠ タイル数が上限（${e.max_tiles} 枚）を超えています。ROI を小さくしてください。`);
      kind = "error";
    } else if (e.n_tiles > 1500) {
      lines.push("⚠ タイル数が多いため時間がかかります。");
      kind = "warn";
    }
    setInfo($("estimate"), lines.join("\n"), kind);
    $("runBtn").disabled = e.too_many_tiles || running;
  } catch (err) {
    if (my !== estSeq) return;
    setInfo($("estimate"), err.message, "error");
    $("runBtn").disabled = true;
  }
}
const scheduleEstimate = debounce(doEstimate, 350);
["gsd", "source", "workers"].forEach((id) => $(id).addEventListener("change", scheduleEstimate));
$("gsd").addEventListener("input", scheduleEstimate);

// ============================================================================
// 実行 / 進捗 / ログ
// ============================================================================
function appendLogs(lines) {
  const box = $("log");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  for (const l of lines) {
    const d = document.createElement("div");
    d.className = l.level;
    const t = document.createElement("span"); t.className = "t"; t.textContent = l.t + " ";
    const m = document.createElement("span"); m.textContent = (l.level === "INFO" ? "" : `[${l.level}] `) + l.msg;
    d.append(t, m);
    box.appendChild(d);
  }
  while (box.childElementCount > 5000) box.removeChild(box.firstChild);
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function applySnapshot(s) {
  state.jobId = s.id;
  appendLogs(s.logs);
  state.logNext = s.next;

  const pct = Math.round(s.progress * 100);
  $("barFill").style.width = pct + "%";
  $("barFill").className = s.status === "running" ? "" : s.status;
  $("pct").textContent = pct + "%";
  $("stage").textContent = s.stage;
  $("elapsed").textContent = fmtDur(s.elapsed);
  $("eta").textContent = s.status === "running" ? (s.eta != null ? "約 " + fmtDur(s.eta) : "--") : "--";
  $("logPath").textContent = s.log_path ? "自動保存: " + s.log_path : "";
  $("logPath").title = s.log_path || "";
  $("logSave").disabled = false;

  const msg = $("resultMsg");
  msg.className = "";
  if (s.status === "done" && s.result) {
    let t = `✔ 保存しました: ${s.result.output}（${fmtBytes(s.result.size_bytes)}, 有効画素率 ${(s.result.valid_ratio * 100).toFixed(1)}%）`;
    const rk = s.result.roi_kml || s.result.roi_kmz;
    if (rk) t += ` / KML: ${rk}`;
    msg.textContent = t;
  } else if (s.status === "error") {
    msg.className = "error"; msg.textContent = "✖ " + (s.error || "エラー");
  } else if (s.status === "cancelled") {
    msg.className = "error"; msg.textContent = "キャンセルしました";
  } else {
    msg.textContent = "";
  }

  const running = s.status === "running";
  $("cancelBtn").disabled = !running;
  $("runBtn").disabled = running || !state.est || state.est.too_many_tiles;
}

async function pollOnce() {
  try {
    const s = await api(`/api/jobs/${state.jobId}?since=${state.logNext}`);
    applySnapshot(s);
    if (s.status !== "running") stopPolling();
  } catch (e) {
    appendLogs([{ t: "", level: "WARN", msg: "進捗の取得に失敗しました: " + e.message }]);
  }
}
function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(pollOnce, 1000);
}
function stopPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = null;
}

$("runBtn").onclick = async () => {
  const p = buildParams();
  p.output_dir = $("outDir").value.trim();
  p.output_name = $("outName").value.trim() || "dem.tif";
  p.overwrite = $("overwrite").checked;
  p.export_roi_kml = $("exportRoiKml").checked;
  store("outDir", p.output_dir); store("outName", p.output_name);
  $("runBtn").disabled = true;
  try {
    $("log").textContent = "";
    state.logNext = 0;
    const s = await post("/api/jobs", p);
    applySnapshot(s);
    startPolling();
  } catch (e) {
    $("runBtn").disabled = false;
    $("resultMsg").className = "error";
    $("resultMsg").textContent = "✖ " + e.message;
    appendLogs([{ t: new Date().toLocaleTimeString(), level: "ERROR", msg: e.message }]);
  }
};
$("cancelBtn").onclick = async () => {
  if (!state.jobId) return;
  $("cancelBtn").disabled = true;
  try { applySnapshot(await post(`/api/jobs/${state.jobId}/cancel?since=${state.logNext}`)); } catch (e) { alert(e.message); }
};
$("logSave").onclick = () => {
  if (!state.jobId) return;
  const a = document.createElement("a");
  a.href = `/api/jobs/${state.jobId}/log`;
  document.body.appendChild(a); a.click(); a.remove();
};
$("logClear").onclick = () => { $("log").textContent = ""; };
$("outBrowse").onclick = () => openFs({
  mode: "dir", title: "保存先フォルダを選択", start: $("outDir").value || "~",
  onSelect: (p) => { $("outDir").value = p; store("outDir", p); },
});

// ============================================================================
// 接続を切る（サーバーの終了）
// ============================================================================
$("shutdownBtn").onclick = async () => {
  const running = state.pollTimer != null;
  const msg = running
    ? "ダウンロードを実行中です。中断してサーバーを終了します。よろしいですか？"
    : "サーバーを終了して、接続を切ります。よろしいですか？";
  if (!confirm(msg)) return;
  try {
    await post("/api/shutdown");
  } catch (e) {
    alert("終了できませんでした: " + e.message);
    return;
  }
  stopPolling();
  $("bye").hidden = false;
};

// ============================================================================
// 初期化
// ============================================================================
(async function init() {
  try {
    state.config = await api("/api/config");
    for (const s of state.config.sources) {
      const o = document.createElement("option");
      o.value = s.id; o.textContent = s.label;
      $("source").appendChild(o);
    }
    $("outDir").value = load("outDir", state.config.default_output_dir);
    $("outName").value = load("outName", "dem.tif");
  } catch (e) {
    setInfo($("estimate"), "サーバーとの通信に失敗しました: " + e.message, "error");
  }

  // 実行中(または直近)のジョブがあれば表示を復元する
  try {
    const cur = await api("/api/jobs/current");
    if (cur.job) {
      applySnapshot(cur.job);
      if (cur.job.status === "running") startPolling();
    }
  } catch (_) { /* 無視 */ }
})();
