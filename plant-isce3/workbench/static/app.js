/* Plant-ISCE3 エクスプローラ — Vanilla JS */
const $ = (id) => document.getElementById(id);
const state = {
  project: null, config: null, tab: "common", schemas: {}, running: false, logOffset: 0,
  fsTarget: null, map: null, roiMode: null, roiLayer: null, footprintLayer: null,
};
let dragStart = null, dragRect = null;
const LOG_VIEW_MAX = 120_000;

function normalizeLogChunk(text) {
  if (!text) return "";
  return text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
}

function trimLogView() {
  const box = $("logBox");
  if (!box || box.textContent.length <= LOG_VIEW_MAX) return;
  const drop = box.textContent.length - LOG_VIEW_MAX;
  box.textContent = `… (先頭 ${drop} 文字を省略)\n` + box.textContent.slice(drop);
}

function appendLog(text) {
  if (!text) return;
  $("logBox").textContent += normalizeLogChunk(text);
  trimLogView();
  const box = $("logBox");
  box.scrollTop = box.scrollHeight;
  const n = (box.textContent.match(/\n/g) || []).length + 1;
  const meta = $("logMeta");
  if (meta) meta.textContent = `約 ${n.toLocaleString()} 行（古い行は自動で省略）`;
}

function clearLogView() {
  $("logBox").textContent = "";
  const meta = $("logMeta");
  if (meta) meta.textContent = "";
  state.logOffset = 0;
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
  setRoi({ west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth() });
}

function initMap() {
  if (state.map) return;
  state.map = L.map("map", { preferCanvas: true }).setView([35, 135], 5);
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
  writeRoiInputs({ west: 130, south: 32, east: 131, north: 33 });
  drawRoiRect(true);
}

function readConfig() {
  const c = structuredClone(state.config) || {};
  delete c.check;
  c.description = $("description").value;
  c.input_h5 = $("inputH5").value.trim();
  c.frequency = $("frequency").value;
  c.polarization = $("polarization").value.trim();
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
  $("polarization").value = cfg.polarization || "HH";
  const r = cfg.roi || {};
  if (r.west != null) setRoi(r, { fit: false });
  else drawRoiRect(false);
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
  pollLog();
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
  pollLog();
}

async function pollLog() {
  if (!state.project) return;
  try {
    const L = await api(`/api/projects/${encodeURIComponent(state.project)}/log?offset=${state.logOffset}`);
    if (L.text) { appendLog(L.text); state.logOffset = L.offset; }
    const p = await api("/api/projects/" + encodeURIComponent(state.project));
    state.running = p.running;
  } catch (_) {}
}
state._poll = setInterval(pollLog, 1500);

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
  showBanner(`プロジェクト「${name.trim()}」を作成しました。NISAR ファイルと ROI を設定して「保存」してください。`, "info");
};
$("geocodeOutput")?.addEventListener("change", () => {
  if (state.tab === "geocode") {
    $("toolHint").textContent = $("geocodeOutput").value === "rtc_gamma"
      ? "plant_isce3_geocode で地図投影し、area-projection RTC（γ⁰）を適用します。下の plant オプションで補正方式などを上書きできます。"
      : "isce3.geocode_slc で複素 GSLC を地図投影します（距離位相フラットニングあり、放射地形補正なし）。";
  }
});
$("btnLogClear").onclick = () => clearLogView();
$("btnSave").onclick = () => saveConfig();
$("btnInspect").onclick = async () => {
  if (!requireProject()) return;
  if (!$("inputH5").value.trim()) {
    showBanner("先に NISAR の HDF5 パスを指定し「設定を保存」してください。", "error");
    return;
  }
  await saveConfig();
  try {
    const r = await api("/api/projects/" + encodeURIComponent(state.project) + "/inspect", "POST", {});
    const prod = r.product || {};
    const fp = prod.footprint;
    if (fp?.length && state.map) {
      state.footprintLayer.clearLayers();
      const poly = L.polygon(fp.map((c) => [c[1], c[0]]), { color: "#888", weight: 1, fillOpacity: 0.08 }).addTo(state.footprintLayer);
      state.map.fitBounds(poly.getBounds().pad(0.05));
    }
    const bits = [prod.datetime, prod.track, prod.direction].filter(Boolean);
    const msg = bits.length ? bits.join(" · ") : "メタデータを読み込みました";
    showBanner(`NISAR データを読み込みました（${msg}）。灰色の枠が観測範囲です。`, "info");
    if (prod.spacing?.recommended_m && !$("gridSpacing").value.trim()) {
      const s = prod.spacing;
      $("gridSpacingHint").textContent =
        `推奨（自動）: ${s.recommended_m} m（地上方位 ${s.azimuth_m} m、地上距離 ${s.ground_range_m} m、周波数 ${s.frequency || ""}）`;
    }
    if (prod.frequencies?.length && !$("frequency").dataset.userPicked) {
      const f0 = prod.frequencies[0];
      if (f0 === "A" || f0 === "B") $("frequency").value = f0;
    }
  } catch (err) {
    showBanner("NISAR の読み込みに失敗: " + err.message, "error");
  }
};
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
["roiWest", "roiSouth", "roiEast", "roiNorth"].forEach((id) => $(id).addEventListener("change", () => drawRoiRect(false)));
$("demSource").addEventListener("change", toggleDemRows);

let browseExts = "";
document.querySelectorAll(".browse").forEach((btn) => {
  btn.onclick = () => {
    browseExts = btn.dataset.ext || "";
    state.fsTarget = btn.previousElementSibling?.tagName === "INPUT" ? btn.previousElementSibling : $("inputH5");
    openFs(state.fsTarget.value ? state.fsTarget.value.replace(/[^/\\]+$/, "") : "");
  };
});

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
$("fsPick").onclick = () => {
  if (state._pick && state.fsTarget) state.fsTarget.value = state._pick;
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
    if ($("projectSelect").options.length) {
      await loadProject($("projectSelect").value);
    } else {
      showBanner("まず「＋ 新しいプロジェクトを作成」を押してください。プロジェクトは設定・ログ・出力の保存先です。");
    }
  } catch (err) {
    showBanner("サーバーと通信できません: " + err.message, "error");
  }
})();