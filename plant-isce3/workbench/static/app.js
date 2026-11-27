/* Plant-ISCE3 エクスプローラ — Vanilla JS */
const $ = (id) => document.getElementById(id);
const LAST_PROJECT_KEY = "plant_isce3_explorer_last_project";
const LAST_ROI_KEY = "plant_isce3_explorer_last_roi";
/** プロジェクト未設定時のフォールバック（日本・関東地方おおよそ） */
const KANTO_ROI = { west: 138.75, south: 34.85, east: 140.35, north: 36.25 };
const state = {
  project: null, config: null, tab: "common", schemas: {}, running: false, logOffset: 0,
  fsTarget: null, map: null, roiMode: null, roiLayer: null, footprintLayer: null,
};
let dragStart = null, dragRect = null;
const LOG_VIEW_MAX = 120_000;

/** \\r を改行にせず、進捗行はログ末尾 1 行を上書きする */
function applyLogChunk(cur, text) {
  if (!text) return cur;
  text = text.replace(/\r\n/g, "\n");
  let line = "";
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === "\r") {
      line = "";
    } else if (ch === "\n") {
      cur += line + "\n";
      line = "";
    } else {
      line += ch;
    }
  }
  if (line.length || text.includes("\r")) {
    const nl = cur.lastIndexOf("\n");
    cur = nl < 0 ? line : cur.slice(0, nl + 1) + line;
  }
  return cur;
}

function trimLogView() {
  const box = $("logBox");
  if (!box || box.textContent.length <= LOG_VIEW_MAX) return;
  const drop = box.textContent.length - LOG_VIEW_MAX;
  box.textContent = `… (先頭 ${drop} 文字を省略)\n` + box.textContent.slice(drop);
}

function appendLog(text) {
  if (!text) return;
  const box = $("logBox");
  box.textContent = applyLogChunk(box.textContent, text);
  trimLogView();
  box.scrollTop = box.scrollHeight;
  const n = (box.textContent.match(/\n/g) || []).length + 1;
  const meta = $("logMeta");
  if (meta) meta.textContent = `約 ${n.toLocaleString()} 行（古い行は自動で省略）`;
}

async function clearLogView() {
  $("logBox").textContent = "";
  const meta = $("logMeta");
  if (meta) meta.textContent = "";
  if (state.project) {
    try {
      const L = await api(`/api/projects/${encodeURIComponent(state.project)}/log?offset=0`);
      state.logOffset = L.offset;
    } catch (_) {
      state.logOffset = 0;
    }
  } else {
    state.logOffset = 0;
  }
}

function showBanner(text, kind = "warn") {
  const b = $("banner");
  if (!text) { b.classList.add("hidden"); return; }
  b.textContent = text;
  b.className = "banner " + (kind === "warn" ? "" : kind);
}

window.addEventListener("error", (e) => showBanner("画面の処理でエラーが発生しました: " + e.message, "error"));
window.addEventListener("unhandledrejection", (e) => showBanner("エラー: " + (e.reason?.message || e.reason), "error"));

function requireProject() {
  if (state.project) return true;
  showBanner("先に「＋ 新しいプロジェクトを作成」でプロジェクトを作ってください（設定・ログ・出力の保存先になります）。");
  return false;
}

async function api(path, method = "GET", body) {
  const r = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const details = (j.details || []).map((d) =>
      typeof d === "string" ? d : `${(d.loc || []).join(".")}: ${d.msg}`);
    throw new Error([j.error || r.statusText, ...details].join(" / "));
  }
  return j;
}

function roiFromFields() {
  return { west: +$("roiWest").value, south: +$("roiSouth").value, east: +$("roiEast").value, north: +$("roiNorth").value };
}

function roiValid(r) {
  return Number.isFinite(r.west) && Number.isFinite(r.south) && Number.isFinite(r.east) && Number.isFinite(r.north)
    && r.west < r.east && r.south < r.north;
}

function readStoredRoi() {
  try {
    const r = JSON.parse(localStorage.getItem(LAST_ROI_KEY) || "null");
    if (r && roiValid(r)) return r;
  } catch (_) {}
  return null;
}

function storeRoi(r) {
  if (!roiValid(r)) return;
  try {
    localStorage.setItem(LAST_ROI_KEY, JSON.stringify({
      west: +r.west, south: +r.south, east: +r.east, north: +r.north,
    }));
  } catch (_) {}
}

/** ① ブラウザに保存した ROI、② なければ関東付近 */
function defaultRoi() {
  return readStoredRoi() || KANTO_ROI;
}

function writeRoiInputs(r) {
  const f = (x) => (x == null || Number.isNaN(x) ? "" : +x);
  $("roiWest").value = f(r.west); $("roiSouth").value = f(r.south);
  $("roiEast").value = f(r.east); $("roiNorth").value = f(r.north);
}

function drawRoiRect(fit) {
  state.roiLayer.clearLayers();
  const r = roiFromFields();
  if (!roiValid(r)) {
    $("roiInfo").textContent = "";
    return;
  }
  const rect = L.rectangle([[r.south, r.west], [r.north, r.east]], {
    color: "#2563eb", weight: 2, fillOpacity: 0.12,
  }).addTo(state.roiLayer);
  const km = (a, b) => (a - b) * 111.32 * Math.cos(((r.south + r.north) / 2) * Math.PI / 180);
  $("roiInfo").textContent = `ROI: 東西 約 ${Math.abs(km(r.east, r.west)).toFixed(2)} km × 南北 約 ${Math.abs((r.north - r.south) * 111.32).toFixed(2)} km`;
  if (fit) state.map.fitBounds(rect.getBounds().pad(0.15));
}

function setRoi(r, opt = {}) {
  writeRoiInputs(r);
  drawRoiRect(opt.fit !== false);
}

function setRoiMode(mode) {
  state.roiMode = mode;
  $("btnDrawRoi").classList.toggle("active", mode === "rect");
  const wrap = document.querySelector(".map-wrap");
  wrap?.classList.toggle("crosshair", mode === "rect");
  if (mode === "rect") {
    state.map.dragging.disable();
    $("mapHint").textContent = "地図上でドラッグして矩形を描いてください（Esc で中止）";
  } else {
    state.map.dragging.enable();
    $("mapHint").textContent = "";
  }
}

function finishDrag(e) {
  if (state.roiMode !== "rect" || !dragStart) return;
  const b = L.latLngBounds(dragStart, e.latlng);
  if (dragRect) state.map.removeLayer(dragRect);
  dragStart = null;
  dragRect = null;
  setRoiMode(null);
  if (b.getNorth() - b.getSouth() < 1e-5 || b.getEast() - b.getWest() < 1e-5) return;
  const drawn = { west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth() };
  setRoi(drawn);
  storeRoi(drawn);
}

function initMap() {
  if (state.map) return;
  state.map = L.map("map", { preferCanvas: true }).setView([35.55, 139.55], 9);
  L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png", { attribution: "GSI", maxZoom: 18 }).addTo(state.map);
  state.roiLayer = L.layerGroup().addTo(state.map);
  state.footprintLayer = L.layerGroup().addTo(state.map);
  state.map.on("mousedown", (e) => {
    if (state.roiMode !== "rect") return;
    L.DomEvent.stopPropagation(e);
    dragStart = e.latlng;
    dragRect = L.rectangle([dragStart, dragStart], { color: "#d69e2e", weight: 2, fillOpacity: 0.15 }).addTo(state.map);
  });
  state.map.on("mousemove", (e) => {
    if (state.roiMode !== "rect" || !dragStart || !dragRect) return;
    dragRect.setBounds(L.latLngBounds(dragStart, e.latlng));
  });
  state.map.on("mouseup", finishDrag);
  setTimeout(() => state.map.invalidateSize(), 100);
  setRoi(defaultRoi(), { fit: true });
}

const CONFIG_SEND_STRIP = new Set([
  "check",
  "polarizations_available",
  "suggested_outputs",
  "spacing",
  "footprint",
  "bounds",
  "path",
  "platform",
  "datetime",
  "track",
  "frame",
  "direction",
  "frequencies",
]);

function readConfig() {
  const c = structuredClone(state.config) || {};
  for (const k of CONFIG_SEND_STRIP) delete c[k];
  c.description = $("description").value;
  c.input_h5 = $("inputH5").value.trim();
  c.frequency = $("frequency").value;
  c.polarization = $("polarization").value;
  c.roi = { west: +$("roiWest").value, south: +$("roiSouth").value, east: +$("roiEast").value, north: +$("roiNorth").value };
  const gs = $("gridSpacing").value.trim();
  c.grid = {
    epsg: c.grid?.epsg ?? null,
    spacing: gs === "" ? null : Number(gs),
  };
  if (c.grid.spacing != null && (!Number.isFinite(c.grid.spacing) || c.grid.spacing <= 0)) {
    c.grid.spacing = null;
  }
  c.geocode = { output: $("geocodeOutput")?.value || c.geocode?.output || "rtc_gamma" };
  c.outputs = {
    geocode: ($("outputGeocode")?.value || "").trim(),
    rtc: ($("outputRtc")?.value || "").trim(),
  };
  c.dem = {
    source: $("demSource").value,
    path: $("demPath").value.trim(),
    gsi_mode: $("gsiMode").value,
    vertical_datum: c.dem?.vertical_datum ?? "ellipsoid",
    margin_deg: c.dem?.margin_deg ?? 0.05,
  };
  c.tools = c.tools || { geocode: {}, rtc: {} };
  return c;
}

function fillForm(payload) {
  const cfg = payload.config ?? payload;
  const chk = payload.check ?? cfg.check ?? { errors: [], warnings: [] };
  $("description").value = cfg.description || "";
  $("inputH5").value = cfg.input_h5 || "";
  $("frequency").value = cfg.frequency || "A";
  const pol = cfg.polarization || "HH";
  $("polarization").value = ["HH", "HV", "HH_HV"].includes(pol) ? pol : "HH";
  updatePolarizationHint(cfg);
  const r = cfg.roi || {};
  if (r.west != null) {
    setRoi(r, { fit: false });
    storeRoi(r);
  } else {
    setRoi(defaultRoi(), { fit: true });
  }
  $("demSource").value = cfg.dem?.source || "gsi";
  $("demPath").value = cfg.dem?.path || "";
  $("gsiMode").value = cfg.dem?.gsi_mode || "auto";
  $("gridSpacing").value = cfg.grid?.spacing != null && cfg.grid.spacing !== "" ? cfg.grid.spacing : "";
  if ($("outputGeocode")) $("outputGeocode").value = cfg.outputs?.geocode || "";
  if ($("outputRtc")) $("outputRtc").value = cfg.outputs?.rtc || "";
  if ($("geocodeOutput")) $("geocodeOutput").value = cfg.geocode?.output || "rtc_gamma";
  updateGridSpacingHint(cfg, payload.runtime);
  toggleDemRows();
  $("checkMsg").textContent = chk.errors?.length ? chk.errors.join(" / ") : chk.warnings?.join(" / ") || "";
}

function updatePolarizationHint(cfg, available) {
  const h = $("polarizationHint");
  if (!h) return;
  const av = available || state.inspectMeta?.polarizations_available;
  const sel = $("polarization").value;
  let text =
    sel === "HH_HV"
      ? "RTC 付き geocode は 1 ファイル・2 バンド（HH, HV）。複素 GSLC は _HH.tif / _HV.tif に分割保存。"
      : sel === "HV"
        ? "HV のみを処理します（入力に HV が必要）。"
        : "HH のみを処理します（1 バンド）。"
  if (av?.length) text += ` 利用可能: ${av.join(", ")}`;
  h.textContent = text;
}

function updateGridSpacingHint(cfg, runtime) {
  const h = $("gridSpacingHint");
  if (!h) return;
  if (cfg.grid?.spacing != null && cfg.grid.spacing !== "") {
    h.textContent = `手動: ${cfg.grid.spacing} m`;
    return;
  }
  if (runtime?.grid_spacing_note) {
    h.textContent = runtime.grid_spacing_note;
    return;
  }
  h.textContent =
    "空欄のとき、実行時に NISAR の地上ピクセル間隔（スワス中央の方位・距離の細い方）を使います。NISAR を読み込むと推奨値が表示されます。";
}

function toggleDemRows() {
  const s = $("demSource").value;
  $("demFileRow").classList.toggle("hidden", s !== "file");
  $("gsiModeRow").classList.toggle("hidden", s !== "gsi");
}

async function loadSchemas() {
  for (const t of ["geocode", "rtc"]) {
    state.schemas[t] = await api("/api/tools/schema?tool=" + t);
  }
}

function renderToolForm(tool) {
  const sch = state.schemas[tool];
  const vals = (state.config?.tools || {})[tool] || {};
  const byGroup = {};
  for (const f of sch.fields) {
    const g = f.group || "options";
    (byGroup[g] ||= []).push(f);
  }
  const root = $("toolForm");
  root.innerHTML = "";
  for (const [g, fields] of Object.entries(byGroup)) {
    const det = document.createElement("details");
    det.open = g.includes("OPTION input") || fields.length < 8;
    det.innerHTML = `<summary>${g.replace(/\n+/g, " ")}</summary>`;
    for (const f of fields) {
      const lab = document.createElement("label");
      const id = `arg_${tool}_${f.dest}`;
      let input;
      if (f.type === "bool_true" || f.type === "bool_false") {
        input = document.createElement("input");
        input.type = "checkbox";
        input.checked = !!vals[f.dest];
      } else if (f.choices) {
        input = document.createElement("select");
        f.choices.forEach((c) => { const o = document.createElement("option"); o.value = c; o.textContent = c; input.appendChild(o); });
        if (vals[f.dest] != null) input.value = vals[f.dest];
      } else {
        input = document.createElement("input");
        input.type = f.type === "int" || f.type === "float" ? "number" : "text";
        input.value = vals[f.dest] ?? (f.default ?? "");
      }
      input.dataset.dest = f.dest;
      input.dataset.type = f.type;
      input.title = f.help || "";
      lab.appendChild(input);
      lab.append(` ${f.flags[0] || f.dest} `);
      if (f.help) lab.appendChild(document.createTextNode(" — " + f.help.slice(0, 80)));
      det.appendChild(lab);
    }
    root.appendChild(det);
  }
}

function collectToolArgs(tool) {
  const out = {};
  $("toolForm").querySelectorAll("[data-dest]").forEach((el) => {
    const dest = el.dataset.dest, t = el.dataset.type;
    if (t === "bool_true" || t === "bool_false") { if (el.checked) out[dest] = true; return; }
    if (el.value === "" || el.value == null) return;
    out[dest] = t === "int" ? parseInt(el.value, 10) : t === "float" ? parseFloat(el.value) : el.value;
  });
  return out;
}

async function refreshProjects() {
  const { projects } = await api("/api/projects");
  state.projectList = projects;
  const sel = $("projectSelect");
  sel.innerHTML = "";
  projects.forEach((p) => {
    const o = document.createElement("option");
    o.value = p.name;
    o.textContent = p.name + (p.running ? " (実行中)" : "");
    sel.appendChild(o);
  });
  if (state.project && [...sel.options].some((o) => o.value === state.project)) sel.value = state.project;
}

async function loadProject(name) {
  const p = await api("/api/projects/" + encodeURIComponent(name));
  state.project = name;
  try { localStorage.setItem(LAST_PROJECT_KEY, name); } catch (_) {}
  state.config = p.config;
  state.config.check = p.check;
  state.running = p.running;
  fillForm(p);
  renderToolForm(state.tab === "common" ? "geocode" : state.tab);
  if (p.runtime?.grid) { /* グリッド表示は将来 */ }
  $("logBox").textContent = "";
  $("logMeta").textContent = "";
  state.logOffset = 0;
  try {
    const L = await api(`/api/projects/${encodeURIComponent(name)}/log?offset=0`);
    if (L.text) { appendLog(L.text); state.logOffset = L.offset; }
  } catch (_) {}
  scheduleLogPoll();
}

async function saveConfig() {
  if (!requireProject()) return;
  const cfg = readConfig();
  if (state.tab !== "common") {
    cfg.tools[state.tab] = collectToolArgs(state.tab);
  }
  const p = await api("/api/projects/" + encodeURIComponent(state.project) + "/config", "POST", { config: cfg });
  state.config = p.config;
  state.config.check = p.check;
  fillForm(p);
  storeRoi(roiFromFields());
  if (p.check?.errors?.length) showBanner("");
  else showBanner("設定を保存しました。", "info");
}

async function runTool() {
  if (!requireProject()) return;
  await saveConfig();
  const tool = state.tab === "common" ? "geocode" : state.tab;
  if (tool === "common") return;
  await api("/api/projects/" + encodeURIComponent(state.project) + "/run", "POST", { tool });
  state.running = true;
  clearLogView();
  scheduleLogPoll();
  pollLog();
}

function scheduleLogPoll() {
  if (state._logTimer) clearInterval(state._logTimer);
  const ms = state.running ? 400 : 1500;
  state._logTimer = setInterval(pollLog, ms);
}

async function pollLog() {
  if (!state.project) return;
  const wasRunning = state.running;
  try {
    const L = await api(`/api/projects/${encodeURIComponent(state.project)}/log?offset=${state.logOffset}`);
    if (L.text) { appendLog(L.text); state.logOffset = L.offset; }
    const p = await api("/api/projects/" + encodeURIComponent(state.project));
    state.running = p.running;
    if (wasRunning !== state.running) scheduleLogPoll();
  } catch (_) {}
}
scheduleLogPoll();

$("mainTabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (!b) return;
  [...$("mainTabs").children].forEach((x) => x.classList.toggle("active", x === b));
  state.tab = b.dataset.tab;
  $("panelCommon").classList.toggle("hidden", state.tab !== "common");
  $("panelTool").classList.toggle("hidden", state.tab === "common");
  $("geocodeModeRow")?.classList.toggle("hidden", state.tab !== "geocode");
  if (state.tab === "common") setTimeout(() => state.map?.invalidateSize(), 80);
  if (state.tab !== "common") {
    renderToolForm(state.tab);
    const geoOut = $("geocodeOutput")?.value || "rtc_gamma";
    $("toolHint").textContent = state.tab === "geocode"
      ? (geoOut === "rtc_gamma"
        ? "plant_isce3_geocode で地図投影し、area-projection RTC（γ⁰）を適用します。下の plant オプションで補正方式などを上書きできます。"
        : "isce3.geocode_slc で複素 GSLC を地図投影します（距離位相フラットニングあり、放射地形補正なし）。")
      : "共通タブの ROI・DEM を使い、plant_isce3_rtc を実行します（レーダー座標）。地図上の RTC には geocode タブの「RTC 付き」を使ってください。";
  }
});

$("projectSelect").addEventListener("change", () => loadProject($("projectSelect").value));
$("btnNewProject").onclick = async () => {
  const name = prompt(
    "新しいプロジェクトの名前を入力してください。\n" +
    "projects/<名前>/ に設定・ログ・出力がまとめて保存されます。\n" +
    "（英数字・日本語・_ - . が使えます）"
  );
  if (!name) return;
  try {
    await api("/api/projects", "POST", { name: name.trim() });
  } catch (err) {
    showBanner("プロジェクトを作れませんでした: " + err.message, "error");
    return;
  }
  await refreshProjects();
  $("projectSelect").value = name.trim();
  await loadProject(name.trim());
  if (!state.config?.roi?.west) setRoi(defaultRoi(), { fit: true });
  showBanner(`プロジェクト「${name.trim()}」を作成しました。NISAR ファイルと ROI を設定して「保存」してください。`, "info");
};
function inspectRequestBody() {
  return {
    input_h5: $("inputH5").value.trim(),
    frequency: $("frequency").value,
    polarization: $("polarization").value,
    geocode: { output: $("geocodeOutput")?.value || "rtc_gamma" },
  };
}

function applySuggestedOutputs(sug) {
  if (!sug) return;
  if (sug.geocode) $("outputGeocode").value = sug.geocode;
  if (sug.rtc) $("outputRtc").value = sug.rtc;
  if (sug.geocode || sug.rtc) {
    $("outputNameHint").textContent =
      `出力ファイル名を更新しました（変更したら「設定を保存」）。例: ${sug.geocode || sug.rtc}`;
  }
}

function applyInspectResult(r, { showFootprint = true } = {}) {
  const prod = r.product || {};
  if (showFootprint && prod.footprint?.length && state.map) {
    state.footprintLayer.clearLayers();
    const poly = L.polygon(prod.footprint.map((c) => [c[1], c[0]]), {
      color: "#888", weight: 1, fillOpacity: 0.08,
    }).addTo(state.footprintLayer);
    state.map.fitBounds(poly.getBounds().pad(0.05));
  }
  if (prod.spacing?.recommended_m && !$("gridSpacing").value.trim()) {
    const s = prod.spacing;
    $("gridSpacingHint").textContent =
      `推奨（自動）: ${s.recommended_m} m（地上方位 ${s.azimuth_m} m、地上距離 ${s.ground_range_m} m、周波数 ${s.frequency || ""}）`;
  }
  if (prod.polarizations_available?.length) {
    state.inspectMeta = { polarizations_available: prod.polarizations_available };
    updatePolarizationHint(state.config, prod.polarizations_available);
  }
  applySuggestedOutputs(r.suggested_outputs || prod.suggested_outputs);
  if (prod.frequencies?.length && !$("frequency").dataset.userPicked) {
    const f0 = prod.frequencies[0];
    if (f0 === "A" || f0 === "B") $("frequency").value = f0;
  }
}

let _inspectTimer = null;
let _inspectInflight = null;

async function runNisarInspect({ showFootprint = true, announce = true } = {}) {
  if (!requireProject()) return false;
  const h5 = $("inputH5").value.trim();
  if (!h5) {
    if (announce) showBanner("NISAR の HDF5 パスを指定してください。", "error");
    return false;
  }
  if (_inspectInflight) {
    try { await _inspectInflight; } catch (_) {}
  }
  const run = (async () => {
    const r = await api(
      "/api/projects/" + encodeURIComponent(state.project) + "/inspect",
      "POST",
      inspectRequestBody(),
    );
    applyInspectResult(r, { showFootprint });
    if (announce) {
      const prod = r.product || {};
      const bits = [prod.datetime, prod.track, prod.direction].filter(Boolean);
      const msg = bits.length ? bits.join(" · ") : "メタデータを読み込みました";
      showBanner(`NISAR データを読み込みました（${msg}）。灰色の枠が観測範囲です。`, "info");
    }
    return true;
  })();
  _inspectInflight = run;
  try {
    return await run;
  } catch (err) {
    if (announce) showBanner("NISAR の読み込みに失敗: " + err.message, "error");
    return false;
  } finally {
    if (_inspectInflight === run) _inspectInflight = null;
  }
}

function scheduleNisarInspect() {
  clearTimeout(_inspectTimer);
  if (!$("inputH5").value.trim()) return;
  _inspectTimer = setTimeout(
    () => runNisarInspect({ showFootprint: false, announce: false }),
    450,
  );
}

$("polarization")?.addEventListener("change", () => {
  updatePolarizationHint(state.config);
  scheduleNisarInspect();
});
$("frequency")?.addEventListener("change", () => {
  $("frequency").dataset.userPicked = "1";
  scheduleNisarInspect();
});
$("geocodeOutput")?.addEventListener("change", () => {
  if (state.tab === "geocode") {
    $("toolHint").textContent = $("geocodeOutput").value === "rtc_gamma"
      ? "plant_isce3_geocode で地図投影し、area-projection RTC（γ⁰）を適用します。下の plant オプションで補正方式などを上書きできます。"
      : "isce3.geocode_slc で複素 GSLC を地図投影します（距離位相フラットニングあり、放射地形補正なし）。";
  }
  scheduleNisarInspect();
});
$("inputH5")?.addEventListener("change", () => scheduleNisarInspect());
$("btnLogClear").onclick = () => { clearLogView(); };
$("btnSave").onclick = () => saveConfig();
$("btnInspect").onclick = () => runNisarInspect({ showFootprint: true, announce: true });
$("btnRun").onclick = () => runTool();
$("btnCancel").onclick = async () => {
  if (state.project) await api("/api/projects/" + encodeURIComponent(state.project) + "/cancel", "POST", {});
};
$("btnShutdown").onclick = async () => {
  if (!confirm("GUI サーバーを終了しますか？（実行中の処理はバックグラウンドで継続します）")) return;
  const msg = $("checkMsg");
  msg.textContent = "サーバーを終了しています…";
  try {
    await api("/api/shutdown", "POST", {});
    const fin = "サーバーを終了しました。このタブを閉じてください。";
    msg.textContent = fin;
    appendLog(fin + "\n");
  } catch (_) {
    const fin = "サーバーを終了しました（接続が切断されました）。";
    msg.textContent = fin;
    appendLog(fin + "\n");
  }
};
$("btnDrawRoi").onclick = () => setRoiMode(state.roiMode === "rect" ? null : "rect");
$("btnClearDraw").onclick = () => {
  if (dragRect) state.map.removeLayer(dragRect);
  dragStart = null;
  dragRect = null;
  setRoiMode(null);
};
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && state.roiMode) {
    if (dragRect) state.map.removeLayer(dragRect);
    dragStart = null;
    dragRect = null;
    setRoiMode(null);
  }
});
["roiWest", "roiSouth", "roiEast", "roiNorth"].forEach((id) =>
  $(id).addEventListener("change", () => {
    drawRoiRect(false);
    storeRoi(roiFromFields());
  })
);
$("demSource").addEventListener("change", toggleDemRows);

let browseExts = "";
document.querySelectorAll(".browse").forEach((btn) => {
  btn.onclick = () => {
    state.fsPurpose = "field";
    browseExts = btn.dataset.ext || "";
    state.fsTarget = btn.previousElementSibling?.tagName === "INPUT" ? btn.previousElementSibling : $("inputH5");
    openFs(state.fsTarget.value ? state.fsTarget.value.replace(/[^/\\]+$/, "") : "");
  };
});

async function importRoiVector(path) {
  const r = await api("/api/roi/import", "POST", { path });
  setRoi(r.roi, { fit: true });
  storeRoi(r.roi);
  showBanner("ベクターから ROI を読み込みました。", "info");
}

async function exportRoiKml() {
  const r = roiFromFields();
  if (!roiValid(r)) {
    showBanner("ROI が未設定です。", "error");
    return;
  }
  const label = state.project || "explorer_roi";
  const resp = await fetch("/api/roi/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ roi: r, name: label, filename: `${label}.kml` }),
  });
  if (!resp.ok) {
    const j = await resp.json().catch(() => ({}));
    throw new Error(j.error || resp.statusText);
  }
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${label}.kml`;
  a.click();
  URL.revokeObjectURL(url);
  showBanner("KML をダウンロードしました。", "info");
}

$("btnRoiImport").onclick = () => {
  if (!requireProject()) return;
  state.fsPurpose = "roi";
  browseExts = "kml,kmz,shp,gpkg";
  openFs("");
};
$("btnRoiExportKml").onclick = async () => {
  try {
    await exportRoiKml();
  } catch (err) {
    showBanner("KML 書き出しに失敗: " + err.message, "error");
  }
};

async function openFs(path) {
  const r = await api("/api/fs?path=" + encodeURIComponent(path || "") + "&ext=" + encodeURIComponent(browseExts));
  $("fsPath").textContent = r.path;
  const ul = $("fsList");
  ul.innerHTML = "";
  if (r.parent) {
    const li = document.createElement("li");
    li.textContent = "↑ 親フォルダ";
    li.onclick = () => openFs(r.parent);
    ul.appendChild(li);
  }
  r.dirs.forEach((d) => {
    const li = document.createElement("li");
    li.textContent = "📁 " + d;
    li.onclick = () => openFs(r.path + "/" + d);
    ul.appendChild(li);
  });
  r.files.forEach((f) => {
    const li = document.createElement("li");
    li.textContent = f.name;
    li.dataset.full = r.path + "/" + f.name;
    li.onclick = () => { ul.querySelectorAll("li").forEach((x) => x.classList.remove("sel")); li.classList.add("sel"); state._pick = li.dataset.full; };
    ul.appendChild(li);
  });
  $("dlgBrowse").classList.remove("hidden");
  if (state.map) state.map.getContainer().style.pointerEvents = "none";
}
function closeFsDlg() {
  $("dlgBrowse").classList.add("hidden");
  if (state.map) state.map.getContainer().style.pointerEvents = "";
  setTimeout(() => state.map?.invalidateSize(), 50);
}
$("fsPick").onclick = async () => {
  if (!state._pick) {
    closeFsDlg();
    return;
  }
  try {
    if (state.fsPurpose === "roi") {
      await importRoiVector(state._pick);
    } else if (state.fsTarget) {
      state.fsTarget.value = state._pick;
      if (state.fsTarget === $("inputH5")) scheduleNisarInspect();
    }
  } catch (err) {
    showBanner(err.message || String(err), "error");
  }
  state.fsPurpose = "field";
  closeFsDlg();
};
$("fsClose").onclick = () => closeFsDlg();
$("dlgBrowse").addEventListener("click", (e) => { if (e.target === $("dlgBrowse")) closeFsDlg(); });

(async function boot() {
  try {
    initMap();
  } catch (err) {
    showBanner("地図を初期化できませんでした: " + err.message, "error");
  }
  try {
    await loadSchemas();
    await refreshProjects();
    const projects = state.projectList || [];
    if (projects.length) {
      let name = null;
      try { name = localStorage.getItem(LAST_PROJECT_KEY); } catch (_) {}
      if (!name || !projects.some((p) => p.name === name)) {
        const sorted = [...projects].sort((a, b) => (b.modified || "").localeCompare(a.modified || ""));
        name = sorted[0]?.name;
      }
      $("projectSelect").value = name;
      await loadProject(name);
    } else {
      showBanner("まず「＋ 新しいプロジェクトを作成」を押してください。プロジェクトは設定・ログ・出力の保存先です。");
    }
  } catch (err) {
    showBanner("サーバーと通信できません: " + err.message, "error");
  }
})();