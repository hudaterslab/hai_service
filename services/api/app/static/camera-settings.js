async function api(path, options = {}) {
  const token = localStorage.getItem("accessToken") || "";
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { headers, ...options });
  if (res.status === 401) {
    localStorage.removeItem("accessToken");
    throw new Error("401 토큰 만료 또는 인증 실패");
  }
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(`${res.status} ${txt}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

function q(id) {
  return document.getElementById(id);
}

function row(el, text, cls = "") {
  const div = document.createElement("div");
  div.className = `item ${cls}`;
  div.textContent = text;
  el.appendChild(div);
}

function fill(el, rows, cls = "") {
  if (!el) return;
  el.innerHTML = "";
  rows.forEach((r) => row(el, r, cls));
}

function formatTsLocal(ts) {
  if (!ts) return "-";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  const parts = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Seoul",
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
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}.${values.fractionalSecond} KST`;
}

const EVENT_TYPES = [
  { value: "conveyor_crossing", label: "컨베이어벨트 횡단" },
  { value: "helmet_missing", label: "헬멧 미착용" },
  { value: "illegal_parking", label: "불법 주정차" },
  { value: "unauthorized_departure", label: "임의출발(신호수 지시없이 출발)" },
];

const ROI_REQUIRED_EVENTS = new Set(["conveyor_crossing", "unauthorized_departure"]);

// UI 이벤트명 <-> 엔진/레거시 이벤트명 동기화 매핑
const EVENT_POLICY_ALIASES = {
  conveyor_crossing: ["conveyor_crossing", "person_cross_roi"],
  helmet_missing: ["helmet_missing", "helmet_missing_in_roi"],
  illegal_parking: ["illegal_parking", "no_parking_stop"],
  unauthorized_departure: ["unauthorized_departure", "vehicle_move_without_signalman"],
};

function expandPolicyEventTypes(selected) {
  const out = new Set();
  (selected || []).forEach((ev) => {
    const aliases = EVENT_POLICY_ALIASES[ev] || [ev];
    aliases.forEach((name) => out.add(String(name)));
  });
  return Array.from(out);
}

let cameras = [];
let cameraId = "";
let baselineMainState = "";
let roiZones = [];
let roiSelected = -1;
let roiBgDataUrl = "";
let roiDragStart = null;
let roiDrawRect = null;
let roiDrawShape = "rect";
let roiPolygonDraft = [];

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

function clamp01(v) {
  if (!Number.isFinite(v)) return 0;
  return Math.max(0, Math.min(1, v));
}

function sanitizeZone(z, idx) {
  const shape = String(z?.shape || "rect").toLowerCase() === "polygon" ? "polygon" : "rect";
  const name = typeof z?.name === "string" && z.name.trim() ? z.name.trim() : `zone-${idx + 1}`;
  if (shape === "polygon") {
    const points = Array.isArray(z?.points)
      ? z.points
          .map((p) => ({ x: clamp01(Number(p?.x ?? 0)), y: clamp01(Number(p?.y ?? 0)) }))
          .filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y))
      : [];
    if (points.length >= 3) return { name, shape: "polygon", points };
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

function selectedEventTypes() {
  return Array.from(document.querySelectorAll("#eventTypePicker2 input[type='checkbox']:checked")).map((el) => String(el.value));
}

function currentMainState() {
  return JSON.stringify({
    cameraName: q("cameraName2")?.value || "",
    modelEnabled: q("cameraModelEnabled2")?.value || "false",
    modelPath: q("cameraModelPath2")?.value || "",
    conf: q("cameraModelConf2")?.value || "0.35",
    timeout: q("cameraModelTimeoutSec2")?.value || "5",
    poll: q("cameraModelPollSec2")?.value || "2",
    cooldown: q("cameraModelCooldownSec2")?.value || "10",
    events: selectedEventTypes().sort(),
    policyMode: q("policyMode2")?.value || "snapshot",
    pre: q("policyPreSec2")?.value || "10",
    post: q("policyPostSec2")?.value || "20",
    snapCnt: q("policySnapshotCount2")?.value || "1",
    snapInt: q("policySnapshotIntervalMs2")?.value || "0",
  });
}

function isMainDirty() {
  return baselineMainState !== "" && currentMainState() !== baselineMainState;
}

function updateRoiGateVisibility() {
  const selected = selectedEventTypes();
  const needRoi = selected.some((ev) => ROI_REQUIRED_EVENTS.has(ev));
  q("openRoiModalBtn").classList.toggle("is-hidden", !needRoi);
  q("roiRequiredHint").textContent = needRoi
    ? "선택한 이벤트에 ROI 설정이 필요합니다."
    : "현재 선택 이벤트에는 ROI가 필요하지 않습니다.";
}

function syncRoiText() {
  q("roiZones2").value = JSON.stringify(roiZones, null, 2);
}

function parseRoiText() {
  try {
    const arr = JSON.parse(q("roiZones2").value || "[]");
    if (!Array.isArray(arr)) throw new Error("array required");
    roiZones = arr.map((z, i) => sanitizeZone(z, i));
    roiSelected = Math.min(roiSelected, roiZones.length - 1);
    roiPolygonDraft = [];
    drawRoiCanvas();
    return true;
  } catch (err) {
    fill(q("cameraRoiOnlyInfo"), [`ROI JSON 오류: ${err.message}`], "warn");
    return false;
  }
}

function canvasPoint(e) {
  const c = q("roiCanvas2");
  const r = c.getBoundingClientRect();
  const x = ((e.clientX - r.left) * c.width) / Math.max(r.width, 1);
  const y = ((e.clientY - r.top) * c.height) / Math.max(r.height, 1);
  return { x: clamp01(x / c.width), y: clamp01(y / c.height) };
}

function zoneContains(z, p) {
  if (String(z?.shape || "rect") === "polygon" && Array.isArray(z?.points) && z.points.length >= 3) {
    let inside = false;
    for (let i = 0, j = z.points.length - 1; i < z.points.length; j = i, i += 1) {
      const xi = z.points[i].x;
      const yi = z.points[i].y;
      const xj = z.points[j].x;
      const yj = z.points[j].y;
      const intersects = yi > p.y !== yj > p.y && p.x < ((xj - xi) * (p.y - yi)) / ((yj - yi) || 1e-9) + xi;
      if (intersects) inside = !inside;
    }
    return inside;
  }
  return p.x >= z.x && p.x <= z.x + z.w && p.y >= z.y && p.y <= z.y + z.h;
}

function drawRoiLayer(ctx, c) {
  roiZones.forEach((z, i) => {
    ctx.fillStyle = i === roiSelected ? "rgba(255,120,80,0.25)" : "rgba(80,180,255,0.18)";
    ctx.strokeStyle = i === roiSelected ? "#ff9966" : "#7fd0ff";
    ctx.lineWidth = i === roiSelected ? 3 : 2;
    if (String(z.shape || "rect") === "polygon" && Array.isArray(z.points) && z.points.length >= 3) {
      ctx.beginPath();
      ctx.moveTo(z.points[0].x * c.width, z.points[0].y * c.height);
      for (let p = 1; p < z.points.length; p += 1) {
        ctx.lineTo(z.points[p].x * c.width, z.points[p].y * c.height);
      }
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    } else {
      const x = z.x * c.width;
      const y = z.y * c.height;
      const w = z.w * c.width;
      const h = z.h * c.height;
      ctx.fillRect(x, y, w, h);
      ctx.strokeRect(x, y, w, h);
    }
    ctx.fillStyle = "#fff";
    ctx.font = "12px IBM Plex Mono";
    const tx = String(z.shape || "rect") === "polygon" && Array.isArray(z.points) && z.points.length
      ? z.points[0].x * c.width
      : z.x * c.width;
    const ty = String(z.shape || "rect") === "polygon" && Array.isArray(z.points) && z.points.length
      ? z.points[0].y * c.height
      : z.y * c.height;
    ctx.fillText(z.name || `zone-${i + 1}`, tx + 4, ty + 14);
  });
  if (roiDrawRect) {
    ctx.strokeStyle = "#ffe58c";
    ctx.lineWidth = 2;
    ctx.strokeRect(roiDrawRect.x * c.width, roiDrawRect.y * c.height, roiDrawRect.w * c.width, roiDrawRect.h * c.height);
  }
  if (roiPolygonDraft.length > 0) {
    ctx.strokeStyle = "#ffe58c";
    ctx.fillStyle = "rgba(255,229,140,0.2)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(roiPolygonDraft[0].x * c.width, roiPolygonDraft[0].y * c.height);
    for (let i = 1; i < roiPolygonDraft.length; i += 1) {
      ctx.lineTo(roiPolygonDraft[i].x * c.width, roiPolygonDraft[i].y * c.height);
    }
    ctx.stroke();
    roiPolygonDraft.forEach((p) => {
      ctx.beginPath();
      ctx.arc(p.x * c.width, p.y * c.height, 4, 0, Math.PI * 2);
      ctx.fill();
    });
  }
}

function drawRoiCanvas() {
  const c = q("roiCanvas2");
  const ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  if (!roiBgDataUrl) {
    ctx.fillStyle = "#0e1b26";
    ctx.fillRect(0, 0, c.width, c.height);
    drawRoiLayer(ctx, c);
    return;
  }
  const img = new Image();
  img.onload = () => {
    ctx.drawImage(img, 0, 0, c.width, c.height);
    drawRoiLayer(ctx, c);
  };
  img.onerror = () => {
    ctx.fillStyle = "#0e1b26";
    ctx.fillRect(0, 0, c.width, c.height);
    drawRoiLayer(ctx, c);
  };
  img.src = roiBgDataUrl;
}

async function loadCameraList() {
  const rows = await api("/cameras");
  cameras = dedupeCamerasById(rows);
  const sel = q("cameraSelect");
  sel.innerHTML = cameras.map((c) => `<option value="${c.id}">${c.name} (${(c.id || "").slice(0, 8)})</option>`).join("");
  const fromQuery = new URLSearchParams(window.location.search).get("cameraId") || "";
  cameraId = cameras.some((c) => c.id === fromQuery) ? fromQuery : cameras[0]?.id || "";
  if (cameraId) sel.value = cameraId;
}

async function loadCameraMain() {
  const cam = cameras.find((x) => x.id === cameraId);
  if (!cam) return;
  q("cameraName2").value = cam.name || "";

  const chips = q("cameraSummaryChips");
  if (chips) {
    const statusClass = String(cam.status || "").toLowerCase() === "online" ? "ok" : "warn";
    chips.innerHTML = [
      `<span class="summary-chip">Name: ${cam.name || "-"}</span>`,
      `<span class="summary-chip ${statusClass}">Status: ${cam.status || "-"}</span>`,
      `<span class="summary-chip">WebRTC: ${cam.webrtcPath || "-"}</span>`,
    ].join("");
  }
  fill(q("cameraMainInfo"), [`cameraId=${(cam.id || "").slice(0, 8)}`, `rtsp=${cam.rtspUrl || "-"}`]);

  try {
    const cfg = await api(`/cameras/${cameraId}/model-settings`);
    q("cameraModelEnabled2").value = String(!!cfg.enabled);
    q("cameraModelPath2").value = cfg.modelPath || "";
    q("cameraModelConf2").value = String(cfg.confidenceThreshold ?? 0.35);
    q("cameraModelTimeoutSec2").value = String(cfg.timeoutSec ?? 5);
    q("cameraModelPollSec2").value = String(cfg.pollSec ?? 2);
    q("cameraModelCooldownSec2").value = String(cfg.cooldownSec ?? 10);
  } catch (_) {}

  const allPolicies = await api("/event-policies");
  const policies = allPolicies.filter((x) => x.cameraId === cameraId);
  const selected = new Set(policies.map((p) => String(p.eventType || "")));
  Array.from(document.querySelectorAll("#eventTypePicker2 input[type='checkbox']")).forEach((el) => {
    el.checked = selected.has(el.value);
  });
  const p0 = policies.length ? policies[0] : null;
  q("policyMode2").value = p0?.mode || "snapshot";
  q("policyPreSec2").value = String(p0?.clip?.preSec ?? 10);
  q("policyPostSec2").value = String(p0?.clip?.postSec ?? 20);
  q("policySnapshotCount2").value = String(p0?.snapshot?.snapshotCount ?? 1);
  q("policySnapshotIntervalMs2").value = String(p0?.snapshot?.intervalMs ?? 0);

  await loadRoi();
  updateRoiGateVisibility();
  baselineMainState = currentMainState();
}

async function captureSnapshot() {
  try {
    const snap = await api(`/cameras/${cameraId}/snapshot`, { method: "POST", body: "{}" });
    roiBgDataUrl = snap.imageDataUrl || "";
    drawRoiCanvas();
    fill(q("cameraRoiOnlyInfo"), [`snapshot=${formatTsLocal(snap.capturedAt)}`, `camera=${cameraId.slice(0, 8)}`], "ok");
    return;
  } catch (_) {}
  try {
    const prev = await api(`/dev/ai/preview?cameraId=${encodeURIComponent(cameraId)}`);
    roiBgDataUrl = prev.imageDataUrl || "";
    drawRoiCanvas();
    fill(q("cameraRoiOnlyInfo"), ["snapshot API 실패, AI preview 이미지를 사용합니다."], "warn");
  } catch (err) {
    fill(q("cameraRoiOnlyInfo"), [`스냅샷 실패: ${err.message}`], "warn");
  }
}

async function loadRoi() {
  const roi = await api(`/cameras/${cameraId}/roi`);
  q("roiEnabled2").value = String(!!roi.enabled);
  roiZones = (Array.isArray(roi.zones) ? roi.zones : []).map((z, i) => sanitizeZone(z, i));
  roiSelected = roiZones.length ? 0 : -1;
  roiPolygonDraft = [];
  syncRoiText();
  drawRoiCanvas();
}

async function saveMain(saveCameraId = "", options = {}) {
  const { reloadAfterSave = true } = options || {};
  const targetCameraId = String(saveCameraId || cameraId || q("cameraSelect")?.value || "").trim();
  if (!targetCameraId) {
    fill(q("cameraMainInfo"), ["저장할 카메라를 먼저 선택해주세요."], "warn");
    return false;
  }

  const selectedUiEvents = selectedEventTypes();
  if (!selectedUiEvents.length) {
    const okNoEvent = confirm("이벤트를 선택하지 않았습니다. 이 카메라는 이벤트 없이 저장됩니다. 계속할까요?");
    if (!okNoEvent) {
      fill(q("cameraMainInfo"), ["이벤트 미선택 저장이 취소되었습니다."], "warn");
      return false;
    }
  }
  const selected = expandPolicyEventTypes(selectedUiEvents);

  const warnings = [];
  const name = q("cameraName2").value.trim();
  try {
    await api(`/cameras/${targetCameraId}`, { method: "PATCH", body: JSON.stringify({ name }) });
  } catch (err) {
    if (String(err?.message || "").includes("404")) {
      warnings.push("카메라 이름 저장은 실패(404)했지만 다른 설정은 저장됩니다.");
    } else {
      throw err;
    }
  }

  const modelBody = {
    enabled: q("cameraModelEnabled2").value === "true",
    modelPath: q("cameraModelPath2").value.trim(),
    confidenceThreshold: Number(q("cameraModelConf2").value || "0.35"),
    timeoutSec: Number(q("cameraModelTimeoutSec2").value || "5"),
    pollSec: Number(q("cameraModelPollSec2").value || "2"),
    cooldownSec: Number(q("cameraModelCooldownSec2").value || "10"),
    extra: {},
  };
  await api(`/cameras/${targetCameraId}/model-settings`, { method: "PUT", body: JSON.stringify(modelBody) });

  const all = await api("/event-policies");
  const existing = all.filter((x) => x.cameraId === targetCameraId).map((x) => String(x.eventType || ""));
  const policyBody = {
    mode: q("policyMode2").value,
    clip: {
      preSec: Number(q("policyPreSec2").value || "10"),
      postSec: Number(q("policyPostSec2").value || "20"),
    },
    snapshot: {
      snapshotCount: Number(q("policySnapshotCount2").value || "1"),
      intervalMs: Number(q("policySnapshotIntervalMs2").value || "0"),
      format: "jpg",
    },
  };
  await Promise.all(
    selected.map((eventType) =>
      api(`/cameras/${targetCameraId}/event-policy`, {
        method: "PATCH",
        body: JSON.stringify({ ...policyBody, eventType }),
      })
    )
  );
  const stale = existing.filter((ev) => !selected.includes(ev));
  const deleteErrors = [];
  for (const ev of stale) {
    try {
      await api(`/cameras/${targetCameraId}/event-policy?eventType=${encodeURIComponent(ev)}`, { method: "DELETE" });
    } catch (err) {
      deleteErrors.push(`${ev}: ${err.message}`);
    }
  }
  if (deleteErrors.length) {
    throw new Error(`해제된 이벤트 정책 삭제 실패\n${deleteErrors.join("\n")}`);
  }

  if (reloadAfterSave) {
    await loadCameraList();
    const sel = q("cameraSelect");
    if (sel) {
      if (cameras.some((c) => c.id === targetCameraId)) {
        sel.value = targetCameraId;
        cameraId = targetCameraId;
      } else {
        cameraId = cameras[0]?.id || "";
        if (cameraId) sel.value = cameraId;
      }
    }
    await loadCameraMain();
  } else {
    baselineMainState = currentMainState();
  }
  try {
    localStorage.setItem("cameraSettingsUpdatedAt", String(Date.now()));
  } catch (_) {}
  if (warnings.length) {
    fill(q("cameraMainInfo"), ["카메라 세팅 저장 완료", ...warnings], "warn");
  } else {
    fill(q("cameraMainInfo"), ["카메라 세팅 저장 완료"], "ok");
  }
  return true;
}
function openRoiModal() {
  q("roiModal2").classList.add("open");
  q("roiModal2").setAttribute("aria-hidden", "false");
}

function closeRoiModal() {
  q("roiModal2").classList.remove("open");
  q("roiModal2").setAttribute("aria-hidden", "true");
}

q("cameraSelect").addEventListener("change", async (e) => {
  const nextId = e.target.value || "";
  if (!nextId || nextId === cameraId) return;
  if (isMainDirty()) {
    const doSave = confirm("저장되지 않은 변경사항이 있습니다.\n확인: 저장 후 변경\n취소: 저장 없이 변경");
    if (doSave) {
      try {
        const prevCameraId = cameraId;
        const ok = await saveMain(prevCameraId, { reloadAfterSave: false });
        if (!ok) {
          q("cameraSelect").value = cameraId;
          return;
        }
      } catch (err) {
        fill(q("cameraMainInfo"), [`저장 실패: ${err.message}`], "warn");
        q("cameraSelect").value = cameraId;
        return;
      }
    }
  }
  cameraId = nextId;
  window.history.replaceState({}, "", `/static/camera-settings.html?cameraId=${encodeURIComponent(cameraId)}`);
  await loadCameraMain();
});

q("cameraMainForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await saveMain();
  } catch (err) {
    fill(q("cameraMainInfo"), [`저장 실패: ${err.message}`], "warn");
  }
});

q("camSettingRefreshBtn").addEventListener("click", async () => {
  await loadCameraList();
  q("cameraSelect").value = cameraId;
  await loadCameraMain();
});

Array.from(document.querySelectorAll("#eventTypePicker2 input[type='checkbox']")).forEach((el) => {
  el.addEventListener("change", updateRoiGateVisibility);
});

q("openRoiModalBtn").addEventListener("click", async () => {
  openRoiModal();
  await captureSnapshot();
});
q("closeRoiModalBtn").addEventListener("click", closeRoiModal);
q("roiModal2").addEventListener("click", (e) => {
  if (e.target?.id === "roiModal2") closeRoiModal();
});

q("roiSnapshotBtn2").addEventListener("click", async () => {
  await captureSnapshot();
});
q("roiZones2").addEventListener("change", () => {
  parseRoiText();
});
q("roiDeleteBtn2").addEventListener("click", () => {
  if (roiSelected < 0 || !roiZones[roiSelected]) return;
  roiZones.splice(roiSelected, 1);
  roiSelected = roiZones.length ? Math.min(roiSelected, roiZones.length - 1) : -1;
  roiPolygonDraft = [];
  syncRoiText();
  drawRoiCanvas();
});
q("roiClearBtn2").addEventListener("click", () => {
  roiZones = [];
  roiSelected = -1;
  roiDrawRect = null;
  roiPolygonDraft = [];
  syncRoiText();
  drawRoiCanvas();
});
q("cameraRoiOnlyForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!parseRoiText()) return;
  await api(`/cameras/${cameraId}/roi`, {
    method: "PUT",
    body: JSON.stringify({
      enabled: q("roiEnabled2").value === "true",
      zones: roiZones,
    }),
  });
  fill(q("cameraRoiOnlyInfo"), ["ROI 저장 완료"], "ok");
});

function applyCameraSettingFieldHints() {
  const hints = {
    cameraSelect: "설정할 카메라를 선택합니다.",
    cameraName2: "대시보드/설정 화면에 표시될 카메라 이름입니다.",
    cameraModelEnabled2: "해당 카메라에서 모델 추론 사용 여부를 설정합니다.",
    cameraModelPath2: "사용할 모델 파일 경로를 입력합니다.",
    cameraModelConf2: "탐지 신뢰도 임계값입니다. 높을수록 엄격하게 탐지합니다.",
    cameraModelTimeoutSec2: "추론 타임아웃 시간(초)입니다.",
    cameraModelPollSec2: "카메라 상태/추론 폴링 주기(초)입니다.",
    cameraModelCooldownSec2: "이벤트 재발행 제한 시간(초)입니다.",
    policyMode2: "이벤트 발생 시 저장 타입(클립/스냅샷)을 선택합니다.",
    policyPreSec2: "클립 모드에서 이벤트 이전 저장 시간(초)입니다.",
    policyPostSec2: "클립 모드에서 이벤트 이후 저장 시간(초)입니다.",
    policySnapshotCount2: "스냅샷 모드에서 생성할 이미지 개수입니다.",
    policySnapshotIntervalMs2: "스냅샷 간격(밀리초)입니다.",
    openRoiModalBtn: "ROI가 필요한 이벤트일 때 영역 설정 창을 엽니다.",
    roiEnabled2: "ROI 필터 사용 여부입니다.",
    roiSnapshotBtn2: "현재 시점의 스냅샷을 불러옵니다.",
    roiDeleteBtn2: "선택한 ROI 영역을 삭제합니다.",
    roiClearBtn2: "ROI 영역을 모두 삭제합니다.",
    roiRectModeBtn2: "사각형 ROI 그리기 모드로 전환합니다.",
    roiPolyModeBtn2: "다각형 ROI 점찍기 모드로 전환합니다.",
    roiPolyDoneBtn2: "현재 점찍은 다각형을 ROI로 확정합니다.",
    roiZones2: "ROI JSON을 직접 수정할 수 있습니다.",
    cameraSaveBtn: "카메라 이름/모델/이벤트 설정을 한 번에 저장합니다.",
  };
  Object.entries(hints).forEach(([id, text]) => {
    const el = q(id);
    if (el) el.title = text;
  });
}

q("roiCanvas2").addEventListener("mousedown", (e) => {
  const p = canvasPoint(e);
  const hit = roiZones.findIndex((z) => zoneContains(z, p));
  if (hit >= 0) {
    roiSelected = hit;
    roiDragStart = null;
    roiDrawRect = null;
    roiPolygonDraft = [];
    drawRoiCanvas();
    return;
  }
  if (roiDrawShape === "polygon") {
    roiSelected = -1;
    roiPolygonDraft.push({ x: p.x, y: p.y });
    drawRoiCanvas();
    return;
  }
  roiSelected = -1;
  roiDragStart = p;
  roiDrawRect = { x: p.x, y: p.y, w: 0, h: 0 };
  drawRoiCanvas();
});
q("roiCanvas2").addEventListener("mousemove", (e) => {
  if (!roiDragStart || !roiDrawRect) return;
  const p = canvasPoint(e);
  const x = Math.min(roiDragStart.x, p.x);
  const y = Math.min(roiDragStart.y, p.y);
  const w = Math.abs(p.x - roiDragStart.x);
  const h = Math.abs(p.y - roiDragStart.y);
  roiDrawRect = { x, y, w, h };
  drawRoiCanvas();
});
window.addEventListener("mouseup", () => {
  if (roiDrawShape === "polygon") return;
  if (!roiDragStart || !roiDrawRect) return;
  if (roiDrawRect.w >= 0.01 && roiDrawRect.h >= 0.01) {
    roiZones.push(sanitizeZone({ ...roiDrawRect, name: `zone-${roiZones.length + 1}` }, roiZones.length));
    roiSelected = roiZones.length - 1;
    syncRoiText();
  }
  roiDragStart = null;
  roiDrawRect = null;
  drawRoiCanvas();
});

q("roiRectModeBtn2").addEventListener("click", () => {
  roiDrawShape = "rect";
  roiPolygonDraft = [];
  drawRoiCanvas();
});

q("roiPolyModeBtn2").addEventListener("click", () => {
  roiDrawShape = "polygon";
  roiDragStart = null;
  roiDrawRect = null;
  roiPolygonDraft = [];
  drawRoiCanvas();
});

q("roiPolyDoneBtn2").addEventListener("click", () => {
  if (roiPolygonDraft.length < 3) {
    fill(q("cameraRoiOnlyInfo"), ["다각형은 최소 3개 점이 필요합니다."], "warn");
    return;
  }
  roiZones.push(
    sanitizeZone(
      {
        name: `zone-${roiZones.length + 1}`,
        shape: "polygon",
        points: roiPolygonDraft,
      },
      roiZones.length
    )
  );
  roiSelected = roiZones.length - 1;
  roiPolygonDraft = [];
  syncRoiText();
  drawRoiCanvas();
});

async function init() {
  try {
    applyCameraSettingFieldHints();
    await loadCameraList();
    if (!cameraId) {
      fill(q("cameraMainInfo"), ["카메라가 없습니다. 대시보드에서 카메라를 먼저 등록해주세요."], "warn");
      return;
    }
    q("cameraSelect").value = cameraId;
    await loadCameraMain();
  } catch (err) {
    fill(q("cameraMainInfo"), [`초기화 실패: ${err.message}`], "warn");
  }
}

init();



