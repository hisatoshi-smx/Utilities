/* 三時期 CCD（plant-isce3）GUI — Vanilla JS + Leaflet */
"use strict";

const $ = (id) => document.getElementById(id);
const TAGS = ["t1", "t2", "t3"];
const TAG_COLORS = { t1: "#2b6cb0", t2: "#2f855a", t3: "#c53030" };
const KM_PER_DEG_LAT = 111.32;

const S = {
  info: null,
  name: "",
  payload: null,
  dirty: false,
  logName: null,
  logOffset: 0,
  following: true,
  wasRunning: false,
  previewKey: "",
  previewStamp: "",
  roiMode: null, // "rect" | "center" | null
};

// ------------------------------------------------------------------ API
async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(url, opt);
  let data = null;
  try { data = await res.json(); } catch (_) { /* 空応答 */ }
  if (!res.ok) {
    const err = new Error((data && data.error) || `HTTP ${res.status}`);
    err.details = data && data.details;
    throw err;
  }
  return data;
}
const pUrl = (suffix = "") => `/api/projects/${encodeURIComponent(S.name)}${suffix}`;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtBytes(n) {
  if (n == null) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}
function toast(msg, isErr) {
  const box = $("checkBox");
  box.innerHTML = `<div class="${isErr ? "msg-err" : "msg-ok"}">${esc(msg)}</div>`;
}
function showError(e) {
  let msg = e.message || String(e);
  if (e.details && e.details.length) {
    msg += "\n" + e.details.map((d) => `・${d.field}: ${d.message}`).join("\n");
  }
  $("checkBox").innerHTML = `<div class="msg-err" style="white-space:pre-wrap">${esc(msg)}</div>`;
}

// ------------------------------------------------------------------ 地図
const map = L.map("map", { zoomControl: true, preferCanvas: false }).setView([36.5, 138.0], 6);
const attrGsi = '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank">国土地理院</a>';
const baseLayers = {
  "地理院 淡色": L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png", { maxZoom: 18, attribution: attrGsi }),
  "地理院 標準": L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png", { maxZoom: 18, attribution: attrGsi }),
  "地理院 写真": L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg", { maxZoom: 18, attribution: attrGsi }),
  "OpenStreetMap": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" }),
};
baseLayers["地理院 淡色"].addTo(map);
const footprintLayer = L.layerGroup().addTo(map);
const burstLayer = L.layerGroup().addTo(map);
const gridLayer = L.layerGroup().addTo(map);
const roiLayer = L.layerGroup().addTo(map);
L.control.layers(baseLayers, {
  "シーン範囲": footprintLayer,
  "バースト": burstLayer,
  "出力グリッド": gridLayer,
  "ROI": roiLayer,
}, { collapsed: true }).addTo(map);
L.control.scale({ imperial: false }).addTo(map);
let resultOverlay = null;
let roiRect = null;
let centerMarker = null;

function setMapHint(text) {
  const el = $("mapHint");
  el.hidden = !text;
  el.textContent = text || "";
}

// ---- ROI 表示 ----
function readRoiInputs() {
  const v = (id) => { const s = $(id).value.trim(); return s === "" ? null : Number(s); };
  return { west: v("roiWest"), south: v("roiSouth"), east: v("roiEast"), north: v("roiNorth") };
}
function roiValid(r) {
  return [r.west, r.south, r.east, r.north].every((x) => x !== null && Number.isFinite(x)) && r.west < r.east && r.south < r.north;
}
function writeRoiInputs(r) {
  const f = (x) => (x === null || x === undefined ? "" : Number(x).toFixed(6));
  $("roiWest").value = f(r.west); $("roiSouth").value = f(r.south);
  $("roiEast").value = f(r.east); $("roiNorth").value = f(r.north);
}
function drawRoi() {
  roiLayer.clearLayers();
  roiRect = null;
  const r = readRoiInputs();
  const any = [r.west, r.south, r.east, r.north].some((x) => x !== null);
  ["roiWest", "roiSouth", "roiEast", "roiNorth"].forEach((id) => $(id).classList.toggle("invalid", any && !roiValid(r)));
  if (!roiValid(r)) { $("roiInfo").textContent = any ? "ROI が不正です（西 < 東、南 < 北）" : ""; return; }
  roiRect = L.rectangle([[r.south, r.west], [r.north, r.east]], { color: "#d69e2e", weight: 2, fillOpacity: 0.08, dashArray: "6 4" }).addTo(roiLayer);
  const latc = (r.south + r.north) / 2;
  const ew = (r.east - r.west) * KM_PER_DEG_LAT * Math.cos(latc * Math.PI / 180);
  const ns = (r.north - r.south) * KM_PER_DEG_LAT;
  $("roiInfo").textContent = `ROI: 東西 約 ${ew.toFixed(2)} km × 南北 約 ${ns.toFixed(2)} km`;
  if (centerMarker) { roiLayer.addLayer(centerMarker); }
}
function setRoi(r, { fit = false, syncCenter = true } = {}) {
  writeRoiInputs(r);
  if (syncCenter && roiValid(r)) {
    const latc = (r.south + r.north) / 2, lonc = (r.west + r.east) / 2;
    $("centerLat").value = latc.toFixed(6);
    $("centerLon").value = lonc.toFixed(6);
    $("sizeEwKm").value = ((r.east - r.west) * KM_PER_DEG_LAT * Math.cos(latc * Math.PI / 180)).toFixed(3);
    $("sizeNsKm").value = ((r.north - r.south) * KM_PER_DEG_LAT).toFixed(3);
    $("squareLock").checked = Math.abs(Number($("sizeEwKm").value) - Number($("sizeNsKm").value)) < 0.01;
    setCenterMarker(latc, lonc);
  }
  drawRoi();
  markDirty();
  if (fit && roiRect) map.fitBounds(roiRect.getBounds().pad(0.3));
}
function setCenterMarker(lat, lon) {
  if (centerMarker) roiLayer.removeLayer(centerMarker);
  centerMarker = L.circleMarker([lat, lon], { radius: 5, color: "#b7791f", fillColor: "#f6e05e", fillOpacity: 1, weight: 2 });
  roiLayer.addLayer(centerMarker);
}

// ---- 中心＋四方 km ----
function roiFromCenter(lat, lon) {
  let ew = Number($("sizeEwKm").value);
  let ns = $("squareLock").checked ? ew : Number($("sizeNsKm").value);
  if (!(ew > 0) || !(ns > 0)) throw new Error("東西・南北の大きさ（km）を正の数で入力してください");
  if (!(Math.abs(lat) < 89)) throw new Error("中心緯度が不正です");
  const dlat = (ns / 2) / KM_PER_DEG_LAT;
  const dlon = (ew / 2) / (KM_PER_DEG_LAT * Math.cos(lat * Math.PI / 180));
  return { west: lon - dlon, east: lon + dlon, south: lat - dlat, north: lat + dlat };
}
function applyCenter(fit) {
  const lat = Number($("centerLat").value), lon = Number($("centerLon").value);
  if ($("centerLat").value === "" || $("centerLon").value === "" || !Number.isFinite(lat) || !Number.isFinite(lon)) {
    throw new Error("中心の緯度・経度を入力するか、地図でクリックしてください");
  }
  const r = roiFromCenter(lat, lon);
  setCenterMarker(lat, lon);
  setRoi(r, { fit, syncCenter: false });
}

// ---- 地図操作モード ----
function setRoiMode(mode) {
  S.roiMode = mode;
  $("drawRoiBtn").classList.toggle("active", mode === "rect");
  $("pickCenterBtn").classList.toggle("active", mode === "center");
  map.getContainer().classList.toggle("crosshair", !!mode);
  if (mode === "rect") {
    map.dragging.disable();
    setMapHint("地図上でドラッグして矩形を描いてください（Esc で中止）");
  } else if (mode === "center") {
    map.dragging.enable();
    setMapHint("ROI の中心をクリックしてください。指定した東西・南北 km の矩形になります（Esc で中止）");
  } else {
    map.dragging.enable();
    setMapHint("");
  }
}
let dragStart = null, dragRect = null;
map.on("mousedown", (e) => {
  if (S.roiMode !== "rect") return;
  dragStart = e.latlng;
  dragRect = L.rectangle([dragStart, dragStart], { color: "#d69e2e", weight: 2, fillOpacity: 0.1 }).addTo(map);
});
map.on("mousemove", (e) => {
  if (S.roiMode !== "rect" || !dragStart || !dragRect) return;
  dragRect.setBounds(L.latLngBounds(dragStart, e.latlng));
});
function finishDrag(e) {
  if (S.roiMode !== "rect" || !dragStart) return;
  const b = L.latLngBounds(dragStart, e.latlng);
  map.removeLayer(dragRect);
  dragStart = null; dragRect = null;
  setRoiMode(null);
  if (b.getNorth() - b.getSouth() < 1e-5 || b.getEast() - b.getWest() < 1e-5) return;
  setRoi({ west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth() });
}
map.on("mouseup", finishDrag);
map.on("click", (e) => {
  if (S.roiMode !== "center") return;
  $("centerLat").value = e.latlng.lat.toFixed(6);
  $("centerLon").value = L.Util.wrapNum(e.latlng.lng, [-180, 180], true).toFixed(6);
  setRoiMode(null);
  try { applyCenter(false); } catch (err) { showError(err); }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && S.roiMode) {
    if (dragRect) map.removeLayer(dragRect);
    dragStart = null; dragRect = null;
    setRoiMode(null);
  }
});

// ------------------------------------------------------------------ シーン・バースト表示
function burstSet() {
  return new Set($("burstIds").value.split(/[\s,]+/).map((s) => s.trim().toLowerCase()).filter(Boolean));
}
function drawScenes() {
  footprintLayer.clearLayers();
  burstLayer.clearLayers();
  gridLayer.clearLayers();
  const p = S.payload;
  if (!p) return;
  const sc = p.scenes;
  const bounds = [];
  if (sc && sc.scenes) {
    for (const t of TAGS) {
      const s = sc.scenes[t];
      if (!s) continue;
      const ll = s.footprint.map(([x, y]) => [y, x]);
      bounds.push(...ll);
      L.polygon(ll, { color: TAG_COLORS[t], weight: 2, fill: false })
        .bindTooltip(`${t}: ${s.datetime.replace("T", " ").slice(0, 19)}`, { sticky: true })
        .addTo(footprintLayer);
    }
    // バーストは t2 の形状で表示（3 シーン共通のもののみ）
    const t2 = sc.scenes.t2;
    if (t2 && t2.bursts && t2.bursts.length) {
      const ids = TAGS.map((t) => new Set((sc.scenes[t].bursts || []).map((b) => b.id)));
      const sel = burstSet();
      const used = new Set(((p.grid && p.grid.units) || []).map((u) => u.id));
      for (const b of t2.bursts) {
        const common = ids[0].has(b.id) && ids[1].has(b.id) && ids[2].has(b.id);
        if (!common) continue;
        const chosen = sel.has(b.id);
        const poly = L.polygon(b.border.map(([x, y]) => [y, x]), {
          color: chosen ? "#805ad5" : used.has(b.id) ? "#3182ce" : "#718096",
          weight: chosen ? 2 : 1,
          fillOpacity: chosen ? 0.2 : used.has(b.id) ? 0.08 : 0.02,
        });
        poly.bindTooltip(`${b.id}${chosen ? "（選択中）" : ""}<br><small>クリックで選択／解除</small>`, { sticky: true });
        poly.on("click", (ev) => {
          if (S.roiMode) return;
          L.DomEvent.stopPropagation(ev);
          const s = burstSet();
          if (s.has(b.id)) s.delete(b.id); else s.add(b.id);
          $("burstIds").value = [...s].sort().join("\n");
          markDirty();
          drawScenes();
        });
        poly.addTo(burstLayer);
      }
    }
  }
  if (p.grid && p.grid.lonlat_bounds) {
    const [w, s, e, n] = p.grid.lonlat_bounds;
    L.rectangle([[s, w], [n, e]], { color: "#4a5568", weight: 1, dashArray: "2 4", fill: false })
      .bindTooltip(`出力グリッド: EPSG:${p.grid.grid.epsg} ${p.grid.grid.width}×${p.grid.grid.length}`, { sticky: true })
      .addTo(gridLayer);
  }
  return bounds;
}
function renderSceneInfo() {
  const sc = S.payload && S.payload.scenes;
  const box = $("sceneInfo");
  if (!sc || !sc.scenes) { box.innerHTML = ""; return; }
  let html = "<table><tr><th></th><th>観測日時 (UTC)</th><th>衛星</th><th>軌道</th></tr>";
  for (const t of TAGS) {
    const s = sc.scenes[t];
    const nb = s.bursts && s.bursts.length ? ` / ${s.bursts.length} バースト` : "";
    html += `<tr><td><span class="swatch" style="background:${TAG_COLORS[t]}"></span>${t}</td>` +
      `<td>${esc(s.datetime.replace("T", " ").slice(0, 19))}</td><td>${esc(s.platform)}</td>` +
      `<td>${esc(s.track)} ${esc(s.direction || "")}${nb}</td></tr>` +
      `<tr><td></td><td colspan="3" class="muted mono" style="font-size:11px">${esc(s.path)}</td></tr>`;
  }
  html += "</table>";
  for (const w of sc.warnings || []) html += `<div class="msg-warn">注意: ${esc(w)}</div>`;
  box.innerHTML = html;
}
function renderGridInfo() {
  const g = S.payload && S.payload.grid;
  $("gridInfo").textContent = g
    ? `前回決定したグリッド: EPSG:${g.grid.epsg}（${g.crs_name}）、${g.grid.width} × ${g.grid.length} 画素、処理単位 ${g.units.length} 個`
    : "";
}

// ------------------------------------------------------------------ フォーム
function sensor() { return document.querySelector("input[name=sensor]:checked").value; }
function radioVal(name) { return document.querySelector(`input[name=${name}]:checked`).value; }
function setRadio(name, v) {
  const el = document.querySelector(`input[name=${name}][value="${v}"]`);
  if (el) el.checked = true;
}
function buildPathRows(containerId, prefix, labelFn, extFn) {
  const box = $(containerId);
  box.innerHTML = "";
  TAGS.forEach((t, i) => {
    const row = document.createElement("div");
    row.className = "scene-row";
    row.innerHTML = `<span class="tag">${i + 1}</span>` +
      `<input id="${prefix}${i}" type="text" spellcheck="false" placeholder="${esc(labelFn(i))}">` +
      `<button type="button" class="browse" data-target="${prefix}${i}">参照…</button>`;
    box.appendChild(row);
  });
  box.querySelectorAll("button.browse").forEach((b) => { b.dataset.extFn = extFn; });
}
function sceneExt() { return sensor() === "sentinel1" ? ".zip" : ".h5"; }
function updateVisibility() {
  const s1 = sensor() === "sentinel1";
  document.querySelectorAll(".s1-only").forEach((el) => { el.hidden = !s1; });
  document.querySelectorAll(".nisar-only").forEach((el) => { el.hidden = s1; });
  document.querySelectorAll(".dem-file-only").forEach((el) => { el.hidden = radioVal("demSource") !== "file"; });
  const om = radioVal("orbitMode");
  document.querySelectorAll(".orbit-auto-only").forEach((el) => { el.hidden = om !== "auto"; });
  document.querySelectorAll(".orbit-files-only").forEach((el) => { el.hidden = om !== "files"; });
  $("sizeNsKm").disabled = $("squareLock").checked;
  if ($("squareLock").checked) $("sizeNsKm").value = $("sizeEwKm").value;
}

function fillForm(cfg) {
  $("description").value = cfg.description || "";
  setRadio("sensor", cfg.inputs.sensor);
  cfg.inputs.scenes.forEach((p, i) => { $(`scene${i}`).value = p; });
  $("polarization").value = cfg.inputs.polarization;
  $("nisarFrequency").value = cfg.inputs.nisar_frequency;
  $("burstIds").value = (cfg.inputs.burst_ids || []).join("\n");
  writeRoiInputs(cfg.roi);
  if (roiValid(cfg.roi)) setRoi(cfg.roi, { syncCenter: true }); else drawRoi();
  $("gridEpsg").value = cfg.grid.epsg ?? "";
  $("gridSpacing").value = cfg.grid.spacing;
  setRadio("demSource", cfg.dem.source);
  $("demPath").value = cfg.dem.path;
  $("demVertical").value = cfg.dem.vertical_datum;
  $("demMargin").value = cfg.dem.margin_deg;
  setRadio("orbitMode", cfg.orbit.mode);
  cfg.orbit.files.forEach((p, i) => { $(`orbit${i}`).value = p; });
  $("orbitProvider").value = cfg.orbit.provider;
  $("orbitNetrc").value = cfg.orbit.netrc_file;
  $("winX").value = cfg.ccd.window_x;
  $("winY").value = cfg.ccd.window_y;
  $("minValid").value = cfg.ccd.min_valid_fraction;
  $("edgeErosion").value = cfg.ccd.edge_erosion;
  $("dgammaRange").value = cfg.ccd.dgamma_range;
  $("interp").value = cfg.processing.data_interp_method;
  $("keepIntermediate").checked = cfg.processing.keep_intermediate;
  $("ompThreads").value = cfg.processing.omp_threads ?? "";
  updateVisibility();
  S.dirty = false;
  $("dirtyNote").hidden = true;
}

function readForm() {
  const num = (id) => { const s = $(id).value.trim(); return s === "" ? null : Number(s); };
  const base = S.payload ? S.payload.config : S.info.defaults;
  const r = readRoiInputs();
  const roiAny = [r.west, r.south, r.east, r.north].some((x) => x !== null);
  return {
    version: base.version,
    name: S.name,
    description: $("description").value,
    created: base.created,
    inputs: {
      sensor: sensor(),
      scenes: TAGS.map((_, i) => $(`scene${i}`).value.trim()),
      polarization: $("polarization").value,
      nisar_frequency: $("nisarFrequency").value,
      burst_ids: [...burstSet()],
    },
    roi: roiAny ? r : { west: null, south: null, east: null, north: null },
    grid: { epsg: num("gridEpsg"), spacing: num("gridSpacing") },
    dem: { source: radioVal("demSource"), path: $("demPath").value.trim(), vertical_datum: $("demVertical").value, margin_deg: num("demMargin") },
    orbit: {
      mode: radioVal("orbitMode"),
      files: TAGS.map((_, i) => $(`orbit${i}`).value.trim()),
      provider: $("orbitProvider").value,
      netrc_file: $("orbitNetrc").value.trim(),
    },
    ccd: {
      window_x: num("winX"), window_y: num("winY"), min_valid_fraction: num("minValid"),
      edge_erosion: num("edgeErosion"), dgamma_range: num("dgammaRange"),
    },
    processing: { data_interp_method: $("interp").value, keep_intermediate: $("keepIntermediate").checked, omp_threads: num("ompThreads") },
  };
}

function markDirty() {
  if (!S.name) return;
  S.dirty = true;
  $("dirtyNote").hidden = false;
}

async function saveConfig() {
  const data = await api("POST", pUrl("/config"), { config: readForm() });
  applyPayload(data, { fill: true });
  return data;
}

// ------------------------------------------------------------------ 状態表示
function renderCheck() {
  const p = S.payload;
  if (!p) return;
  let html = "";
  for (const e of p.check.errors) html += `<div class="msg-err">✖ ${esc(e)}</div>`;
  for (const w of p.check.warnings) html += `<div class="msg-warn">⚠ ${esc(w)}</div>`;
  if (!p.check.errors.length) html += `<div class="msg-ok">✔ 実行できます</div>`;
  $("checkBox").innerHTML = html;
}

function renderSteps() {
  const p = S.payload;
  const box = $("steps");
  if (!p) { box.innerHTML = ""; return; }
  const job = p.state.job;
  const jobLabel = { running: "実行中", cancelling: "中止中…", finished: "完了", failed: "失敗", cancelled: "中止しました", interrupted: "中断" };
  let html = `<div class="job-status">${job ? esc(jobLabel[job.status] || job.status) : "未実行"}</div>`;
  const icons = { done: "✔", running: "●", failed: "✖", cancelled: "✖", pending: "○" };
  for (const st of S.info.steps) {
    const s = p.state.steps[st.id] || { status: "pending" };
    const stale = s.stale;
    const cls = stale ? "stale" : s.status;
    let sub = "";
    if (stale) sub = "設定変更あり（再実行されます）";
    else if (s.status === "done" && s.elapsed != null) sub = `${s.elapsed} 秒`;
    else if (s.message) sub = s.message;
    html += `<div class="step ${cls}"><span class="icon">${stale ? "↻" : icons[s.status] || "○"}</span>` +
      `<span class="name">${esc(st.label)}<small>${esc(sub)}</small></span></div>`;
  }
  box.innerHTML = html;
  const running = p.running;
  $("runBtn").disabled = running;
  $("rerunBtn").disabled = running;
  $("cancelBtn").disabled = !running;
  $("saveBtn").disabled = running;
  $("inspectBtn").disabled = running;
}

function renderResults() {
  const pv = S.payload && S.payload.previews;
  const panel = $("resultPanel");
  if (!pv || !Object.keys(pv).length) {
    panel.hidden = true;
    if (resultOverlay) { map.removeLayer(resultOverlay); resultOverlay = null; }
    return;
  }
  panel.hidden = false;
  const stamp = (S.payload.summary && S.payload.summary.created) || "";
  const keys = Object.keys(pv);
  if (!S.previewKey || (S.previewKey !== "none" && !pv[S.previewKey])) S.previewKey = pv.rgb ? "rgb" : keys[0];
  let html = `<label><input type="radio" name="pv" value="none" ${S.previewKey === "none" ? "checked" : ""}> 表示しない</label>`;
  for (const k of keys) {
    html += `<label><input type="radio" name="pv" value="${k}" ${S.previewKey === k ? "checked" : ""}> ${esc(pv[k].label)}</label>`;
  }
  $("resultRadios").innerHTML = html;
  $("resultRadios").querySelectorAll("input").forEach((el) => el.addEventListener("change", () => {
    S.previewKey = el.value; S.previewStamp = ""; showPreview();
  }));
  if (stamp !== S.previewStamp) showPreview();
}
function showPreview() {
  const pv = S.payload.previews;
  const stamp = (S.payload.summary && S.payload.summary.created) || "";
  if (resultOverlay) { map.removeLayer(resultOverlay); resultOverlay = null; }
  $("legend").innerHTML = "";
  S.previewStamp = stamp;
  if (S.previewKey === "none" || !pv[S.previewKey]) return;
  const item = pv[S.previewKey];
  const url = `${pUrl("/preview/" + item.png)}?t=${encodeURIComponent(stamp)}`;
  resultOverlay = L.imageOverlay(url, item.bounds, { opacity: Number($("opacity").value) / 100, interactive: false }).addTo(map);
  if (item.legend) {
    const lg = item.legend;
    $("legend").innerHTML = `<div class="legend-bar" style="background:${lg.gradient}"></div>` +
      `<div class="legend-scale"><span>${lg.vmin}</span><span>${((lg.vmin + lg.vmax) / 2).toFixed(2)}</span><span>${lg.vmax}</span></div>`;
  } else if (S.previewKey === "rgb") {
    $("legend").innerHTML = `<div class="hint">赤: 災害前は高く、災害後に低下（変化の可能性）<br>白: 両期間とも高い（安定）<br>暗: 両期間とも低い（植生・水域など）<br>シアン: 災害後のみ高い</div>`;
  }
}

function applyPayload(data, { fill = false } = {}) {
  S.payload = data;
  if (fill) fillForm(data.config);
  $("projectRoot").textContent = data.root;
  renderSceneInfo();
  renderGridInfo();
  renderSteps();
  renderCheck();
  renderResults();
  // 定期更新では、シーン・グリッドが変わったときだけ地図を描き直す（ツールチップのちらつき防止）
  const key = JSON.stringify([data.scenes && data.scenes.common_bounds, data.scenes && TAGS.map((t) => data.scenes.scenes[t].path),
    data.grid && data.grid.lonlat_bounds, data.grid && data.grid.units.length]);
  if (fill || key !== S.drawKey) { S.drawKey = key; drawScenes(); }
}

// ------------------------------------------------------------------ ログ
function colorize(text) {
  return text.split("\n").map((line) => {
    const e = esc(line);
    if (/^=====|^-----/.test(line)) return `<span class="head">${e}</span>`;
    if (/エラー|ERROR|Traceback|Error:|失敗/.test(line)) return `<span class="err">${e}</span>`;
    if (/処理が完了しました/.test(line)) return `<span class="ok">${e}</span>`;
    return e;
  }).join("\n");
}
const ANSI_RE = /\x1b\[[0-9;]*[A-Za-z]/g;
async function pollLog(reset) {
  if (!S.name) return;
  if (reset) { S.logOffset = 0; $("log").innerHTML = ""; }
  const sel = $("logSelect").value || "";
  const q = `?offset=${S.logOffset}${sel ? "&name=" + encodeURIComponent(sel) : ""}`;
  const data = await api("GET", pUrl("/log") + q);
  if (data.name !== S.logName) {
    S.logName = data.name;
    if (S.logOffset !== 0) { S.logOffset = 0; $("log").innerHTML = ""; return pollLog(false); }
  }
  if (data.text) {
    const pre = $("log");
    pre.insertAdjacentHTML("beforeend", colorize(data.text.replace(ANSI_RE, "")));
    if ($("logFollow").checked) pre.scrollTop = pre.scrollHeight;
  }
  S.logOffset = data.offset;
}
async function refreshLogList(selectLatest) {
  if (!S.name) return;
  const data = await api("GET", pUrl("/logs"));
  const sel = $("logSelect");
  const cur = sel.value;
  sel.innerHTML = `<option value="">最新の実行</option>` + data.logs.map((f) => `<option value="${esc(f)}">${esc(f)}</option>`).join("");
  if (!selectLatest && data.logs.includes(cur)) sel.value = cur;
}

// ------------------------------------------------------------------ プロジェクト
async function loadProjects(selectName) {
  const data = await api("GET", "/api/projects");
  const sel = $("projectSelect");
  sel.innerHTML = `<option value="">（選択してください）</option>` + data.projects.map((p) =>
    `<option value="${esc(p.name)}">${esc(p.name)}${p.running ? " ●実行中" : ""}（${p.steps_done}/${p.steps_total}）${p.description ? " - " + esc(p.description) : ""}</option>`).join("");
  if (selectName) sel.value = selectName;
}
async function openProject(name) {
  if (S.dirty && S.name && !confirm("未保存の変更があります。破棄して切り替えますか？")) {
    $("projectSelect").value = S.name;
    return;
  }
  S.name = name;
  S.payload = null;
  S.previewKey = ""; S.previewStamp = "";
  if (resultOverlay) { map.removeLayer(resultOverlay); resultOverlay = null; }
  if (centerMarker) { roiLayer.removeLayer(centerMarker); centerMarker = null; }
  $("log").innerHTML = "";
  S.logOffset = 0; S.logName = null;
  const has = !!name;
  $("configForm").hidden = !has;
  $("actions").hidden = !has;
  $("noProject").hidden = has;
  if (!has) { footprintLayer.clearLayers(); burstLayer.clearLayers(); roiLayer.clearLayers(); gridLayer.clearLayers(); $("steps").innerHTML = ""; $("projectRoot").textContent = ""; return; }
  localStorage.setItem("ccd3.lastProject", name);
  const data = await api("GET", pUrl());
  applyPayload(data, { fill: true });
  S.wasRunning = data.running;
  await refreshLogList(true);
  await pollLog(true);
  const b = drawScenes();
  if (roiRect) map.fitBounds(roiRect.getBounds().pad(0.5));
  else if (b && b.length) map.fitBounds(L.latLngBounds(b).pad(0.1));
}

// ------------------------------------------------------------------ ファイルブラウザ
let fsTarget = null, fsExt = "";
async function fsOpen(targetId, ext) {
  fsTarget = targetId; fsExt = ext || "";
  const cur = $(targetId).value.trim();
  $("fsFilter").textContent = fsExt ? `表示中の拡張子: ${fsExt}` : "";
  $("fsDlg").showModal();
  await fsList(cur || localStorage.getItem("ccd3.lastDir") || "");
}
async function fsList(path) {
  try {
    const d = await api("GET", `/api/fs?path=${encodeURIComponent(path)}&ext=${encodeURIComponent(fsExt)}`);
    $("fsPath").value = d.path;
    $("fsUp").dataset.path = d.parent || "";
    const ul = $("fsList");
    ul.innerHTML = "";
    for (const n of d.dirs) {
      const li = document.createElement("li");
      li.className = "dir"; li.textContent = n;
      li.onclick = () => fsList(d.path.replace(/\/$/, "") + "/" + n);
      ul.appendChild(li);
    }
    for (const f of d.files) {
      const li = document.createElement("li");
      li.className = "file";
      li.innerHTML = `<span>${esc(f.name)}</span><span class="size">${fmtBytes(f.size)}</span>`;
      li.onclick = () => {
        $(fsTarget).value = d.path.replace(/\/$/, "") + "/" + f.name;
        localStorage.setItem("ccd3.lastDir", d.path);
        $("fsDlg").close();
        markDirty();
      };
      ul.appendChild(li);
    }
    if (!d.dirs.length && !d.files.length) ul.innerHTML = `<li class="muted">（該当するファイルがありません）</li>`;
  } catch (e) { alert(e.message); }
}

// ------------------------------------------------------------------ 定期更新
let pollBusy = false, tick = 0;
async function poll() {
  if (!S.name || pollBusy) return;
  pollBusy = true;
  try {
    tick++;
    const running = S.payload && S.payload.running;
    if (running || tick % 4 === 0) {
      const data = await api("GET", pUrl());
      applyPayload(data);
      if (S.wasRunning && !data.running) { await refreshLogList(false); loadProjects(S.name); }
      S.wasRunning = data.running;
    }
    if (running || tick % 4 === 0) await pollLog(false);
  } catch (_) { /* サーバー停止中など */ }
  finally { pollBusy = false; }
}

// ------------------------------------------------------------------ イベント
function bind() {
  buildPathRows("sceneRows", "scene", (i) => `シーン ${i + 1} のパス`, "scene");
  buildPathRows("orbitRows", "orbit", (i) => `シーン ${i + 1} の軌道ファイル（.EOF）`, "orbit");

  $("projectSelect").addEventListener("change", (e) => openProject(e.target.value).catch(showError));
  $("newProjectBtn").addEventListener("click", () => {
    $("newName").value = ""; $("newDesc").value = ""; $("newProjectErr").textContent = "";
    $("newProjectDlg").showModal();
  });
  $("newCancel").addEventListener("click", () => $("newProjectDlg").close());
  $("newProjectForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("POST", "/api/projects", { name: $("newName").value.trim(), description: $("newDesc").value });
      $("newProjectDlg").close();
      S.dirty = false;
      await loadProjects(r.name);
      await openProject(r.name);
    } catch (err) { $("newProjectErr").textContent = err.message; }
  });

  $("configForm").addEventListener("input", (e) => {
    if (e.target.id && e.target.id.startsWith("cred")) return;
    markDirty();
    if (e.target.name === "sensor" || e.target.name === "demSource" || e.target.name === "orbitMode") updateVisibility();
    if (["roiWest", "roiSouth", "roiEast", "roiNorth"].includes(e.target.id)) drawRoi();
    if (e.target.id === "burstIds") drawScenes();
    if (e.target.id === "sizeEwKm" && $("squareLock").checked) $("sizeNsKm").value = $("sizeEwKm").value;
  });
  $("configForm").addEventListener("change", (e) => {
    if (e.target.type === "radio" || e.target.id === "squareLock") updateVisibility();
  });
  $("configForm").addEventListener("submit", (e) => e.preventDefault());

  document.addEventListener("click", (e) => {
    const b = e.target.closest("button.browse");
    if (!b) return;
    let ext = b.dataset.ext || "";
    if (b.dataset.extFn === "scene") ext = sceneExt();
    if (b.dataset.extFn === "orbit") ext = ".eof";
    fsOpen(b.dataset.target, ext);
  });
  $("fsUp").addEventListener("click", () => { if ($("fsUp").dataset.path) fsList($("fsUp").dataset.path); });
  $("fsGo").addEventListener("click", () => fsList($("fsPath").value));
  $("fsPath").addEventListener("keydown", (e) => { if (e.key === "Enter") fsList($("fsPath").value); });
  $("fsCancel").addEventListener("click", () => $("fsDlg").close());

  // ROI
  $("drawRoiBtn").addEventListener("click", () => setRoiMode(S.roiMode === "rect" ? null : "rect"));
  $("pickCenterBtn").addEventListener("click", () => setRoiMode(S.roiMode === "center" ? null : "center"));
  $("roiFromCenterBtn").addEventListener("click", () => { try { applyCenter(true); } catch (e) { showError(e); } });
  $("roiClearBtn").addEventListener("click", () => {
    writeRoiInputs({ west: null, south: null, east: null, north: null });
    if (centerMarker) { roiLayer.removeLayer(centerMarker); centerMarker = null; }
    drawRoi(); markDirty();
  });
  $("roiFromCommonBtn").addEventListener("click", () => {
    const sc = S.payload && S.payload.scenes;
    if (!sc) return showError(new Error("先に「保存してシーン情報を読む」を実行してください"));
    const [w, s, e, n] = sc.common_bounds;
    setRoi({ west: w, south: s, east: e, north: n }, { fit: true });
  });

  $("inspectBtn").addEventListener("click", async () => {
    const btn = $("inspectBtn");
    btn.disabled = true; btn.textContent = "読み込み中…";
    try {
      await saveConfig();
      const data = await api("POST", pUrl("/inspect"), {});
      applyPayload(data);
      const b = drawScenes();
      if (b && b.length) map.fitBounds(L.latLngBounds(b).pad(0.1));
    } catch (e) { showError(e); }
    finally { btn.disabled = false; btn.textContent = "保存してシーン情報を読む"; }
  });

  $("saveBtn").addEventListener("click", async () => {
    try { await saveConfig(); toast("保存しました"); renderCheck(); } catch (e) { showError(e); }
  });
  const credentials = () => ({
    cdse_user: $("credCdseUser").value, cdse_password: $("credCdsePass").value,
    asf_user: $("credAsfUser").value, asf_password: $("credAsfPass").value,
  });
  async function run(body) {
    try {
      await saveConfig();
      if (S.payload.check.errors.length) { renderCheck(); return; }
      await api("POST", pUrl("/run"), { ...body, credentials: credentials() });
      $("logSelect").value = "";
      S.wasRunning = true;
      const data = await api("GET", pUrl());
      applyPayload(data);
      await refreshLogList(true);
      await pollLog(true);
    } catch (e) { showError(e); }
  }
  $("runBtn").addEventListener("click", () => run({}));
  $("rerunBtn").addEventListener("click", () => run({ from_step: $("fromStep").value }));
  $("cancelBtn").addEventListener("click", async () => {
    if (!confirm("実行中の処理を中止しますか？")) return;
    try { await api("POST", pUrl("/cancel"), {}); } catch (e) { showError(e); }
  });
  $("deleteBtn").addEventListener("click", async () => {
    if (!confirm(`プロジェクト「${S.name}」を完全に削除します。よろしいですか？`)) return;
    try {
      await api("POST", pUrl("/delete"), { confirm: $("deleteConfirm").value });
      S.dirty = false;
      localStorage.removeItem("ccd3.lastProject");
      await loadProjects("");
      await openProject("");
    } catch (e) { showError(e); }
  });

  $("opacity").addEventListener("input", () => { if (resultOverlay) resultOverlay.setOpacity(Number($("opacity").value) / 100); });
  $("logSelect").addEventListener("change", () => pollLog(true));
  $("outputsBtn").addEventListener("click", async () => {
    if (!S.name) return;
    try {
      const d = await api("GET", pUrl("/outputs"));
      $("outputsList").innerHTML = d.files.length
        ? `<p class="hint mono">${esc(d.dir)}</p><table>` + d.files.map((f) => `<tr><td class="mono">${esc(f.name)}</td><td>${fmtBytes(f.size)}</td></tr>`).join("") + "</table>"
        : "<p>まだ成果物がありません。</p>";
      $("outputsDlg").showModal();
    } catch (e) { showError(e); }
  });
  $("outputsClose").addEventListener("click", () => $("outputsDlg").close());

  $("shutdownBtn").addEventListener("click", async () => {
    if (!confirm("GUI サーバーを終了しますか？（実行中の処理はバックグラウンドで継続します）")) return;
    try { await api("POST", "/api/shutdown", {}); } catch (_) { /* 切断 */ }
    document.body.innerHTML = "<p style='padding:2em'>サーバーを終了しました。このタブを閉じてください。</p>";
  });
  window.addEventListener("beforeunload", (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });
}

async function init() {
  bind();
  S.info = await api("GET", "/api/info");
  $("fromStep").innerHTML = S.info.steps.map((s) => `<option value="${s.id}">${esc(s.label)}（${s.id}）</option>`).join("");
  await loadProjects();
  const last = localStorage.getItem("ccd3.lastProject");
  if (last && [...$("projectSelect").options].some((o) => o.value === last)) {
    $("projectSelect").value = last;
    await openProject(last);
  }
  setInterval(poll, 1500);
}

init().catch((e) => { console.error(e); alert("初期化に失敗しました: " + e.message); });
