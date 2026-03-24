async function api(path, options = {}) {
  const token = localStorage.getItem("accessToken") || "";
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(path, { headers, ...options });
  if (res.status === 401) {
    localStorage.removeItem("accessToken");
    throw new Error("401 토큰 만료 또는 인증 실패");
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

function decodeJwt(token) {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = b64 + "===".slice((b64.length + 3) % 4);
    return JSON.parse(atob(padded));
  } catch (_) {
    return null;
  }
}

function isTokenExpired(token) {
  const p = decodeJwt(token);
  if (!p || !p.exp) return false;
  return Math.floor(Date.now() / 1000) >= Number(p.exp);
}

const defaultLiveBaseUrl = `${window.location.protocol}//${window.location.hostname}:8889`;
let liveBaseUrl = localStorage.getItem("liveBaseUrl") || defaultLiveBaseUrl;
let roiCameraSelection = "";
let roiZonesState = [];
let roiSelectedIndex = -1;
let roiDragMode = null;
let roiDragStart = null;
let roiDrawRect = null;
let roiDrawShape = "rect";
let roiPolygonDraft = [];
let roiSnapshotDataUrl = "";
let currentUserRole = null;
let authEnabled = null;
let discoverResults = [];
let discoverJobTimer = null;
let aiDebugTimer = null;
let aiDebugBusy = false;
let eventPacksState = [];
let webrtcEnabled = true;
let camerasState = [];
let eventViewClearBeforeMs = Number(localStorage.getItem("eventViewClearBeforeMs") || "0");
let dashboardCameraFilter = "__all__";
let dashboardSnapshotIntervalHours = Number(localStorage.getItem("dashboardSnapshotIntervalHours") || "1");
let dashboardSnapshotTimer = null;
let dashboardSnapshotMeta = {};
let dashboardSnapshotCache = {};
let dashboardLastEvents = [];
let dashboardLastCameras = [];
let pendingDeleteCameraId = "";
let saveInFlightCount = 0;
let helpTooltipEl = null;
let helpTooltipAnchor = null;
let helpTooltipText = "";
let lastCameraSettingsUpdatedAt = Number(localStorage.getItem("cameraSettingsUpdatedAt") || "0");
const MAIN_TAB_KEY = "mainUiTab";
const CAMERA_ROTATE_KEY = "cameraRotateMap";
const MAIN_TAB_SECTIONS = {
  operation: ["section-dashboard"],
  settings: ["section-camera", "section-roi", "section-policy", "section-route", "section-ai"],
  system: ["section-live", "section-ai-debug", "section-auth"],
};
const LEGACY_TAB_MAP = {
  camera: "settings",
  roi: "settings",
  event: "operation",
  model: "settings",
  system: "system",
};

function normalizeMainTab(tab) {
  if (MAIN_TAB_SECTIONS[tab]) return tab;
  if (LEGACY_TAB_MAP[tab]) return LEGACY_TAB_MAP[tab];
  return "operation";
}

function applyPageSectionFilter() {
  const pageSection = window.PAGE_SECTION;
  if (!pageSection) return;
  const sections = Array.from(document.querySelectorAll("main > section"));
  sections.forEach((sec) => {
    const keep = sec.id === "section-auth" || sec.id === pageSection;
    sec.style.display = keep ? "" : "none";
  });
  const links = Array.from(document.querySelectorAll(".quick-nav a"));
  links.forEach((a) => {
    const href = a.getAttribute("href") || "";
    const isActive = href.includes(pageSection.replace("section-", "page-"));
    if (isActive) a.classList.add("active");
  });
}

function applyMainTab(tab) {
  const tabId = normalizeMainTab(tab);
  const visible = new Set(MAIN_TAB_SECTIONS[tabId]);
  Array.from(document.querySelectorAll("main > section")).forEach((sec) => {
    sec.style.display = visible.has(sec.id) ? "" : "none";
  });
  Array.from(document.querySelectorAll(".quick-nav [data-tab]")).forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-tab") === tabId);
  });
  localStorage.setItem(MAIN_TAB_KEY, tabId);
}

function initMainTabs() {
  if (window.PAGE_SECTION) return;
  const tabButtons = Array.from(document.querySelectorAll(".quick-nav [data-tab]"));
  if (!tabButtons.length) return;
  const hashTab = (window.location.hash || "").replace(/^#tab-/, "");
  const savedTab = localStorage.getItem(MAIN_TAB_KEY) || "";
  const defaultTab = "operation";
  const initialTab = MAIN_TAB_SECTIONS[normalizeMainTab(hashTab)]
    ? normalizeMainTab(hashTab)
    : MAIN_TAB_SECTIONS[normalizeMainTab(savedTab)]
    ? normalizeMainTab(savedTab)
    : defaultTab;
  applyMainTab(initialTab);
  tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const tabId = btn.getAttribute("data-tab") || defaultTab;
      applyMainTab(tabId);
      window.location.hash = `tab-${tabId}`;
    });
  });
}

function bindPanelHelpIcons() {
  const blocks = Array.from(document.querySelectorAll(".helpable"));
  blocks.forEach((el) => {
    const msg = (el.getAttribute("data-help") || "").trim();
    if (!msg) return;
    const title = el.querySelector("h2");
    if (!title || title.querySelector(".help-pop")) return;
    title.classList.add("panel-title");
    const pop = document.createElement("span");
    pop.className = "help-pop";
    pop.innerHTML = '<button type="button" class="help-icon" aria-label="섹션 설명">?</button><span class="help-tip" role="tooltip"></span>';
    const tip = pop.querySelector(".help-tip");
    if (tip) tip.textContent = msg;
    const icon = pop.querySelector(".help-icon");
    if (icon) {
      icon.addEventListener("mouseenter", () => showGlobalHelpTooltip(icon, msg));
      icon.addEventListener("mouseleave", hideGlobalHelpTooltip);
      icon.addEventListener("focus", () => showGlobalHelpTooltip(icon, msg));
      icon.addEventListener("blur", hideGlobalHelpTooltip);
    }
    title.appendChild(pop);
  });
}

function ensureGlobalHelpTooltip() {
  if (helpTooltipEl) return helpTooltipEl;
  const el = document.createElement("div");
  el.id = "globalHelpTooltip";
  el.className = "help-tip-floating";
  document.body.appendChild(el);
  helpTooltipEl = el;
  return el;
}

function placeGlobalHelpTooltip() {
  if (!helpTooltipEl || !helpTooltipAnchor) return;
  const gap = 8;
  const margin = 10;
  const r = helpTooltipAnchor.getBoundingClientRect();
  helpTooltipEl.style.visibility = "hidden";
  helpTooltipEl.classList.add("show");
  const tr = helpTooltipEl.getBoundingClientRect();

  let left = r.left;
  if (left + tr.width > window.innerWidth - margin) left = window.innerWidth - tr.width - margin;
  if (left < margin) left = margin;

  let top = r.bottom + gap;
  if (top + tr.height > window.innerHeight - margin) top = r.top - tr.height - gap;
  if (top < margin) top = margin;

  helpTooltipEl.style.left = `${Math.round(left)}px`;
  helpTooltipEl.style.top = `${Math.round(top)}px`;
  helpTooltipEl.style.visibility = "visible";
}

function showGlobalHelpTooltip(anchor, text) {
  const el = ensureGlobalHelpTooltip();
  helpTooltipAnchor = anchor;
  helpTooltipText = text || "";
  el.textContent = helpTooltipText;
  placeGlobalHelpTooltip();
}

function hideGlobalHelpTooltip() {
  if (!helpTooltipEl) return;
  helpTooltipEl.classList.remove("show");
  helpTooltipAnchor = null;
  helpTooltipText = "";
}

function ensureEdgeControls() {
  const roiToolbar = document.querySelector("#section-roi .roi-toolbar");
  if (roiToolbar && !document.getElementById("roiSnapshotBtn")) {
    const btnSnap = document.createElement("button");
    btnSnap.type = "button";
    btnSnap.id = "roiSnapshotBtn";
    btnSnap.textContent = "스냅샷 캡처";
    roiToolbar.appendChild(btnSnap);

    const btnRect = document.createElement("button");
    btnRect.type = "button";
    btnRect.id = "roiRectModeBtn";
    btnRect.textContent = "사각형 그리기";
    roiToolbar.appendChild(btnRect);

    const btnPoly = document.createElement("button");
    btnPoly.type = "button";
    btnPoly.id = "roiPolyModeBtn";
    btnPoly.textContent = "다각형 그리기";
    roiToolbar.appendChild(btnPoly);

    const btnPolyDone = document.createElement("button");
    btnPolyDone.type = "button";
    btnPolyDone.id = "roiPolyDoneBtn";
    btnPolyDone.textContent = "다각형 완료";
    roiToolbar.appendChild(btnPolyDone);
  }

  const aiSection = document.getElementById("section-ai");
  if (aiSection && !document.getElementById("cameraModelForm")) {
    const wrap = document.createElement("div");
    wrap.innerHTML = `
      <h2>모델 설정 (카메라별)</h2>
      <form id="cameraModelForm" class="grid">
        <select id="cameraModelCameraId" name="cameraId" required></select>
        <select name="enabled" id="cameraModelEnabled">
          <option value="true">카메라 모델 사용</option>
          <option value="false">카메라 모델 미사용</option>
        </select>
        <select id="cameraModelPreset">
          <option value="">모델 파일 선택 (자동 검색)</option>
        </select>
        <button type="button" id="cameraModelListRefreshBtn">모델 목록 새로고침</button>
        <button type="button" id="cameraModelPresetApplyBtn">선택 모델 적용</button>
        <input name="modelPath" id="cameraModelPath" placeholder="models/my_model.onnx" />
        <div class="sensitivity-control" title="민감도(낮음-높음), 기본 35%">
          <label for="cameraModelConf">민감도</label>
          <input name="confidenceThreshold" id="cameraModelConf" type="range" min="0.05" max="0.95" step="0.05" value="0.35" />
          <output id="cameraModelConfValue" for="cameraModelConf">35%</output>
        </div>
        <input name="timeoutSec" id="cameraModelTimeoutSec" type="number" min="1" value="5" />
        <input name="pollSec" id="cameraModelPollSec" type="number" min="1" value="2" />
        <input name="cooldownSec" id="cameraModelCooldownSec" type="number" min="0" value="10" />
        <button type="submit">카메라 모델 저장</button>
      </form>
      <div id="cameraModelPresetInfo" class="list"></div>
      <div id="cameraModelInfo" class="list"></div>
    `;
    aiSection.appendChild(wrap);
  }

  const policySection = document.getElementById("section-policy");
  if (policySection && !document.getElementById("eventPackForm")) {
    const wrap = document.createElement("div");
    wrap.innerHTML = `
      <h2>이벤트 팩 적용</h2>
      <form id="eventPackForm" class="grid">
        <select id="eventPackCameraId" name="cameraId" required></select>
        <select id="eventPackEnabled" name="enabled">
          <option value="true">사용</option>
          <option value="false">미사용</option>
        </select>
        <select id="eventPackId" name="packId"></select>
        <input id="eventPackVersion" name="packVersion" placeholder="1.0.0" />
        <textarea id="eventPackParams" name="params" rows="4" placeholder='{"person_cross_roi":{"roiName":"zone-1"}}'></textarea>
        <button type="submit">이벤트 팩 저장</button>
      </form>
      <div id="eventPackInfo" class="list"></div>
    `;
    policySection.insertBefore(wrap, policySection.querySelector("#policyForm"));
  }

  const liveForm = document.getElementById("liveConfigForm");
  if (liveForm && !document.getElementById("webrtcToggleForm")) {
    const form = document.createElement("form");
    form.id = "webrtcToggleForm";
    form.className = "grid";
    form.innerHTML = `
      <select id="webrtcEnabled">
        <option value="true">WebRTC 사용</option>
        <option value="false">WebRTC 미사용</option>
      </select>
      <button type="submit">WebRTC 설정 저장</button>
    `;
    liveForm.parentNode.insertBefore(form, liveForm.nextSibling);
  }
}

function optionHtml(list, labelKey = "name") {
  return list.map((x) => `<option value="${x.id}">${x[labelKey]} (${x.id.slice(0, 8)})</option>`).join("");
}

function selectHasValue(selectEl, value) {
  if (!selectEl || value == null || value === "") return false;
  return Array.from(selectEl.options || []).some((opt) => opt.value === String(value));
}

function setSelectOptionsKeepingValue(selectEl, html, preferredValue = "") {
  if (!selectEl) return;
  const prev = selectEl.value;
  selectEl.innerHTML = html;
  if (preferredValue && selectHasValue(selectEl, preferredValue)) {
    selectEl.value = preferredValue;
    return;
  }
  if (prev && selectHasValue(selectEl, prev)) {
    selectEl.value = prev;
  }
}

function isFormEditing(formId) {
  const form = document.getElementById(formId);
  if (!form) return false;
  const active = document.activeElement;
  return !!active && form.contains(active);
}

function shouldSyncForm(formId) {
  return saveInFlightCount === 0 && !isFormEditing(formId);
}

async function runWithSaving(formId, fn) {
  const form = document.getElementById(formId);
  saveInFlightCount += 1;
  if (form) form.setAttribute("data-saving", "true");
  try {
    return await fn();
  } finally {
    if (form) form.removeAttribute("data-saving");
    saveInFlightCount = Math.max(0, saveInFlightCount - 1);
  }
}

function row(el, text, cls = "") {
  const div = document.createElement("div");
  div.className = `item ${cls}`;
  div.textContent = text;
  el.appendChild(div);
}

function fill(el, rows) {
  el.innerHTML = "";
  rows.forEach((r) => row(el, r));
}

function fillHtml(el, rows) {
  el.innerHTML = "";
  rows.forEach((html) => {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = html;
    el.appendChild(div);
  });
}


function escapeHtml(v) {
  return String(v ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function parseJsonObjectOrNull(raw, fieldName) {
  const txt = String(raw || "").trim();
  if (!txt) return null;
  let parsed;
  try {
    parsed = JSON.parse(txt);
  } catch (err) {
    throw new Error(`${fieldName} JSON 오류: ${err.message}`);
  }
  if (parsed == null) return null;
  if (typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${fieldName}는 JSON 객체여야 합니다.`);
  }
  return parsed;
}

function severityClass(sev) {
  if (sev === "high") return "sev-high";
  if (sev === "medium") return "sev-medium";
  return "sev-low";
}

function formatTsLocal(ts) {
  if (!ts) return "-";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  const parts = new Intl.DateTimeFormat(undefined, {
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 3,
  }).formatToParts(d);
  const values = Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}.${values.fractionalSecond}`;
}

function statusChip(ok, okText = "정상", badText = "장애") {
  return ok ? `<span class="ok">${escapeHtml(okText)}</span>` : `<span class="warn">${escapeHtml(badText)}</span>`;
}

function monitorStateMeta(ok, okText = "정상", badText = "오프라인") {
  return {
    className: ok ? "is-online" : "is-offline",
    label: ok ? okText : badText,
  };
}

function monitorCardHtml(title, ok, lines, opts = {}) {
  const meta = monitorStateMeta(ok, opts.okText || "정상", opts.badText || "오프라인");
  const body = (Array.isArray(lines) ? lines : [])
    .filter(Boolean)
    .map((line) => `<div class="monitor-card-line">${line}</div>`)
    .join("");
  const kind = opts.kind ? ` monitor-card-${escapeHtml(opts.kind)}` : "";
  return `
    <article class="monitor-card ${meta.className}${kind}">
      <div class="monitor-card-head">
        <strong>${escapeHtml(title)}</strong>
        <span class="monitor-badge ${meta.className}">${escapeHtml(meta.label)}</span>
      </div>
      <div class="monitor-card-body">${body}</div>
    </article>
  `;
}

function httpProbeText(probe) {
  if (!probe || typeof probe !== "object") return "미확인";
  if (probe.reachable !== true) return probe.error || "미도달";
  const code = probe.httpStatus != null ? `HTTP ${probe.httpStatus}` : "HTTP -";
  const latency = probe.latencyMs != null ? `, ${probe.latencyMs}ms` : "";
  return `${code}${latency}`;
}

function renderMonitorSummaryHtml(overview) {
  if (!overview || typeof overview !== "object") return [];
  const edge = overview.edge || {};
  const recorder = edge.recorder || {};
  const dxnn = edge.dxnnHost || {};
  const checkedAt = formatTsLocal(overview.checkedAt);
  const rows = [
    monitorCardHtml(
      "VMS API",
      edge.api?.ok === true,
      [
        `점검시각: ${escapeHtml(checkedAt)}`,
        `단말명: ${escapeHtml(edge.deviceName || "-")}`,
      ],
      { kind: "api" }
    ),
    monitorCardHtml(
      "데이터베이스/레코더",
      edge.database?.ok === true && recorder.ok === true,
      [
        `DB: ${statusChip(edge.database?.ok === true, "정상", "오프라인")}`,
        `Recorder: ${statusChip(recorder.ok === true, "정상", "지연")}`,
        `연결 카메라: ${escapeHtml(String(recorder.connectedCameraCount ?? 0))}/${escapeHtml(String(recorder.cameraCount ?? 0))}`,
        `최근 Probe: ${escapeHtml(formatTsLocal(recorder.lastProbeAt))}`,
      ],
      { kind: "recorder", okText: "정상", badText: "점검 필요" }
    ),
    monitorCardHtml(
      "추론부 DXNN/DXRT",
      dxnn.ok === true,
      [
        `상태: ${escapeHtml(httpProbeText(dxnn))}`,
        `헬스체크: ${escapeHtml(dxnn.healthUrl || "-")}`,
        `추론 URL: ${escapeHtml(dxnn.inferUrl || "-")}`,
      ],
      { kind: "dxnn", okText: "연결됨", badText: "오프라인" }
    ),
  ];
  const destinations = Array.isArray(overview.destinations) ? overview.destinations : [];
  destinations.forEach((dest) => {
    const probe = dest?.probe || {};
    const lastDelivery = dest?.lastDeliveryStatus ? `${dest.lastDeliveryStatus} @ ${formatTsLocal(dest.lastDeliveryAt)}` : "이력 없음";
    rows.push(
      monitorCardHtml(
        `라우팅 서버 ${dest?.name || "-"}`,
        probe.ok === true,
        [
          `연결 상태: ${escapeHtml(httpProbeText(probe))}`,
          `URL: ${escapeHtml(dest?.url || "-")}`,
          `최근 전송: ${escapeHtml(lastDelivery)}`,
        ],
        { kind: "server", okText: "연결됨", badText: "오프라인" }
      )
    );
  });
  return rows;
}

function renderMonitorLinkHtml(overview, fallbackRows) {
  const links = Array.isArray(overview?.links) ? overview.links : [];
  const dxnnOk = overview?.edge?.dxnnHost?.ok === true;
  if (links.length) {
    return links.map((link) => {
      const hop = link.cameraToEdge || {};
      const cameraOk = hop.connected === true && hop.stale !== true;
      const ringOk = hop.ringRunning === true;
      const downstream = Array.isArray(link.edgeToServer) ? link.edgeToServer : [];
      const serverText = downstream.length
        ? downstream
            .map((item) => {
              const server = item?.server || {};
              const probe = server?.probe || {};
              const name = escapeHtml(item?.destinationName || "-");
              return `${name}: ${probe.ok === true ? "정상" : "오프라인"}`;
            })
            .join("<br />")
        : "라우팅 없음";
      return monitorCardHtml(
        link.name || "-",
        cameraOk && ringOk && dxnnOk,
        [
          `카메라 연결: ${statusChip(cameraOk, "정상", "오프라인")}`,
          `레코더 상태: ${statusChip(ringOk, "동작중", "정지")}`,
          `추론부 연결: ${statusChip(dxnnOk, "정상", "오프라인")}`,
          `라우팅 서버: ${serverText}`,
          `최근 Probe: ${escapeHtml(formatTsLocal(hop.lastProbeAt))}`,
          `사유: ${escapeHtml(hop.lastConnectReason || "-")}`,
        ],
        { kind: "camera", okText: "정상", badText: "문제 있음" }
      );
    });
  }
  const rows = Array.isArray(fallbackRows) ? fallbackRows : [];
  return rows.map((m) => {
    const connected = m.connected === true;
    const ring = m.ringRunning === true;
    return monitorCardHtml(
      m.name || "-",
      connected && ring,
      [
        `카메라 연결: ${statusChip(connected, "정상", "오프라인")}`,
        `레코더 상태: ${statusChip(ring, "동작중", "정지")}`,
        `상태: ${escapeHtml(m.status || "-")}`,
        `재시작 횟수: ${escapeHtml(String(m.ringRestartCount || 0))}`,
        `사유: ${escapeHtml(m.lastConnectReason || "-")}`,
      ],
      { kind: "camera", okText: "정상", badText: "문제 있음" }
    );
  });
}

function normalizeSnapshotIntervalHours(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return 1;
  return Math.max(0.1, Math.min(168, n));
}

function dedupeCamerasById(rows) {
  const src = Array.isArray(rows) ? rows : [];
  const map = new Map();
  src.forEach((c) => {
    const id = String(c?.id || "").trim();
    if (!id) return;
    if (!map.has(id)) map.set(id, c);
  });
  return Array.from(map.values());
}

function isDevState() {
  return authEnabled === false;
}

function getCameraIpFromRtsp(rtspUrl) {
  try {
    const u = new URL(rtspUrl);
    return u.hostname || "-";
  } catch (_) {
    return "-";
  }
}

function setSnapshotIntervalInfo(text, cls = "") {
  const el = document.getElementById("snapshotIntervalInfo");
  if (!el) return;
  el.textContent = text || "";
  el.className = cls || "";
}

function applyDashboardFieldHints() {
  const hints = [
    ["#snapshotIntervalHours", "대시보드 스냅샷 갱신 주기(시간)입니다."],
    ["#authUsername", "로그인 아이디를 입력합니다."],
    ["#authPassword", "로그인 비밀번호를 입력합니다."],
    ["#cameraForm [name='name']", "카메라 표시 이름입니다."],
    ["#cameraForm [name='rtspUrl']", "카메라 RTSP 주소를 입력합니다."],
    ["#cameraForm [name='webrtcPath']", "WebRTC 송출 경로 식별자입니다. 예: cam-01"],
    ["#cameraRotateCameraId", "회전할 카메라를 선택합니다."],
    ["#discoverCidr", "검색할 네트워크 대역(CIDR)입니다. auto 사용 가능"],
    ["#discoverPorts", "탐색할 RTSP 포트 목록입니다. 쉼표로 구분"],
    ["#discoverUseOnvif", "ONVIF 탐색 포함 여부를 선택합니다."],
    ["#discoverOnvifTimeoutMs", "ONVIF 응답 대기시간(밀리초)입니다."],
    ["#discoverUsername", "카메라 인증 계정입니다."],
    ["#discoverPassword", "카메라 인증 비밀번호입니다."],
    ["#discoverMaxHosts", "탐색할 최대 호스트 수입니다."],
    ["#discoverTimeoutMs", "RTSP 연결 타임아웃(밀리초)입니다."],
    ["#roiCameraId", "ROI를 설정할 카메라를 선택합니다."],
    ["#roiEnabled", "ROI 기능 사용 여부입니다."],
    ["#roiZones", "ROI 영역 JSON입니다. 직접 수정 가능합니다."],
    ["#eventPackCameraId", "이벤트 팩을 적용할 카메라를 선택합니다."],
    ["#eventPackEnabled", "이벤트 팩 사용 여부입니다."],
    ["#eventPackId", "적용할 이벤트 팩을 선택합니다."],
    ["#eventPackVersion", "이벤트 팩 버전 문자열입니다."],
    ["#eventPackParams", "이벤트 팩 파라미터 JSON입니다."],
    ["#policyCameraId", "정책을 적용할 카메라를 선택합니다."],
    ["#policyForm [name='eventType']", "정책 이벤트 타입 이름입니다."],
    ["#policyMode", "이벤트 저장 방식(클립/스냅샷)을 선택합니다."],
    ["#policyForm [name='preSec']", "클립 저장 시 이벤트 이전 시간(초)입니다."],
    ["#policyForm [name='postSec']", "클립 저장 시 이벤트 이후 시간(초)입니다."],
    ["#policyForm [name='snapshotCount']", "스냅샷 저장 개수입니다."],
    ["#personRuleEnabled", "사람 이벤트 규칙 사용 여부입니다."],
    ["#personRuleDwellSec", "사람 감지 유지시간 임계값(초)입니다."],
    ["#personRuleCooldownSec", "사람 이벤트 재발행 제한 시간(초)입니다."],
    ["#personRuleEventType", "사람 이벤트 타입 이름입니다."],
    ["#personRuleSeverity", "사람 이벤트 심각도를 선택합니다."],
    ["#eventCameraId", "수동 이벤트를 발생시킬 카메라를 선택합니다."],
    ["#eventForm [name='type']", "수동 이벤트 타입 이름입니다."],
    ["#eventForm [name='severity']", "수동 이벤트 심각도를 선택합니다."],
    ["#destinationForm [name='name']", "목적지 식별용 이름입니다."],
    ["#destinationForm [name='url']", "CCTV 이미지 수집 API URL입니다."],
    ["#destinationForm [name='terminalId']", "필수 terminalId 입니다."],
    ["#destinationForm [name='cctvId']", "기본 cctvId입니다(카메라 매핑 없을 때)."],
    ["#destinationForm [name='cctvIdByCameraId']", "cameraId -> cctvId 매핑 JSON입니다."],
    ["#destinationForm [name='authTokenEnv']", "Bearer 토큰 환경변수 이름입니다(선택)."],
    ["#routeCameraId", "라우팅 대상 카메라입니다."],
    ["#routeForm [name='artifactKindFixed']", "현재 통신방식은 스냅샷 전송만 사용합니다."],
    ["#routeDestinationId", "전송받을 목적지를 선택합니다."],
    ["#aiEnabled", "공통 모델 사용 여부입니다."],
    ["#aiModelPreset", "자동 검색된 모델 파일 목록입니다."],
    ["#aiModelPath", "공통 모델 파일 경로입니다."],
    ["#aiTimeoutSec", "추론 타임아웃(초)입니다."],
    ["#aiPollSec", "모델 폴링 주기(초)입니다."],
    ["#aiCooldownSec", "이벤트 재발행 제한 시간(초)입니다."],
    ["#cameraModelCameraId", "카메라별 모델을 설정할 대상 카메라입니다."],
    ["#cameraModelEnabled", "카메라별 모델 사용 여부입니다."],
    ["#cameraModelPreset", "카메라별 모델 파일 목록입니다."],
    ["#cameraModelPath", "카메라별 모델 경로입니다."],
    ["#cameraModelConf", "탐지 민감도(신뢰도 임계값)입니다."],
    ["#cameraModelTimeoutSec", "카메라별 추론 타임아웃(초)입니다."],
    ["#cameraModelPollSec", "카메라별 폴링 주기(초)입니다."],
    ["#cameraModelCooldownSec", "카메라별 쿨다운(초)입니다."],
    ["#webrtcBaseUrl", "WebRTC 서비스 기본 URL입니다."],
    ["#webrtcEnabled", "WebRTC 기능 사용 여부입니다."],
    ["#aiDebugCameraId", "디버그 추론 대상 카메라입니다."],
    ["#aiDebugIntervalMs", "디버그 추론 반복 주기(밀리초)입니다."],
  ];
  hints.forEach(([selector, text]) => {
    const el = document.querySelector(selector);
    if (el) el.title = text;
  });
}

function applyAdminOnlyVisibility(isAdmin) {
  document.querySelectorAll("[data-admin-only]").forEach((el) => {
    el.classList.toggle("is-hidden", !isAdmin);
  });
}

function openSettingsForCamera(cameraId) {
  if (!cameraId) return;
  window.location.href = `/static/camera-settings.html?cameraId=${encodeURIComponent(cameraId)}`;
}

function hideGlobalModelUi() {
  const form = document.getElementById("aiModelForm");
  if (!form) return;
  const title = form.previousElementSibling;
  if (title && title.tagName === "H2") title.style.display = "none";
  form.style.display = "none";
  const preset = document.getElementById("aiModelPresetInfo");
  const info = document.getElementById("aiModelInfo");
  if (preset) preset.style.display = "none";
  if (info) info.style.display = "none";
}

function mountDiscoverIntoModal() {
  const mount = document.getElementById("discoverModalBody");
  const section = document.getElementById("section-discover");
  const form = document.getElementById("discoverForm");
  const info = document.getElementById("discoverInfo");
  const list = document.getElementById("discoverList");
  if (!mount || !section || !form || !info || !list) return;
  if (form.parentElement === mount) return;
  mount.appendChild(form);
  mount.appendChild(info);
  mount.appendChild(list);
  section.classList.add("is-relocated");
}

function openDiscoverModal() {
  const modal = document.getElementById("discoverModal");
  if (!modal) return;
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
}

function closeDiscoverModal() {
  const modal = document.getElementById("discoverModal");
  if (!modal) return;
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
}

function openDeleteCameraModal(cameraId) {
  if (!cameraId) return;
  pendingDeleteCameraId = cameraId;
  const modal = document.getElementById("deleteCameraModal");
  if (!modal) return;
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
}

function closeDeleteCameraModal() {
  pendingDeleteCameraId = "";
  const modal = document.getElementById("deleteCameraModal");
  if (!modal) return;
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
}

async function captureRoiSnapshotForCamera(cameraId) {
  if (!cameraId) return;
  try {
    const snap = await api(`/cameras/${cameraId}/snapshot`, { method: "POST", body: "{}" });
    roiSnapshotDataUrl = snap.imageDataUrl || "";
    fill(document.getElementById("roiInfo"), [`snapshot=${snap.capturedAt || "-"}`, `camera=${cameraId}`]);
    drawRoiCanvas();
  } catch (err) {
    fill(document.getElementById("roiInfo"), [`스냅샷 실패: ${err.message}`]);
  }
}

function renderDashboardFilters(cameras) {
  const el = document.getElementById("dashCameraFilters");
  if (!el) return;
  const btns = ['<button type="button" class="dashboard-filter-btn" data-filter="__all__">ALL</button>'];
  cameras.forEach((c) => {
    btns.push(
      `<button type="button" class="dashboard-filter-btn" data-filter="${escapeHtml(c.id)}">${escapeHtml(c.name)}</button>`
    );
  });
  btns.push('<button type="button" class="dashboard-filter-btn plus" data-action="open-discover-modal">+</button>');
  if (isDevState()) {
    btns.push('<button type="button" class="dashboard-filter-btn dev" data-action="add-dummy-camera">DUMMY+</button>');
  }
  el.innerHTML = btns.join("");
  Array.from(el.querySelectorAll(".dashboard-filter-btn")).forEach((btn) => {
    const active = btn.getAttribute("data-filter") === dashboardCameraFilter;
    btn.classList.toggle("active", active);
  });
}

async function addDummyCamera() {
  if (!isDevState()) return;
  const stamp = Date.now();
  const suffix = Math.floor(Math.random() * 1000)
    .toString()
    .padStart(3, "0");
  const name = `dummy-cam-${stamp.toString().slice(-6)}-${suffix}`;
  const webrtcPath = `dummy-${stamp}-${suffix}`;
  const rtspUrl = `rtsp://127.0.0.1:8554/${webrtcPath}`;
  await api("/cameras", {
    method: "POST",
    body: JSON.stringify({ name, rtspUrl, webrtcPath }),
  });
  dashboardCameraFilter = "__all__";
  await refresh({ updateLiveGrid: false, reloadRoi: false });
  setEventViewInfo(`개발용 더미 카메라 추가 완료: ${name}`, "ok");
}

function renderDashboardEventLog(events, camNameById) {
  const el = document.getElementById("dashEventLog");
  if (!el) return;
  const rows = events.slice(0, 30).map((e) => {
    const camName = camNameById.get(e.cameraId) || e.cameraId || "-";
    const sev = String(e.severity || "low").toUpperCase();
    const src = e.payload?.source || "unknown";
    return `${formatTsLocal(e.occurredAt)} | ${camName} | ${e.type} | ${sev} | ${src}`;
  });
  if (!rows.length) {
    el.innerHTML = '<div class="dashboard-log-item">표시할 이벤트가 없습니다.</div>';
    return;
  }
  el.innerHTML = rows.map((x) => `<div class="dashboard-log-item">${escapeHtml(x)}</div>`).join("");
}

async function captureDashboardSnapshot(cameraId) {
  if (!cameraId) return;
  const marker = dashboardSnapshotMeta[cameraId];
  if (marker && marker.busy) return;
  dashboardSnapshotMeta[cameraId] = { ...(marker || {}), busy: true };
  try {
    const r = await api(`/cameras/${cameraId}/snapshot`, { method: "POST" });
    dashboardSnapshotCache[cameraId] = {
      imageDataUrl: String(r.imageDataUrl || ""),
      capturedAt: String(r.capturedAt || ""),
      error: "",
    };
    dashboardSnapshotMeta[cameraId] = { lastMs: Date.now(), busy: false };
  } catch (err) {
    dashboardSnapshotCache[cameraId] = {
      imageDataUrl: "",
      capturedAt: "",
      error: String(err.message || "snapshot failed"),
    };
    dashboardSnapshotMeta[cameraId] = { lastMs: Date.now(), busy: false };
  }
}

async function runDashboardSnapshotCycle(cameras, forceMissing = false) {
  if (!(currentUserRole === "admin" || currentUserRole === "operator")) return;
  const now = Date.now();
  const dueMs = normalizeSnapshotIntervalHours(dashboardSnapshotIntervalHours) * 3600 * 1000;
  const tasks = [];
  cameras.forEach((c) => {
    const id = String(c.id || "");
    if (!id) return;
    const meta = dashboardSnapshotMeta[id] || {};
    const hasImage = !!dashboardSnapshotCache[id]?.imageDataUrl;
    const due = !meta.lastMs || now - Number(meta.lastMs) >= dueMs;
    if ((forceMissing && !hasImage) || due) {
      tasks.push(captureDashboardSnapshot(id));
    }
  });
  if (tasks.length) {
    await Promise.all(tasks);
  }
}

function restartDashboardSnapshotTimer() {
  if (dashboardSnapshotTimer) {
    clearInterval(dashboardSnapshotTimer);
    dashboardSnapshotTimer = null;
  }
  const ms = normalizeSnapshotIntervalHours(dashboardSnapshotIntervalHours) * 3600 * 1000;
  dashboardSnapshotTimer = setInterval(() => {
    runDashboardSnapshotCycle(dashboardLastCameras, false).then(() => {
      renderDashboardCards(dashboardLastCameras);
    }).catch(() => {});
  }, ms);
}

function renderDashboardCards(cameras) {
  const el = document.getElementById("dashCameraCards");
  if (!el) return;
  const filtered = cameras.filter((c) => {
    const id = String(c.id || "");
    if (dashboardCameraFilter !== "__all__" && dashboardCameraFilter !== id) return false;
    return true;
  });
  if (!filtered.length) {
    el.innerHTML = '<div class="camera-card"><div class="camera-card-shot">카메라 없음</div><div class="camera-card-meta">검색 조건에 맞는 카메라가 없습니다.</div></div>';
    return;
  }
  const canDelete = currentUserRole === "admin" || authEnabled === false;
  el.innerHTML = filtered
    .map((c) => {
      const id = String(c.id || "");
      const snap = dashboardSnapshotCache[id] || {};
      const ip = getCameraIpFromRtsp(String(c.rtspUrl || ""));
      const shotHtml = snap.imageDataUrl
        ? `<img alt="${escapeHtml(c.name || id)} snapshot" src="${escapeHtml(snap.imageDataUrl)}" />`
        : `<span>${escapeHtml(snap.error || "SnapShot")}</span>`;
      return `
        <article class="camera-card">
          <div class="camera-card-shot">${shotHtml}</div>
          <div>
            <div class="camera-card-top-actions">
              <button type="button" class="mini-btn ${canDelete ? "" : "is-hidden"}" data-admin-only data-camera-delete-card="${escapeHtml(id)}">-</button>
            </div>
            <div class="camera-card-meta">
              <div>Name: ${escapeHtml(c.name || "-")}</div>
              <div>IP: ${escapeHtml(ip)}</div>
              <div>Status: ${escapeHtml(c.status || "-")}</div>
              <div>LastShot: ${escapeHtml(snap.capturedAt ? formatTsLocal(snap.capturedAt) : "-")}</div>
            </div>
            <div class="camera-card-actions">
              <button type="button" class="mini-btn ${canDelete ? "" : "is-hidden"}" data-admin-only data-camera-setting="${escapeHtml(id)}">Setting</button>
            </div>
          </div>
        </article>
      `;
    })
    .join("");
  applyAdminOnlyVisibility(canDelete);
}

function renderDashboard(cameras, events, camNameById) {
  dashboardLastCameras = Array.isArray(cameras) ? cameras : [];
  dashboardLastEvents = Array.isArray(events) ? events : [];
  renderDashboardFilters(dashboardLastCameras);
  renderDashboardEventLog(dashboardLastEvents, camNameById);
  renderDashboardCards(dashboardLastCameras);
}

function renderEventBanner(events) {
  const badge = document.getElementById("eventBadge");
  const summary = document.getElementById("eventSummary");
  if (!badge || !summary) return;
  if (!events || events.length === 0) {
    badge.className = "event-badge event-none";
    badge.textContent = "최근 이벤트 없음";
    summary.textContent = "최근 이벤트 요약이 여기에 표시됩니다.";
    return;
  }
  const latest = events[0];
  const sev = String(latest.severity || "low").toLowerCase();
  badge.className = `event-badge event-${sev === "high" ? "high" : sev === "medium" ? "medium" : "low"}`;
  badge.textContent = `최근 ${events.length}건 | ${sev.toUpperCase()}`;
  const src = latest.payload?.source || "unknown";
  summary.textContent = `${formatTsLocal(latest.occurredAt)} | camera=${latest.cameraId} | type=${latest.type} | source=${src}`;
}

async function safeApi(path, fallback) {
  try {
    return await api(path);
  } catch (_) {
    return fallback;
  }
}

function setAuthInfo(lines, cls = "") {
  const el = document.getElementById("authInfo");
  el.innerHTML = "";
  lines.forEach((x) => row(el, x, cls));
}

function setPersonRuleInfo(lines, cls = "") {
  const el = document.getElementById("personRuleInfo");
  if (!el) return;
  el.innerHTML = "";
  lines.forEach((x) => row(el, x, cls));
}

function setAiModelPresetInfo(lines, cls = "") {
  const el = document.getElementById("aiModelPresetInfo");
  if (!el) return;
  el.innerHTML = "";
  lines.forEach((x) => row(el, x, cls));
}

function setCameraModelPresetInfo(lines, cls = "") {
  const el = document.getElementById("cameraModelPresetInfo");
  if (!el) return;
  el.innerHTML = "";
  lines.forEach((x) => row(el, x, cls));
}

function setEventViewInfo(text, cls = "") {
  const el = document.getElementById("eventViewInfo");
  if (!el) return;
  el.innerHTML = "";
  row(el, text, cls);
}

function setCameraRotateInfo(lines, cls = "") {
  const el = document.getElementById("cameraRotateInfo");
  if (!el) return;
  el.innerHTML = "";
  lines.forEach((x) => row(el, x, cls));
}

function toSensitivityValue(raw) {
  let conf = Number(raw);
  if (conf > 1 && conf <= 100) conf = conf / 100;
  conf = Math.max(0.05, Math.min(0.95, Number.isFinite(conf) ? conf : 0.35));
  return conf;
}

function setCameraSensitivityUi(raw) {
  const conf = toSensitivityValue(raw);
  const input = document.getElementById("cameraModelConf");
  const output = document.getElementById("cameraModelConfValue");
  if (input) input.value = String(conf);
  if (output) output.textContent = `${Math.round(conf * 100)}%`;
}

function bindCameraSensitivityControl() {
  const input = document.getElementById("cameraModelConf");
  if (!input || input.dataset.bound === "true") return;
  input.dataset.bound = "true";
  input.addEventListener("input", () => {
    setCameraSensitivityUi(input.value);
  });
  setCameraSensitivityUi(input.value || "0.35");
}

function loadCameraRotateMap() {
  if (window.__cameraRotateMapCache && typeof window.__cameraRotateMapCache === "object") {
    return window.__cameraRotateMapCache;
  }
  try {
    const raw = localStorage.getItem(CAMERA_ROTATE_KEY) || "{}";
    const parsed = JSON.parse(raw);
    const map = parsed && typeof parsed === "object" ? parsed : {};
    window.__cameraRotateMapCache = map;
    return map;
  } catch (_) {
    return {};
  }
}

function normalizeRotateDeg(v) {
  const n = Number(v);
  return n === 90 || n === 180 || n === 270 ? n : 0;
}

function getCameraRotateDeg(cameraId) {
  if (!cameraId) return 0;
  const map = loadCameraRotateMap();
  return normalizeRotateDeg(map[cameraId]);
}

function setCameraRotateDeg(cameraId, deg) {
  if (!cameraId) return;
  const map = loadCameraRotateMap();
  map[cameraId] = normalizeRotateDeg(deg);
  window.__cameraRotateMapCache = map;
  localStorage.setItem(CAMERA_ROTATE_KEY, JSON.stringify(map));
}

function rotateCamera90(cameraId) {
  const next = (getCameraRotateDeg(cameraId) + 90) % 360;
  setCameraRotateDeg(cameraId, next);
  return next;
}

async function saveCameraRotateDeg(cameraId, deg) {
  if (!cameraId) return 0;
  const next = normalizeRotateDeg(deg);
  const cfg =
    (await safeApi(`/cameras/${cameraId}/model-settings`, null)) || {
      enabled: false,
      modelPath: "",
      confidenceThreshold: 0.35,
      timeoutSec: 5,
      pollSec: 2,
      cooldownSec: 10,
      extra: {},
    };
  const extra = cfg.extra && typeof cfg.extra === "object" ? { ...cfg.extra } : {};
  extra.rotationDeg = next;
  await api(`/cameras/${cameraId}/model-settings`, {
    method: "PUT",
    body: JSON.stringify({
      enabled: !!cfg.enabled,
      modelPath: cfg.modelPath || "",
      confidenceThreshold: Number(cfg.confidenceThreshold ?? 0.35),
      timeoutSec: Number(cfg.timeoutSec ?? 5),
      pollSec: Number(cfg.pollSec ?? 2),
      cooldownSec: Number(cfg.cooldownSec ?? 10),
      extra,
    }),
  });
  setCameraRotateDeg(cameraId, next);
  return next;
}

function renderAiModelPresets(modelList, selectedPath = "") {
  const sel = document.getElementById("aiModelPreset");
  if (!sel) return;
  const items = Array.isArray(modelList?.items) ? modelList.items : [];
  const preferredPath = selectedPath || modelList?.selectedPath || document.getElementById("aiModelPath")?.value || "";
  const opts = ['<option value="">모델 경로 선택(자동 검색)</option>'];
  items.forEach((m) => {
    const p = String(m.path || "");
    if (!p) return;
    const src = m.source === "models" ? "models" : "project";
    const name = m.name || p.split(/[\\/]/).pop() || p;
    opts.push(`<option value="${escapeHtml(p)}">${escapeHtml(name)} (${escapeHtml(src)})</option>`);
  });
  sel.innerHTML = opts.join("");
  if (preferredPath) {
    sel.value = preferredPath;
  }
  setAiModelPresetInfo(
    [
      `검색된 모델 수=${items.length}`,
      `현재 선택=${preferredPath || "(없음)"}`,
    ],
    items.length > 0 ? "ok" : "warn"
  );
}

function renderCameraModelPresets(modelList, selectedPath = "") {
  const sel = document.getElementById("cameraModelPreset");
  if (!sel) return;
  const items = Array.isArray(modelList?.items) ? modelList.items : [];
  const preferredPath = selectedPath || document.getElementById("cameraModelPath")?.value || "";
  const opts = ['<option value="">모델 파일 선택 (자동 검색)</option>'];
  items.forEach((m) => {
    const p = String(m.path || "");
    if (!p) return;
    const src = m.source === "models" ? "models" : "project";
    const name = m.name || p.split(/[\\/]/).pop() || p;
    opts.push(`<option value="${escapeHtml(p)}">${escapeHtml(name)} (${escapeHtml(src)})</option>`);
  });
  sel.innerHTML = opts.join("");
  if (preferredPath) sel.value = preferredPath;
  setCameraModelPresetInfo(
    [
      `검색된 모델 수=${items.length}`,
      `현재 선택=${preferredPath || "(없음)"}`,
    ],
    items.length > 0 ? "ok" : "warn"
  );
}

function applyRoleGates() {
  const adminOnlyForms = ["aiModelForm", "destinationForm", "routeForm"];
  const hasRole = !!currentUserRole;
  const isAdmin = currentUserRole === "admin";
  const isOperator = currentUserRole === "operator";
  const allowOperatorWrite = isAdmin || isOperator;
  ["cameraForm", "cameraRotateForm", "roiForm", "policyForm", "eventForm", "liveConfigForm", "personRuleForm", "eventPackForm", "cameraModelForm"].forEach((id) => {
    const form = document.getElementById(id);
    if (!form) return;
    form.querySelectorAll("input,select,textarea,button").forEach((x) => {
      x.disabled = !allowOperatorWrite;
    });
  });
  adminOnlyForms.forEach((id) => {
    const form = document.getElementById(id);
    if (!form) return;
    form.querySelectorAll("input,select,textarea,button").forEach((x) => {
      x.disabled = !isAdmin;
    });
  });
  const discoverForm = document.getElementById("discoverForm");
  if (discoverForm) {
    discoverForm.querySelectorAll("input,button").forEach((x) => {
      x.disabled = !isAdmin;
    });
  }
  const webrtcForm = document.getElementById("webrtcToggleForm");
  if (webrtcForm) {
    webrtcForm.querySelectorAll("input,select,button").forEach((x) => {
      x.disabled = !isAdmin;
    });
  }
  const clearBtn = document.getElementById("clearEventViewBtn");
  if (clearBtn) clearBtn.disabled = !isAdmin;
  const dashClearBtn = document.getElementById("dashClearEventLogBtn");
  if (dashClearBtn) dashClearBtn.disabled = !isAdmin;
  if (!hasRole) {
    setAuthInfo(["인증되지 않음"], "warn");
  } else {
    const mode = authEnabled ? "인증 사용" : "인증 미사용(개발모드)";
    setAuthInfo([`로그인 역할: ${currentUserRole}`, mode], "ok");
  }
  applyAdminOnlyVisibility(isAdmin);
}

async function loadMe() {
  try {
    const me = await api("/auth/me");
    currentUserRole = me.role || null;
    authEnabled = !!me.authEnabled;
  } catch (_) {
    currentUserRole = null;
    authEnabled = null;
  }
  applyRoleGates();
}

function clamp01(v) {
  if (Number.isNaN(v)) return 0;
  return Math.max(0, Math.min(1, v));
}

function sanitizeZone(z, idx) {
  const name = typeof z?.name === "string" && z.name.trim() ? z.name.trim() : `zone-${idx + 1}`;
  const shape = String(z?.shape || "rect").toLowerCase() === "polygon" ? "polygon" : "rect";
  if (shape === "polygon") {
    const rawPoints = Array.isArray(z?.points) ? z.points : [];
    const points = rawPoints
      .map((p) => ({ x: clamp01(Number(p?.x ?? 0)), y: clamp01(Number(p?.y ?? 0)) }))
      .filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
    return { name, shape, points: points.slice(0, 24) };
  }
  return {
    name,
    shape: "rect",
    x: clamp01(Number(z?.x ?? 0)),
    y: clamp01(Number(z?.y ?? 0)),
    w: clamp01(Number(z?.w ?? 0.1)),
    h: clamp01(Number(z?.h ?? 0.1)),
  };
}

function syncRoiTextarea() {
  document.getElementById("roiZones").value = JSON.stringify(roiZonesState, null, 2);
}

function updateRoiSelectedLabel() {
  const label = document.getElementById("roiSelectedLabel");
  if (roiSelectedIndex < 0 || !roiZonesState[roiSelectedIndex]) {
    label.textContent = "선택: 없음";
    return;
  }
  const z = roiZonesState[roiSelectedIndex];
  if (z.shape === "polygon") {
    label.textContent = `선택: ${z.name} polygon(${(z.points || []).length}점)`;
    return;
  }
  label.textContent = `선택: ${z.name} rect(${z.x.toFixed(2)}, ${z.y.toFixed(2)}, ${z.w.toFixed(2)}, ${z.h.toFixed(2)})`;
}

function canvasPoint(e) {
  const canvas = document.getElementById("roiCanvas");
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  const px = (e.clientX - rect.left) * scaleX;
  const py = (e.clientY - rect.top) * scaleY;
  return { x: px / canvas.width, y: py / canvas.height };
}

function zoneContains(z, p) {
  if (z.shape === "polygon") {
    const points = Array.isArray(z.points) ? z.points : [];
    if (points.length < 3) return false;
    let inside = false;
    let j = points.length - 1;
    for (let i = 0; i < points.length; i += 1) {
      const xi = Number(points[i].x || 0);
      const yi = Number(points[i].y || 0);
      const xj = Number(points[j].x || 0);
      const yj = Number(points[j].y || 0);
      const hit = (yi > p.y) !== (yj > p.y) && p.x < ((xj - xi) * (p.y - yi)) / ((yj - yi) || 1e-9) + xi;
      if (hit) inside = !inside;
      j = i;
    }
    return inside;
  }
  return p.x >= z.x && p.x <= z.x + z.w && p.y >= z.y && p.y <= z.y + z.h;
}

function drawRoiCanvas() {
  const canvas = document.getElementById("roiCanvas");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (roiSnapshotDataUrl) {
    const img = new Image();
    img.onload = () => {
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      drawRoiZonesLayer(ctx, canvas);
    };
    img.src = roiSnapshotDataUrl;
    return;
  }
  ctx.fillStyle = "rgba(8, 22, 36, 0.7)";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  drawRoiZonesLayer(ctx, canvas);
}

function drawRoiZonesLayer(ctx, canvas) {
  roiZonesState.forEach((z, idx) => {
    const selected = idx === roiSelectedIndex;
    ctx.strokeStyle = selected ? "#4de8b2" : "#67b7ff";
    ctx.lineWidth = selected ? 3 : 2;
    ctx.fillStyle = selected ? "rgba(77, 232, 178, 0.18)" : "rgba(103, 183, 255, 0.14)";
    if (z.shape === "polygon") {
      const points = Array.isArray(z.points) ? z.points : [];
      if (points.length >= 2) {
        ctx.beginPath();
        ctx.moveTo(Number(points[0].x || 0) * canvas.width, Number(points[0].y || 0) * canvas.height);
        for (let i = 1; i < points.length; i += 1) {
          ctx.lineTo(Number(points[i].x || 0) * canvas.width, Number(points[i].y || 0) * canvas.height);
        }
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      }
    } else {
      const x = z.x * canvas.width;
      const y = z.y * canvas.height;
      const w = z.w * canvas.width;
      const h = z.h * canvas.height;
      ctx.strokeRect(x, y, w, h);
      ctx.fillRect(x, y, w, h);
    }
    ctx.fillStyle = "#d9edff";
    ctx.font = "12px Consolas";
    if (z.shape === "polygon" && Array.isArray(z.points) && z.points.length > 0) {
      const px = Number(z.points[0].x || 0) * canvas.width;
      const py = Number(z.points[0].y || 0) * canvas.height;
      ctx.fillText(z.name, px + 4, py + 14);
    } else {
      const x = z.x * canvas.width;
      const y = z.y * canvas.height;
      ctx.fillText(z.name, x + 4, y + 14);
    }
  });
  if (roiDrawRect) {
    const x = roiDrawRect.x * canvas.width;
    const y = roiDrawRect.y * canvas.height;
    const w = roiDrawRect.w * canvas.width;
    const h = roiDrawRect.h * canvas.height;
    ctx.strokeStyle = "#ffa75e";
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.strokeRect(x, y, w, h);
    ctx.setLineDash([]);
  }
  if (roiPolygonDraft.length > 0) {
    ctx.strokeStyle = "#ffa75e";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(roiPolygonDraft[0].x * canvas.width, roiPolygonDraft[0].y * canvas.height);
    for (let i = 1; i < roiPolygonDraft.length; i += 1) {
      ctx.lineTo(roiPolygonDraft[i].x * canvas.width, roiPolygonDraft[i].y * canvas.height);
    }
    ctx.stroke();
    roiPolygonDraft.forEach((p) => {
      ctx.fillStyle = "#ffa75e";
      ctx.beginPath();
      ctx.arc(p.x * canvas.width, p.y * canvas.height, 3, 0, Math.PI * 2);
      ctx.fill();
    });
  }
}

function setAiDebugInfo(lines, cls = "") {
  const el = document.getElementById("aiDebugInfo");
  if (!el) return;
  el.innerHTML = "";
  lines.forEach((x) => row(el, x, cls));
}

function stopAiDebugLoop() {
  if (aiDebugTimer) {
    clearInterval(aiDebugTimer);
    aiDebugTimer = null;
  }
}

function drawAiDebugPreview(data) {
  const canvas = document.getElementById("aiDebugCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const img = new Image();
  img.onload = () => {
    canvas.width = img.width;
    canvas.height = img.height;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

    const roi = data.roi || {};
    const zones = roi.zones || [];
    if (roi.enabled && zones.length > 0) {
      ctx.strokeStyle = "#4de8b2";
      ctx.lineWidth = 2;
      zones.forEach((z) => {
        if (String(z.shape || "rect") === "polygon" && Array.isArray(z.points) && z.points.length >= 2) {
          ctx.beginPath();
          ctx.moveTo(Number(z.points[0].x || 0) * canvas.width, Number(z.points[0].y || 0) * canvas.height);
          for (let i = 1; i < z.points.length; i += 1) {
            ctx.lineTo(Number(z.points[i].x || 0) * canvas.width, Number(z.points[i].y || 0) * canvas.height);
          }
          ctx.closePath();
          ctx.stroke();
        } else {
          const x = Number(z.x || 0) * canvas.width;
          const y = Number(z.y || 0) * canvas.height;
          const w = Number(z.w || 0) * canvas.width;
          const h = Number(z.h || 0) * canvas.height;
          ctx.strokeRect(x, y, w, h);
        }
      });
    }

    const detections = data.detections || [];
    detections.forEach((d, idx) => {
      const x = Number(d.nx || 0) * canvas.width;
      const y = Number(d.ny || 0) * canvas.height;
      const w = Number(d.nw || 0) * canvas.width;
      const h = Number(d.nh || 0) * canvas.height;
      const conf = Number(d.confidence || 0);
      const label = `${d.label || "person"} ${(conf * 100).toFixed(1)}%`;
      ctx.strokeStyle = "#ffa75e";
      ctx.lineWidth = 2;
      ctx.strokeRect(x, y, w, h);
      ctx.fillStyle = "rgba(255, 167, 94, 0.9)";
      ctx.fillRect(x, Math.max(0, y - 18), Math.max(80, label.length * 7), 16);
      ctx.fillStyle = "#081018";
      ctx.font = "12px Consolas";
      ctx.fillText(label, x + 4, Math.max(12, y - 6));
      if (idx > 30) return;
    });
  };
  img.src = data.imageDataUrl;
}

async function fetchAiDebugPreview() {
  const sel = document.getElementById("aiDebugCameraId");
  if (!sel || !sel.value) {
    setAiDebugInfo(["카메라를 선택하세요."], "warn");
    return;
  }
  if (aiDebugBusy) return;
  aiDebugBusy = true;
  try {
    const cameraId = sel.value;
    const res = await api(`/dev/ai/preview?cameraId=${encodeURIComponent(cameraId)}`);
    drawAiDebugPreview(res);
    setAiDebugInfo([
      `camera=${res.cameraName} (${String(res.cameraId).slice(0, 8)})`,
      `status=${res.status} | detected=${res.count}`,
      `capturedAt=${res.capturedAt}`,
      `model=${res.modelPath}`,
    ]);
  } catch (err) {
    setAiDebugInfo([`디버그 추론 실패: ${err.message}`], "warn");
  } finally {
    aiDebugBusy = false;
  }
}

function startAiDebugLoop() {
  const intervalEl = document.getElementById("aiDebugIntervalMs");
  const intervalMs = Math.max(Number(intervalEl?.value || 700), 150);
  stopAiDebugLoop();
  fetchAiDebugPreview();
  aiDebugTimer = setInterval(() => {
    fetchAiDebugPreview();
  }, intervalMs);
}

function renderAiDebugLive(cameraId) {
  const frame = document.getElementById("aiDebugLiveFrame");
  const statusEl = document.getElementById("aiDebugLiveStatus");
  if (!frame) return;

  const cam = camerasState.find((c) => String(c.id) === String(cameraId || ""));
  if (!cam) {
    frame.removeAttribute("src");
    if (statusEl) statusEl.textContent = "카메라 선택 필요";
    return;
  }
  if (!webrtcEnabled) {
    frame.removeAttribute("src");
    if (statusEl) statusEl.textContent = "WebRTC 비활성";
    return;
  }
  const path = String(cam.webrtcPath || "").replace(/^\/+/, "");
  if (!path) {
    frame.removeAttribute("src");
    if (statusEl) statusEl.textContent = "webrtcPath 없음";
    return;
  }
  const src = `${liveBaseUrl.replace(/\/+$/, "")}/${path}`;
  if (frame.getAttribute("src") !== src) {
    frame.setAttribute("src", src);
  }
  frame.classList.remove("rot-90", "rot-180", "rot-270");
  const rotateDeg = getCameraRotateDeg(String(cam.id));
  if (rotateDeg === 90) frame.classList.add("rot-90");
  else if (rotateDeg === 180) frame.classList.add("rot-180");
  else if (rotateDeg === 270) frame.classList.add("rot-270");
  if (statusEl) statusEl.textContent = `${cam.status || "-"} | ${cam.name || "-"}`;
}

function setRoiZonesFromInput(zones) {
  roiZonesState = (zones || []).map((z, idx) => sanitizeZone(z, idx));
  roiPolygonDraft = [];
  roiSelectedIndex = roiZonesState.length > 0 ? 0 : -1;
  syncRoiTextarea();
  updateRoiSelectedLabel();
  drawRoiCanvas();
}

function tryParseRoiTextarea() {
  const raw = document.getElementById("roiZones").value.trim();
  if (!raw) {
    setRoiZonesFromInput([]);
    return true;
  }
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) throw new Error("zones must be an array");
    setRoiZonesFromInput(parsed);
    return true;
  } catch (err) {
    fill(document.getElementById("roiInfo"), [`ROI JSON 파싱 오류: ${err.message}`]);
    return false;
  }
}

async function loadRoi(cameraId) {
  if (!cameraId) {
    document.getElementById("roiInfo").innerHTML = "";
    setRoiZonesFromInput([]);
    return;
  }
  const roi = await api(`/cameras/${cameraId}/roi`);
  document.getElementById("roiEnabled").value = String(!!roi.enabled);
  setRoiZonesFromInput(roi.zones || []);
  const polyCount = (roi.zones || []).filter((z) => String(z.shape || "rect") === "polygon").length;
  fill(document.getElementById("roiInfo"), [`cameraId=${cameraId}`, `사용=${roi.enabled}`, `영역수=${(roi.zones || []).length}`, `polygon=${polyCount}`]);
}

async function loadCameraModelSettings(cameraId) {
  if (!cameraId) return;
  const cfg = await safeApi(`/cameras/${cameraId}/model-settings`, null);
  if (!cfg) return;
  document.getElementById("cameraModelEnabled").value = String(!!cfg.enabled);
  document.getElementById("cameraModelPath").value = cfg.modelPath || "";
  setCameraSensitivityUi(cfg.confidenceThreshold ?? 0.35);
  document.getElementById("cameraModelTimeoutSec").value = String(cfg.timeoutSec ?? 5);
  document.getElementById("cameraModelPollSec").value = String(cfg.pollSec ?? 2);
  document.getElementById("cameraModelCooldownSec").value = String(cfg.cooldownSec ?? 10);
  fill(document.getElementById("cameraModelInfo"), [`camera=${cameraId}`, `enabled=${!!cfg.enabled}`, `model=${cfg.modelPath || "(none)"}`]);
}

async function loadCameraEventPack(cameraId) {
  if (!cameraId) return;
  const cfg = await safeApi(`/cameras/${cameraId}/event-pack`, null);
  if (!cfg) return;
  document.getElementById("eventPackEnabled").value = String(!!cfg.enabled);
  document.getElementById("eventPackId").value = cfg.packId || "";
  document.getElementById("eventPackVersion").value = cfg.packVersion || "";
  document.getElementById("eventPackParams").value = JSON.stringify(cfg.params || {}, null, 2);
  fill(document.getElementById("eventPackInfo"), [`camera=${cameraId}`, `enabled=${!!cfg.enabled}`, `pack=${cfg.packId || "-"}@${cfg.packVersion || "-"}`]);
}

function renderLiveGrid(cameras) {
  const grid = document.getElementById("liveGrid");
  if (!webrtcEnabled) {
    grid.innerHTML = '<div class="item warn">WebRTC 비활성화 상태입니다.</div>';
    return;
  }
  const existingTiles = new Map();
  Array.from(grid.querySelectorAll(".live-tile")).forEach((tile) => {
    const id = tile.dataset.cameraId;
    if (id) existingTiles.set(id, tile);
  });

  const nextIds = new Set();
  cameras.slice(0, 8).forEach((c, idx) => {
    const camId = String(c.id || `${idx}`);
    nextIds.add(camId);
    const path = (c.webrtcPath || "").replace(/^\/+/, "");
    const rotateDeg = getCameraRotateDeg(camId);

    let tile = existingTiles.get(camId);
    if (!tile) {
      tile = document.createElement("div");
      tile.className = "live-tile";
      tile.dataset.cameraId = camId;
      tile.innerHTML =
        `<div class="live-head"><span></span><span></span></div>` +
        `<iframe class="live-frame" allow="camera; microphone; autoplay; fullscreen"></iframe>`;
    }

    const head = tile.querySelector(".live-head");
    const nameEl = head?.children?.[0];
    const statusEl = head?.children?.[1];
    if (nameEl) nameEl.textContent = `${idx + 1}. ${c.name}`;

    const frame = tile.querySelector("iframe.live-frame");
    if (!path) {
      if (frame) frame.removeAttribute("src");
      if (statusEl) statusEl.textContent = "webrtcPath 없음";
    } else {
      const src = `${liveBaseUrl.replace(/\/+$/, "")}/${path}`;
      if (frame && frame.getAttribute("src") !== src) {
        frame.setAttribute("src", src);
      }
      if (statusEl) statusEl.textContent = c.status || "-";
    }
    if (frame) {
      frame.classList.remove("rot-90", "rot-180", "rot-270");
      if (rotateDeg === 90) frame.classList.add("rot-90");
      else if (rotateDeg === 180) frame.classList.add("rot-180");
      else if (rotateDeg === 270) frame.classList.add("rot-270");
    }

    // Append in order; existing nodes are moved without recreation.
    grid.appendChild(tile);
  });

  Array.from(grid.querySelectorAll(".live-tile")).forEach((tile) => {
    const id = tile.dataset.cameraId || "";
    if (!nextIds.has(id)) {
      tile.remove();
    }
  });
}

async function refresh(options = {}) {
  const { updateLiveGrid = true, reloadRoi = true } = options;
  const tk = localStorage.getItem("accessToken") || "";
  if (tk && isTokenExpired(tk)) {
    localStorage.removeItem("accessToken");
    setAuthInfo(["토큰이 만료되어 자동 로그아웃되었습니다."], "warn");
  }
  await loadMe();
  const [cameras, policies, aiModel, personRule, modelList, destinations, routes, events, artifacts, monitorRows, monitorOverview, webRtcSettings, eventPacks] = await Promise.all([
    api("/cameras"),
    api("/event-policies"),
    api("/settings/ai-model"),
    safeApi("/settings/person-event", { enabled: true, dwellSec: 5, cooldownSec: 10, eventType: "person_detected", severity: "high" }),
    safeApi("/models/list", { selectedPath: "", items: [] }),
    api("/destinations"),
    api("/routing-rules"),
    api("/events"),
    api("/artifacts"),
    safeApi("/monitor/cameras", []),
    safeApi("/monitor/overview", null),
    safeApi("/settings/webrtc", { enabled: true }),
    safeApi("/event-packs", []),
  ]);
  webrtcEnabled = !!webRtcSettings?.enabled;
  const cameraRows = dedupeCamerasById(cameras);
  camerasState = cameraRows;
  eventPacksState = Array.isArray(eventPacks) ? eventPacks : [];
  const rotateMap = loadCameraRotateMap();
  cameraRows.forEach((c) => {
    rotateMap[c.id] = normalizeRotateDeg(c.rotationDeg);
  });
  window.__cameraRotateMapCache = rotateMap;
  localStorage.setItem(CAMERA_ROTATE_KEY, JSON.stringify(rotateMap));
  const camNameById = new Map(cameraRows.map((c) => [c.id, c.name]));
  const destNameById = new Map(destinations.map((d) => [d.id, d.name]));

  fillHtml(
    document.getElementById("cameraList"),
    cameraRows.map(
      (c) =>
        `[${c.status}] ${c.name} | 경로:${c.webrtcPath} | 회전:${getCameraRotateDeg(c.id)}도 | ID:${c.id} <button class="mini-btn" data-camera-delete="${c.id}">삭제</button>`
    )
  );
  fill(
    document.getElementById("policyList"),
    policies.map((p) => {
      const cam = camNameById.get(p.cameraId) || p.cameraId;
      return p.mode === "clip"
        ? `${cam} | ${p.eventType} -> 클립(전 ${p.clip.preSec}s, 후 ${p.clip.postSec}s)`
        : `${cam} | ${p.eventType} -> 스냅샷(개수:${p.snapshot.snapshotCount})`;
    })
  );
  fill(
    document.getElementById("destinationList"),
    destinations.map((d) => {
      const cfg = d && typeof d.config === "object" ? d.config : {};
      const terminal = cfg.terminalId ? ` | terminalId=${cfg.terminalId}` : "";
      const mapFlag = cfg.cctvIdByCameraId && typeof cfg.cctvIdByCameraId === "object" ? " | cameraMap=Y" : "";
      return `${d.name} | ${d.type} | apiMode=${cfg.apiMode || "-"}${terminal}${mapFlag} | 사용=${d.enabled}`;
    })
  );
  fill(
    document.getElementById("routeList"),
    routes.map((r) => {
      const cam = camNameById.get(r.cameraId) || r.cameraId;
      const dest = destNameById.get(r.destinationId) || r.destinationId;
      return `${cam} | 이벤트:* | snapshot -> ${dest}`;
    })
  );
  const visibleEvents = events.filter((e) => {
    if (!eventViewClearBeforeMs) return true;
    const t = Date.parse(String(e.occurredAt || ""));
    return Number.isFinite(t) && t >= eventViewClearBeforeMs;
  });
  renderDashboard(cameraRows, visibleEvents, camNameById);
  renderEventBanner(visibleEvents);
  fillHtml(
    document.getElementById("eventList"),
    visibleEvents.slice(0, 20).map((e) => {
      const sev = String(e.severity || "low").toLowerCase();
      const chip = `<span class="sev-chip ${severityClass(sev)}">${escapeHtml(sev.toUpperCase())}</span>`;
      const src = escapeHtml(e.payload?.source || "unknown");
      const t = escapeHtml(e.type || "-");
      const camName = escapeHtml(camNameById.get(e.cameraId) || (e.cameraId || "").slice(0, 8));
      return `${chip}${escapeHtml(formatTsLocal(e.occurredAt))} | ${t} | cam=${camName} | source=${src}`;
    })
  );
  const visibleArtifacts = artifacts.filter((a) => {
    if (!eventViewClearBeforeMs) return true;
    const t = Date.parse(String(a.createdAt || ""));
    return Number.isFinite(t) && t >= eventViewClearBeforeMs;
  });
  fill(
    document.getElementById("artifactList"),
    visibleArtifacts.slice(0, 20).map((a) => {
      const cam = camNameById.get(a.cameraId) || a.cameraId;
      return `${a.kind} | ${cam} | 해시=${a.checksumSha256.slice(0, 10)}...`;
    })
  );
  fillHtml(document.getElementById("monitorSummaryList"), renderMonitorSummaryHtml(monitorOverview));
  fillHtml(
    document.getElementById("monitorList"),
    renderMonitorLinkHtml(monitorOverview, monitorRows)
  );

  const cameraOptions = optionHtml(cameraRows);
  const policyCameraSel = document.getElementById("policyCameraId");
  const routeCameraSel = document.getElementById("routeCameraId");
  const eventCameraSel = document.getElementById("eventCameraId");
  if (shouldSyncForm("policyForm")) setSelectOptionsKeepingValue(policyCameraSel, cameraOptions);
  if (shouldSyncForm("routeForm")) setSelectOptionsKeepingValue(routeCameraSel, cameraOptions);
  if (shouldSyncForm("eventForm")) setSelectOptionsKeepingValue(eventCameraSel, cameraOptions);
  const eventPackCamSel = document.getElementById("eventPackCameraId");
  if (eventPackCamSel && shouldSyncForm("eventPackForm")) setSelectOptionsKeepingValue(eventPackCamSel, cameraOptions);
  const camModelSel = document.getElementById("cameraModelCameraId");
  if (camModelSel && shouldSyncForm("cameraModelForm")) setSelectOptionsKeepingValue(camModelSel, cameraOptions);
  const camRotateSel = document.getElementById("cameraRotateCameraId");
  if (camRotateSel) {
    setSelectOptionsKeepingValue(camRotateSel, cameraOptions);
    if (!camRotateSel.value && cameraRows.length > 0) camRotateSel.value = cameraRows[0].id;
    if (camRotateSel.value) {
      setCameraRotateInfo([`카메라 선택: ${camRotateSel.value.slice(0, 8)} | 회전=${getCameraRotateDeg(camRotateSel.value)}도`]);
    } else {
      setCameraRotateInfo(["카메라 목록이 없습니다. 먼저 카메라를 등록/자동연결 해주세요."], "warn");
    }
  }
  const aiDebugSel = document.getElementById("aiDebugCameraId");
  const prevAiDebugCam = aiDebugSel ? aiDebugSel.value : "";
  if (aiDebugSel) {
    setSelectOptionsKeepingValue(aiDebugSel, cameraOptions, prevAiDebugCam);
    if (prevAiDebugCam && cameraRows.some((c) => c.id === prevAiDebugCam)) {
      aiDebugSel.value = prevAiDebugCam;
    }
    renderAiDebugLive(aiDebugSel.value);
  }
  const roiSel = document.getElementById("roiCameraId");
  if (shouldSyncForm("roiForm")) {
    setSelectOptionsKeepingValue(roiSel, cameraOptions, roiCameraSelection);
    if (roiCameraSelection && cameraRows.some((c) => c.id === roiCameraSelection)) {
      roiSel.value = roiCameraSelection;
    } else if (cameraRows.length > 0) {
      roiCameraSelection = cameraRows[0].id;
      roiSel.value = roiCameraSelection;
    }
  }
  const routeDestinationSel = document.getElementById("routeDestinationId");
  if (shouldSyncForm("routeForm")) setSelectOptionsKeepingValue(routeDestinationSel, optionHtml(destinations));
  if (shouldSyncForm("liveConfigForm")) document.getElementById("webrtcBaseUrl").value = liveBaseUrl;
  const webrtcSel = document.getElementById("webrtcEnabled");
  if (webrtcSel && shouldSyncForm("webrtcToggleForm")) webrtcSel.value = String(webrtcEnabled);
  const eventPackIdSel = document.getElementById("eventPackId");
  if (eventPackIdSel && shouldSyncForm("eventPackForm")) {
    const prevPackId = eventPackIdSel.value;
    eventPackIdSel.innerHTML = eventPacksState
      .map((p) => `<option value="${escapeHtml(p.packId)}">${escapeHtml(p.packId)} (${escapeHtml(p.version)})</option>`)
      .join("");
    if (prevPackId && selectHasValue(eventPackIdSel, prevPackId)) eventPackIdSel.value = prevPackId;
  }
  if (updateLiveGrid) {
    renderLiveGrid(cameraRows);
  }
  if (reloadRoi && shouldSyncForm("roiForm")) {
    await loadRoi(roiSel.value);
  }
  if (eventPackCamSel && shouldSyncForm("eventPackForm")) {
    if (!eventPackCamSel.value && cameraRows.length > 0) eventPackCamSel.value = cameraRows[0].id;
    await loadCameraEventPack(eventPackCamSel.value);
  }
  if (camModelSel && shouldSyncForm("cameraModelForm")) {
    if (!camModelSel.value && cameraRows.length > 0) camModelSel.value = cameraRows[0].id;
    await loadCameraModelSettings(camModelSel.value);
  }

  if (shouldSyncForm("aiModelForm")) {
    document.getElementById("aiEnabled").value = String(aiModel.enabled);
    document.getElementById("aiModelPath").value = aiModel.modelPath || "";
    document.getElementById("aiTimeoutSec").value = aiModel.timeoutSec || 5;
    document.getElementById("aiPollSec").value = aiModel.pollSec || 2;
    document.getElementById("aiCooldownSec").value = aiModel.cooldownSec || 10;
  }
  renderAiModelPresets(modelList, aiModel.modelPath || "");
  renderCameraModelPresets(modelList, document.getElementById("cameraModelPath")?.value || "");
  fill(document.getElementById("aiModelInfo"), [
    `사용=${aiModel.enabled}`,
    `모델경로=${aiModel.modelPath || "(비어있음)"}`,
    `타임아웃초=${aiModel.timeoutSec}`,
    `추론주기초=${aiModel.pollSec}`,
    `쿨다운초=${aiModel.cooldownSec}`,
  ]);
  const personEnabledEl = document.getElementById("personRuleEnabled");
  const personDwellEl = document.getElementById("personRuleDwellSec");
  const personCooldownEl = document.getElementById("personRuleCooldownSec");
  const personEventTypeEl = document.getElementById("personRuleEventType");
  const personSeverityEl = document.getElementById("personRuleSeverity");
  if (shouldSyncForm("personRuleForm")) {
    if (personEnabledEl) personEnabledEl.value = String(!!personRule.enabled);
    if (personDwellEl) personDwellEl.value = String(personRule.dwellSec ?? 5);
    if (personCooldownEl) personCooldownEl.value = String(personRule.cooldownSec ?? 10);
    if (personEventTypeEl) personEventTypeEl.value = personRule.eventType || "person_detected";
    if (personSeverityEl) personSeverityEl.value = personRule.severity || "high";
  }
  setPersonRuleInfo([
    `사용=${!!personRule.enabled}`,
    `지속감지초=${personRule.dwellSec ?? 5}`,
    `쿨타임초=${personRule.cooldownSec ?? 10}`,
    `eventType=${personRule.eventType || "person_detected"}`,
    `severity=${personRule.severity || "high"}`,
  ]);
  if (eventViewClearBeforeMs) {
    setEventViewInfo(`화면 클리어 기준: ${formatTsLocal(eventViewClearBeforeMs)} 이후 이벤트만 표시`, "warn");
  } else {
    setEventViewInfo("전체 이벤트 표시 중");
  }
  if (discoverResults.length > 0) {
    renderDiscoverResults(discoverResults);
  }
  await runDashboardSnapshotCycle(cameraRows, true);
  renderDashboardCards(cameraRows);
}

ensureEdgeControls();
mountDiscoverIntoModal();
hideGlobalModelUi();

document.getElementById("cameraForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  await api("/cameras", { method: "POST", body: JSON.stringify({ name: f.get("name"), rtspUrl: f.get("rtspUrl"), webrtcPath: f.get("webrtcPath") }) });
  e.target.reset();
  await refresh();
});

document.getElementById("cameraList").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-camera-delete]");
  if (!btn) return;
  const cameraId = btn.getAttribute("data-camera-delete");
  if (!cameraId) return;
  if (!confirm("해당 카메라를 삭제하시겠습니까?")) return;
  try {
    await api(`/cameras/${cameraId}`, { method: "DELETE" });
    await refresh();
  } catch (err) {
    row(document.getElementById("cameraList"), `삭제 실패: ${err.message}`, "warn");
  }
});

function renderDiscoverResults(cameras) {
  const el = document.getElementById("discoverList");
  el.innerHTML = "";
  cameras.forEach((c, idx) => {
    const div = document.createElement("div");
    div.className = "item";
    const onvifTag = c.onvif ? " | ONVIF" : "";
    div.innerHTML = `${idx + 1}. ${c.ip}${onvifTag} | ${c.rtspUrl} <button class="mini-btn" data-idx="${idx}">등록</button>`;
    el.appendChild(div);
  });
}

function applyDiscoverAuthToRtsp(rtspUrl) {
  const raw = String(rtspUrl || "").trim();
  if (!raw || !raw.toLowerCase().startsWith("rtsp://")) return raw;
  if (raw.includes("@")) return raw;
  const username = (document.getElementById("discoverUsername")?.value || "").trim();
  const password = document.getElementById("discoverPassword")?.value || "";
  if (!username) return raw;
  try {
    const u = new URL(raw);
    if (u.protocol !== "rtsp:") return raw;
    const auth = password ? `${encodeURIComponent(username)}:${encodeURIComponent(password)}@` : `${encodeURIComponent(username)}@`;
    return `rtsp://${auth}${u.host}${u.pathname || "/"}${u.search || ""}`;
  } catch (_) {
    return raw;
  }
}

async function registerDiscoveredCamera(item) {
  if (!item?.rtspUrl) throw new Error("rtspUrl not found");
  const camName = `cam-${item.ip.replaceAll(".", "-")}`;
  const webrtcPath = camName;
  const rtspUrl = applyDiscoverAuthToRtsp(item.rtspUrl);
  await api("/cameras", {
    method: "POST",
    body: JSON.stringify({
      name: camName,
      rtspUrl,
      webrtcPath,
    }),
  });
  return camName;
}

async function registerAllDiscovered() {
  if (!discoverResults.length) {
    fill(document.getElementById("discoverInfo"), ["등록할 카메라가 없습니다. 먼저 검색을 실행하세요."]);
    return;
  }
  let ok = 0;
  let skipped = 0;
  for (const item of discoverResults) {
    try {
      await registerDiscoveredCamera(item);
      ok += 1;
    } catch (_) {
      skipped += 1;
    }
  }
  fill(document.getElementById("discoverInfo"), [
    `전체 등록 완료: 성공=${ok}, 중복/실패=${skipped}, 대상=${discoverResults.length}`,
  ]);
  await refresh();
}

function stopDiscoverJobPoll() {
  if (discoverJobTimer) {
    clearInterval(discoverJobTimer);
    discoverJobTimer = null;
  }
}

async function pollDiscoverJob(jobId) {
  stopDiscoverJobPoll();
  const tick = async () => {
    try {
      const j = await api(`/cameras/discover/jobs/${encodeURIComponent(jobId)}`);
      const p = j.progress || {};
      fill(document.getElementById("discoverInfo"), [
        `job=${jobId.slice(0, 8)}`,
        `상태=${j.status} (${j.message || "-"})`,
        `진행=${p.scannedHosts || 0}/${p.totalHosts || 0}`,
        `발견=${p.foundCount || 0}`,
      ]);
      if (j.status === "done" && j.result) {
        discoverResults = j.result.cameras || [];
        fill(document.getElementById("discoverInfo"), [
          `CIDR=${j.result.cidr}`,
          `실제스캔대역=${(j.result.effectiveCidrs || []).join(", ") || "-"}`,
          `ONVIF발견=${j.result.onvifFound || 0}`,
          `스캔호스트수=${j.result.scannedHosts}`,
          `발견카메라수=${j.result.foundCount}`,
        ]);
        renderDiscoverResults(discoverResults);
        stopDiscoverJobPoll();
      } else if (j.status === "error") {
        fill(document.getElementById("discoverInfo"), [`검색 실패: ${j.error || "unknown error"}`]);
        stopDiscoverJobPoll();
      }
    } catch (err) {
      fill(document.getElementById("discoverInfo"), [`검색 상태 확인 실패: ${err.message}`]);
      stopDiscoverJobPoll();
    }
  };
  await tick();
  discoverJobTimer = setInterval(() => {
    tick();
  }, 1200);
}

async function runDiscover() {
  const cidr = document.getElementById("discoverCidr").value.trim();
  const portsRaw = document.getElementById("discoverPorts").value.trim();
  const username = document.getElementById("discoverUsername").value.trim();
  const password = document.getElementById("discoverPassword").value;
  const maxHosts = Number(document.getElementById("discoverMaxHosts").value || 256);
  const timeoutMs = Number(document.getElementById("discoverTimeoutMs").value || 700);
  const useOnvif = document.getElementById("discoverUseOnvif").value === "true";
  const onvifTimeoutMs = Number(document.getElementById("discoverOnvifTimeoutMs").value || 1500);
  const ports = portsRaw
    .split(",")
    .map((x) => Number(x.trim()))
    .filter((x) => Number.isFinite(x) && x > 0 && x <= 65535);
  fill(document.getElementById("discoverInfo"), ["검색 중..."]);
  try {
    const effectiveMaxHosts = (cidr.toLowerCase() === "auto" || cidr.toLowerCase() === "auto-full" || cidr.toLowerCase() === "all" || cidr.toLowerCase() === "full")
      ? Math.max(maxHosts, 8192)
      : maxHosts;
    const started = await api("/cameras/discover/jobs", {
      method: "POST",
      body: JSON.stringify({
        cidr,
        username,
        password,
        ports: ports.length ? ports : [554],
        maxHosts: effectiveMaxHosts,
        timeoutMs,
        useOnvif,
        onvifTimeoutMs,
      }),
    });
    fill(document.getElementById("discoverInfo"), [`검색 작업 시작: ${started.jobId?.slice(0, 8) || "-"}`]);
    await pollDiscoverJob(started.jobId);
  } catch (err) {
    fill(document.getElementById("discoverInfo"), [`검색 실패: ${err.message}`]);
  }
}

document.getElementById("discoverForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await runDiscover();
});

document.getElementById("discoverList").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-idx]");
  if (!btn) return;
  const idx = Number(btn.getAttribute("data-idx"));
  const item = discoverResults[idx];
  if (!item) return;
  try {
    const camName = await registerDiscoveredCamera(item);
    fill(document.getElementById("discoverInfo"), [`등록 완료: ${camName}`]);
    await refresh();
  } catch (err) {
    fill(document.getElementById("discoverInfo"), [`등록 실패: ${err.message}`]);
  }
});

document.getElementById("discoverRegisterAllBtn")?.addEventListener("click", async () => {
  await registerAllDiscovered();
});

document.getElementById("discoverScanAndRegisterBtn")?.addEventListener("click", async () => {
  await runDiscover();
  await registerAllDiscovered();
});

document.getElementById("cameraRotateCameraId")?.addEventListener("change", (e) => {
  const cameraId = e.target.value || "";
  setCameraRotateInfo([`카메라 선택: ${cameraId.slice(0, 8)} | 회전=${getCameraRotateDeg(cameraId)}도`]);
});

document.getElementById("cameraRotateBtn")?.addEventListener("click", async () => {
  await runWithSaving("cameraRotateForm", async () => {
    const cameraId = document.getElementById("cameraRotateCameraId")?.value || "";
    if (!cameraId) {
      setCameraRotateInfo(["회전 설정할 카메라를 먼저 선택하세요."], "warn");
      return;
    }
    const deg = await saveCameraRotateDeg(cameraId, (getCameraRotateDeg(cameraId) + 90) % 360);
    setCameraRotateInfo([`회전 적용: ${cameraId.slice(0, 8)} -> ${deg}도`], "ok");
    await refresh({ updateLiveGrid: true, reloadRoi: false });
  });
});

document.getElementById("cameraRotateResetBtn")?.addEventListener("click", async () => {
  await runWithSaving("cameraRotateForm", async () => {
    const cameraId = document.getElementById("cameraRotateCameraId")?.value || "";
    if (!cameraId) {
      setCameraRotateInfo(["원복할 카메라를 먼저 선택하세요."], "warn");
      return;
    }
    await saveCameraRotateDeg(cameraId, 0);
    setCameraRotateInfo([`원복 완료: ${cameraId.slice(0, 8)} -> 0도`], "ok");
    await refresh({ updateLiveGrid: true, reloadRoi: false });
  });
});

document.getElementById("authForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = document.getElementById("authUsername").value.trim();
  const password = document.getElementById("authPassword").value;
  try {
    const r = await api("/auth/login", { method: "POST", body: JSON.stringify({ username, password }), headers: { Authorization: "" } });
    localStorage.setItem("accessToken", r.accessToken || "");
    await refresh();
  } catch (err) {
    setAuthInfo([`로그인 실패: ${err.message}`], "warn");
  }
});

document.getElementById("logoutBtn").addEventListener("click", async () => {
  localStorage.removeItem("accessToken");
  await refresh();
});

document.getElementById("roiCameraId").addEventListener("change", async (e) => {
  roiCameraSelection = e.target.value;
  roiSnapshotDataUrl = "";
  roiPolygonDraft = [];
  await loadRoi(roiCameraSelection);
});

document.getElementById("roiForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await runWithSaving("roiForm", async () => {
    const f = new FormData(e.target);
    const cameraId = f.get("cameraId");
    if (!tryParseRoiTextarea()) return;
    await api(`/cameras/${cameraId}/roi`, { method: "PUT", body: JSON.stringify({ enabled: f.get("enabled") === "true", zones: roiZonesState }) });
    roiCameraSelection = cameraId;
    await loadRoi(cameraId);
  });
});

document.getElementById("roiZones").addEventListener("change", () => {
  tryParseRoiTextarea();
});

document.getElementById("roiAddBtn").addEventListener("click", () => {
  const z = sanitizeZone({ shape: "rect", name: `zone-${roiZonesState.length + 1}`, x: 0.2, y: 0.2, w: 0.2, h: 0.2 }, roiZonesState.length);
  roiZonesState.push(z);
  roiSelectedIndex = roiZonesState.length - 1;
  syncRoiTextarea();
  updateRoiSelectedLabel();
  drawRoiCanvas();
});

document.getElementById("roiDeleteBtn").addEventListener("click", () => {
  if (roiSelectedIndex < 0 || !roiZonesState[roiSelectedIndex]) return;
  roiZonesState.splice(roiSelectedIndex, 1);
  roiSelectedIndex = roiZonesState.length ? Math.min(roiSelectedIndex, roiZonesState.length - 1) : -1;
  syncRoiTextarea();
  updateRoiSelectedLabel();
  drawRoiCanvas();
});

document.getElementById("roiClearBtn").addEventListener("click", () => {
  roiZonesState = [];
  roiSelectedIndex = -1;
  roiPolygonDraft = [];
  syncRoiTextarea();
  updateRoiSelectedLabel();
  drawRoiCanvas();
});

document.getElementById("roiSnapshotBtn")?.addEventListener("click", async () => {
  const cameraId = document.getElementById("roiCameraId").value;
  await captureRoiSnapshotForCamera(cameraId);
});

document.getElementById("roiRectModeBtn")?.addEventListener("click", () => {
  roiDrawShape = "rect";
  roiPolygonDraft = [];
  drawRoiCanvas();
});

document.getElementById("roiPolyModeBtn")?.addEventListener("click", () => {
  roiDrawShape = "polygon";
  roiPolygonDraft = [];
  drawRoiCanvas();
});

document.getElementById("roiPolyDoneBtn")?.addEventListener("click", () => {
  if (roiPolygonDraft.length < 3) {
    fill(document.getElementById("roiInfo"), ["다각형은 최소 3개 점이 필요합니다."]);
    return;
  }
  const zone = sanitizeZone({ shape: "polygon", name: `zone-${roiZonesState.length + 1}`, points: roiPolygonDraft }, roiZonesState.length);
  roiZonesState.push(zone);
  roiSelectedIndex = roiZonesState.length - 1;
  roiPolygonDraft = [];
  syncRoiTextarea();
  updateRoiSelectedLabel();
  drawRoiCanvas();
});

document.getElementById("roiCanvas").addEventListener("mousedown", (e) => {
  const p = canvasPoint(e);
  if (roiDrawShape === "polygon") {
    roiPolygonDraft.push({ x: clamp01(p.x), y: clamp01(p.y) });
    drawRoiCanvas();
    return;
  }
  const hit = roiZonesState.findIndex((z) => zoneContains(z, p));
  roiDragStart = p;
  if (hit >= 0) {
    roiSelectedIndex = hit;
    roiDragMode = "move";
  } else {
    roiSelectedIndex = -1;
    roiDragMode = "draw";
    roiDrawRect = { x: p.x, y: p.y, w: 0, h: 0 };
  }
  updateRoiSelectedLabel();
  drawRoiCanvas();
});

document.getElementById("roiCanvas").addEventListener("mousemove", (e) => {
  if (!roiDragMode || !roiDragStart) return;
  const p = canvasPoint(e);
  if (roiDragMode === "move" && roiSelectedIndex >= 0) {
    const z = roiZonesState[roiSelectedIndex];
    const dx = p.x - roiDragStart.x;
    const dy = p.y - roiDragStart.y;
    if (z.shape === "polygon") {
      const points = Array.isArray(z.points) ? z.points : [];
      z.points = points.map((pt) => ({ x: clamp01(Number(pt.x || 0) + dx), y: clamp01(Number(pt.y || 0) + dy) }));
    } else {
      z.x = clamp01(z.x + dx);
      z.y = clamp01(z.y + dy);
      z.x = Math.min(z.x, 1 - z.w);
      z.y = Math.min(z.y, 1 - z.h);
    }
    roiDragStart = p;
  } else if (roiDragMode === "draw" && roiDrawRect) {
    const x1 = clamp01(roiDragStart.x);
    const y1 = clamp01(roiDragStart.y);
    const x2 = clamp01(p.x);
    const y2 = clamp01(p.y);
    roiDrawRect = { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1) };
  }
  syncRoiTextarea();
  updateRoiSelectedLabel();
  drawRoiCanvas();
});

document.addEventListener("mouseup", () => {
  if (!roiDragMode) return;
  if (roiDragMode === "draw" && roiDrawRect && roiDrawRect.w > 0.01 && roiDrawRect.h > 0.01) {
    const zone = sanitizeZone({ shape: "rect", name: `zone-${roiZonesState.length + 1}`, x: roiDrawRect.x, y: roiDrawRect.y, w: roiDrawRect.w, h: roiDrawRect.h }, roiZonesState.length);
    roiZonesState.push(zone);
    roiSelectedIndex = roiZonesState.length - 1;
  }
  roiDragMode = null;
  roiDragStart = null;
  roiDrawRect = null;
  syncRoiTextarea();
  updateRoiSelectedLabel();
  drawRoiCanvas();
});

document.getElementById("policyForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await runWithSaving("policyForm", async () => {
    const f = new FormData(e.target);
    await api(`/cameras/${f.get("cameraId")}/event-policy`, {
      method: "PATCH",
      body: JSON.stringify({
        eventType: f.get("eventType"),
        mode: f.get("mode"),
        clip: { preSec: Number(f.get("preSec")), postSec: Number(f.get("postSec")) },
        snapshot: { snapshotCount: Number(f.get("snapshotCount")), intervalMs: 0, format: "jpg" },
      }),
    });
    await refresh({ updateLiveGrid: false, reloadRoi: false });
  });
});

document.getElementById("destinationForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await runWithSaving("destinationForm", async () => {
    const f = new FormData(e.target);
    const cfg = {
      url: String(f.get("url") || "").trim(),
      apiMode: "cctv_img_v1",
      terminalId: String(f.get("terminalId") || "").trim(),
    };
    const baseCctvId = String(f.get("cctvId") || "").trim();
    if (baseCctvId) cfg.cctvId = Number(baseCctvId);
    const mapObj = parseJsonObjectOrNull(f.get("cctvIdByCameraId"), "cctvIdByCameraId");
    if (mapObj) cfg.cctvIdByCameraId = mapObj;
    const authTokenEnv = String(f.get("authTokenEnv") || "").trim();
    if (authTokenEnv) cfg.auth = { type: "bearer", token_env: authTokenEnv };
    await api("/destinations", {
      method: "POST",
      body: JSON.stringify({ name: String(f.get("name") || "").trim(), type: "https_post", config: cfg }),
    });
    e.target.reset();
    await refresh();
  });
});

document.getElementById("aiModelForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await runWithSaving("aiModelForm", async () => {
    const f = new FormData(e.target);
    await api("/settings/ai-model", {
      method: "PUT",
      body: JSON.stringify({
        enabled: f.get("enabled") === "true",
        modelPath: f.get("modelPath"),
        timeoutSec: Number(f.get("timeoutSec")),
        pollSec: Number(f.get("pollSec")),
        cooldownSec: Number(f.get("cooldownSec")),
      }),
    });
    await refresh({ updateLiveGrid: false, reloadRoi: false });
  });
});

document.getElementById("cameraModelCameraId")?.addEventListener("change", async (e) => {
  await loadCameraModelSettings(e.target.value);
});

document.getElementById("cameraModelForm")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  await runWithSaving("cameraModelForm", async () => {
    const f = new FormData(e.target);
    const cameraId = String(f.get("cameraId") || "");
    const prev = (await safeApi(`/cameras/${cameraId}/model-settings`, null)) || {};
    const conf = toSensitivityValue(f.get("confidenceThreshold") || 0.35);
    const body = {
      enabled: f.get("enabled") === "true",
      modelPath: String(f.get("modelPath") || ""),
      confidenceThreshold: conf,
      timeoutSec: Number(f.get("timeoutSec") || 5),
      pollSec: Number(f.get("pollSec") || 2),
      cooldownSec: Number(f.get("cooldownSec") || 10),
      // Keep camera-specific extra settings like rotationDeg.
      extra: prev && typeof prev.extra === "object" ? prev.extra : {},
    };
    await api(`/cameras/${cameraId}/model-settings`, { method: "PUT", body: JSON.stringify(body) });
    setCameraSensitivityUi(conf);
    await loadCameraModelSettings(cameraId);
  });
});

document.getElementById("eventPackCameraId")?.addEventListener("change", async (e) => {
  await loadCameraEventPack(e.target.value);
});

document.getElementById("eventPackForm")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  await runWithSaving("eventPackForm", async () => {
    const f = new FormData(e.target);
    const cameraId = String(f.get("cameraId") || "");
    let params = {};
    const rawParams = String(f.get("params") || "").trim();
    if (rawParams) {
      try {
        params = JSON.parse(rawParams);
      } catch (err) {
        fill(document.getElementById("eventPackInfo"), [`params JSON 오류: ${err.message}`]);
        return;
      }
    }
    const body = {
      enabled: f.get("enabled") === "true",
      packId: String(f.get("packId") || ""),
      packVersion: String(f.get("packVersion") || ""),
      params,
    };
    await api(`/cameras/${cameraId}/event-pack`, { method: "PUT", body: JSON.stringify(body) });
    await loadCameraEventPack(cameraId);
  });
});

document.getElementById("eventPackId")?.addEventListener("change", (e) => {
  const packId = e.target.value;
  const found = eventPacksState.find((p) => p.packId === packId);
  if (!found) return;
  document.getElementById("eventPackVersion").value = found.version || "";
});

document.getElementById("webrtcToggleForm")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  await runWithSaving("webrtcToggleForm", async () => {
    const enabled = document.getElementById("webrtcEnabled").value === "true";
    await api("/settings/webrtc", { method: "PUT", body: JSON.stringify({ enabled }) });
    await refresh({ updateLiveGrid: true, reloadRoi: false });
  });
});

document.getElementById("aiModelListRefreshBtn").addEventListener("click", async () => {
  const modelList = await safeApi("/models/list", { selectedPath: "", items: [] });
  const current = document.getElementById("aiModelPath").value.trim();
  renderAiModelPresets(modelList, current);
});

document.getElementById("aiModelPresetApplyBtn").addEventListener("click", () => {
  const sel = document.getElementById("aiModelPreset");
  const path = sel?.value || "";
  if (!path) {
    setAiModelPresetInfo(["적용할 모델을 먼저 선택하세요."], "warn");
    return;
  }
  document.getElementById("aiModelPath").value = path;
  setAiModelPresetInfo([`선택 경로 적용됨: ${path}`], "ok");
});

document.getElementById("aiModelPreset").addEventListener("change", (e) => {
  const path = e.target.value || "";
  if (!path) return;
  document.getElementById("aiModelPath").value = path;
});

document.getElementById("cameraModelListRefreshBtn")?.addEventListener("click", async () => {
  const modelList = await safeApi("/models/list", { selectedPath: "", items: [] });
  const current = document.getElementById("cameraModelPath")?.value?.trim() || "";
  renderCameraModelPresets(modelList, current);
});

document.getElementById("cameraModelPresetApplyBtn")?.addEventListener("click", () => {
  const sel = document.getElementById("cameraModelPreset");
  const path = sel?.value || "";
  if (!path) {
    setCameraModelPresetInfo(["적용할 모델을 먼저 선택하세요."], "warn");
    return;
  }
  document.getElementById("cameraModelPath").value = path;
  setCameraModelPresetInfo([`선택 경로 적용됨: ${path}`], "ok");
});

document.getElementById("cameraModelPreset")?.addEventListener("change", (e) => {
  const path = e.target.value || "";
  if (!path) return;
  document.getElementById("cameraModelPath").value = path;
});

document.getElementById("routeForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await runWithSaving("routeForm", async () => {
    const f = new FormData(e.target);
    await api("/routing-rules", {
      method: "POST",
      body: JSON.stringify({
        cameraId: f.get("cameraId"),
        eventType: "*",
        artifactKind: "snapshot",
        destinationId: f.get("destinationId"),
      }),
    });
    await refresh({ updateLiveGrid: false, reloadRoi: false });
  });
});

document.getElementById("eventForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  await api("/events", { method: "POST", body: JSON.stringify({ cameraId: f.get("cameraId"), type: f.get("type"), severity: f.get("severity"), payload: { source: "gui" } }) });
  await refresh();
});

document.getElementById("clearEventViewBtn").addEventListener("click", async () => {
  eventViewClearBeforeMs = Date.now();
  localStorage.setItem("eventViewClearBeforeMs", String(eventViewClearBeforeMs));
  setEventViewInfo(`화면 클리어 완료: ${formatTsLocal(eventViewClearBeforeMs)} 이후만 표시`, "warn");
  await refresh({ updateLiveGrid: false, reloadRoi: false });
});

document.getElementById("resetEventViewBtn").addEventListener("click", async () => {
  eventViewClearBeforeMs = 0;
  localStorage.removeItem("eventViewClearBeforeMs");
  setEventViewInfo("전체 이벤트 표시로 복원");
  await refresh({ updateLiveGrid: false, reloadRoi: false });
});

document.getElementById("dashClearEventLogBtn")?.addEventListener("click", async () => {
  if (currentUserRole !== "admin") return;
  eventViewClearBeforeMs = Date.now();
  localStorage.setItem("eventViewClearBeforeMs", String(eventViewClearBeforeMs));
  await refresh({ updateLiveGrid: false, reloadRoi: false });
});

document.getElementById("dashResetEventLogBtn")?.addEventListener("click", async () => {
  eventViewClearBeforeMs = 0;
  localStorage.removeItem("eventViewClearBeforeMs");
  await refresh({ updateLiveGrid: false, reloadRoi: false });
});

document.getElementById("personRuleForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await runWithSaving("personRuleForm", async () => {
    const f = new FormData(e.target);
    const body = {
      enabled: f.get("enabled") === "true",
      dwellSec: Number(f.get("dwellSec") || 5),
      cooldownSec: Number(f.get("cooldownSec") || 10),
      eventType: String(f.get("eventType") || "person_detected"),
      severity: String(f.get("severity") || "high"),
    };
    await api("/settings/person-event", { method: "PUT", body: JSON.stringify(body) });
    setPersonRuleInfo([
      `저장됨: enabled=${body.enabled}`,
      `지속감지초=${body.dwellSec}`,
      `쿨타임초=${body.cooldownSec}`,
      `eventType=${body.eventType}`,
      `severity=${body.severity}`,
    ], "ok");
    await refresh({ updateLiveGrid: false, reloadRoi: false });
  });
});

document.getElementById("refreshBtn").addEventListener("click", refresh);
document.getElementById("adminAuthBtn")?.addEventListener("click", () => {
  if (!window.PAGE_SECTION) applyMainTab("system");
  document.getElementById("section-auth")?.scrollIntoView({ behavior: "smooth", block: "start" });
});
document.getElementById("cameraMonitorBtn")?.addEventListener("click", () => {
  if (window.PAGE_SECTION) {
    document.getElementById("section-monitor")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  window.location.href = "/static/page-monitor.html";
});
document.getElementById("receiverRouteBtn")?.addEventListener("click", () => {
  if (currentUserRole !== "admin") return;
  if (window.PAGE_SECTION) {
    document.getElementById("section-route")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  window.location.href = "/static/network-settings.html";
});
document.getElementById("aiDebugBtn")?.addEventListener("click", () => {
  if (window.PAGE_SECTION) {
    document.getElementById("section-ai-debug")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  window.location.href = "/static/page-ai-debug.html";
});
document.getElementById("networkSettingBtn")?.addEventListener("click", () => {
  if (currentUserRole !== "admin") return;
  window.location.href = "/static/network-settings.html";
});
document.getElementById("dashCameraFilters")?.addEventListener("click", (e) => {
  const addDummyBtn = e.target.closest("button[data-action='add-dummy-camera']");
  if (addDummyBtn) {
    addDummyCamera().catch((err) => {
      setEventViewInfo(`더미 카메라 추가 실패: ${err.message}`, "warn");
    });
    return;
  }
  const actionBtn = e.target.closest("button[data-action='open-discover-modal']");
  if (actionBtn) {
    openDiscoverModal();
    document.getElementById("discoverCidr")?.focus();
    return;
  }
  const btn = e.target.closest("button[data-filter]");
  if (!btn) return;
  dashboardCameraFilter = btn.getAttribute("data-filter") || "__all__";
  renderDashboardFilters(dashboardLastCameras);
  renderDashboardCards(dashboardLastCameras);
});
document.getElementById("dashCameraCards")?.addEventListener("click", async (e) => {
  const delBtn = e.target.closest("button[data-camera-delete-card]");
  if (delBtn) {
    if (!(currentUserRole === "admin" || authEnabled === false)) return;
    const cameraId = delBtn.getAttribute("data-camera-delete-card") || "";
    if (!cameraId) return;
    openDeleteCameraModal(cameraId);
    return;
  }
  const btn = e.target.closest("button[data-camera-setting]");
  if (!btn || currentUserRole !== "admin") return;
  await openSettingsForCamera(btn.getAttribute("data-camera-setting") || "");
});
document.getElementById("snapshotScheduleForm")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("snapshotIntervalHours");
  dashboardSnapshotIntervalHours = normalizeSnapshotIntervalHours(input?.value || "1");
  if (input) input.value = String(dashboardSnapshotIntervalHours);
  localStorage.setItem("dashboardSnapshotIntervalHours", String(dashboardSnapshotIntervalHours));
  restartDashboardSnapshotTimer();
  setSnapshotIntervalInfo(`스냅샷 주기: ${dashboardSnapshotIntervalHours}시간`, "ok");
  await runDashboardSnapshotCycle(dashboardLastCameras, false);
  renderDashboardCards(dashboardLastCameras);
});
document.getElementById("discoverModalCloseBtn")?.addEventListener("click", closeDiscoverModal);
document.getElementById("discoverModal")?.addEventListener("click", (e) => {
  if (e.target?.id === "discoverModal") closeDiscoverModal();
});
document.getElementById("deleteCameraCancelBtn")?.addEventListener("click", closeDeleteCameraModal);
document.getElementById("deleteCameraModal")?.addEventListener("click", (e) => {
  if (e.target?.id === "deleteCameraModal") closeDeleteCameraModal();
});
document.getElementById("deleteCameraConfirmBtn")?.addEventListener("click", async () => {
  if (!(currentUserRole === "admin" || authEnabled === false)) return;
  const cameraId = pendingDeleteCameraId || "";
  if (!cameraId) return;
  try {
    await api(`/cameras/${cameraId}`, { method: "DELETE" });
    setSnapshotIntervalInfo(`카메라 삭제 완료: ${cameraId.slice(0, 8)}`, "ok");
    closeDeleteCameraModal();
    await refresh({ updateLiveGrid: false, reloadRoi: false });
  } catch (err) {
    setSnapshotIntervalInfo(`카메라 삭제 실패: ${err.message}`, "warn");
    setEventViewInfo(`카메라 삭제 실패: ${err.message}`, "warn");
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeDiscoverModal();
    closeDeleteCameraModal();
  }
});
document.getElementById("liveConfigForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await runWithSaving("liveConfigForm", async () => {
    const value = document.getElementById("webrtcBaseUrl").value.trim();
    if (!value) return;
    liveBaseUrl = value;
    localStorage.setItem("liveBaseUrl", value);
    await refresh({ updateLiveGrid: true, reloadRoi: false });
  });
});

document.getElementById("aiDebugForm").addEventListener("submit", (e) => {
  e.preventDefault();
  startAiDebugLoop();
});

document.getElementById("aiDebugStopBtn").addEventListener("click", () => {
  stopAiDebugLoop();
  setAiDebugInfo(["AI 디버그 루프 정지"], "warn");
});

document.getElementById("aiDebugSnapBtn").addEventListener("click", () => {
  fetchAiDebugPreview();
});

document.getElementById("aiDebugCameraId").addEventListener("change", () => {
  renderAiDebugLive(document.getElementById("aiDebugCameraId").value);
  fetchAiDebugPreview();
});

setInterval(() => {
  if (saveInFlightCount > 0) return;
  // Keep periodic polling lightweight: avoid touching live iframes and ROI editor state.
  refresh({ updateLiveGrid: false, reloadRoi: false }).catch(() => {});
}, 5000);

applyPageSectionFilter();
initMainTabs();
bindPanelHelpIcons();
applyDashboardFieldHints();
bindCameraSensitivityControl();
window.addEventListener("scroll", () => {
  if (helpTooltipAnchor) placeGlobalHelpTooltip();
}, { passive: true });
window.addEventListener("resize", () => {
  if (helpTooltipAnchor) placeGlobalHelpTooltip();
});
window.addEventListener("beforeunload", () => {
  if (dashboardSnapshotTimer) clearInterval(dashboardSnapshotTimer);
});
window.addEventListener("storage", (e) => {
  if (e.key !== "cameraSettingsUpdatedAt") return;
  const next = Number(e.newValue || "0");
  if (!Number.isFinite(next) || next <= lastCameraSettingsUpdatedAt) return;
  lastCameraSettingsUpdatedAt = next;
  refresh({ updateLiveGrid: false, reloadRoi: false }).catch(() => {});
});

dashboardSnapshotIntervalHours = normalizeSnapshotIntervalHours(dashboardSnapshotIntervalHours);
if (document.getElementById("snapshotIntervalHours")) {
  document.getElementById("snapshotIntervalHours").value = String(dashboardSnapshotIntervalHours);
  setSnapshotIntervalInfo(`스냅샷 주기: ${dashboardSnapshotIntervalHours}시간`);
}
restartDashboardSnapshotTimer();

refresh().catch((err) => {
  const camList = document.getElementById("cameraList");
  camList.innerHTML = "";
  row(camList, `오류: ${err.message}`, "warn");
  setAuthInfo([`오류: ${err.message}`], "warn");
});

