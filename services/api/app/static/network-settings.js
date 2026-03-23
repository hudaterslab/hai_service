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

function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

let destinationsState = [];
let routesState = [];

function parseJsonObject(text, fieldName) {
  const raw = String(text || "").trim();
  if (!raw) return null;
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    throw new Error(`${fieldName} JSON 파싱 실패: ${err.message}`);
  }
  if (parsed == null) return null;
  if (typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${fieldName}는 JSON 객체여야 합니다.`);
  }
  return parsed;
}

function resetDestinationForm() {
  const form = q("destinationForm2");
  if (!form) return;
  form.reset();
  q("destEditId2").value = "";
  q("destEditCancelBtn2").style.display = "none";
  const submit = form.querySelector("button[type='submit']");
  if (submit) submit.textContent = "목적지 저장";
}

function setDestinationEditMode(destination) {
  if (!destination) return;
  const cfg = destination.config || {};
  q("destEditId2").value = destination.id || "";
  q("destName2").value = destination.name || "";
  q("destUrl2").value = String(cfg.url || "");
  q("destTerminalId2").value = String(cfg.terminalId || "");
  q("destCctvId2").value = cfg.cctvId == null ? "" : String(cfg.cctvId);
  q("destCctvMap2").value = cfg.cctvIdByCameraId ? JSON.stringify(cfg.cctvIdByCameraId, null, 2) : "";
  q("destAuthTokenEnv2").value = String((cfg.auth || {}).token_env || "");
  q("destEditCancelBtn2").style.display = "";
  const submit = q("destinationForm2").querySelector("button[type='submit']");
  if (submit) submit.textContent = "목적지 업데이트";
}

function renderDestinationList(destinations) {
  const el = q("destinationList2");
  if (!el) return;
  destinationsState = Array.isArray(destinations) ? destinations : [];
  if (!destinations.length) {
    fill(el, ["등록된 목적지가 없습니다."], "warn");
    return;
  }
  el.innerHTML = destinations
    .map((d) => {
      const cfg = d.config || {};
      const terminal = cfg.terminalId ? ` | terminalId=${escapeHtml(cfg.terminalId)}` : "";
      const hasMap = cfg.cctvIdByCameraId && typeof cfg.cctvIdByCameraId === "object" ? " | cameraMap=Y" : "";
      const enabled = !!d.enabled;
      return (
        `<div class="item">` +
        `<span>${escapeHtml(d.name)} | ${escapeHtml(d.type)} | 통신=${enabled ? "ON" : "OFF"} | apiMode=cctv_img_v1${terminal}${hasMap}</span>` +
        ` <button type="button" class="mini-btn" data-destination-toggle="${escapeHtml(d.id)}">${enabled ? "통신 OFF" : "통신 ON"}</button>` +
        ` <button type="button" class="mini-btn" data-destination-edit="${escapeHtml(d.id)}">편집</button>` +
        ` <button type="button" class="mini-btn danger-btn" data-destination-delete="${escapeHtml(d.id)}">삭제</button>` +
        `</div>`
      );
    })
    .join("");
}

function renderRouteList(routes) {
  const el = q("routeList2");
  if (!el) return;
  routesState = Array.isArray(routes) ? routes : [];
  if (!routes.length) {
    fill(el, ["등록된 라우팅이 없습니다."], "warn");
    return;
  }
  el.innerHTML = routes
    .map((r) => {
      const enabled = !!r.enabled;
      return (
        `<div class="item">` +
        `<span>${escapeHtml((r.cameraId || "").slice(0, 8))} | 이벤트:* | snapshot -> ${escapeHtml((r.destinationId || "").slice(0, 8))} | 통신=${enabled ? "ON" : "OFF"}</span>` +
        ` <button type="button" class="mini-btn" data-route-toggle="${escapeHtml(r.id)}">${enabled ? "통신 OFF" : "통신 ON"}</button>` +
        ` <button type="button" class="mini-btn danger-btn" data-route-delete="${escapeHtml(r.id)}">삭제</button>` +
        `</div>`
      );
    })
    .join("");
}

function applyNetworkFieldHints() {
  const hints = {
    destName2: "목적지 식별용 이름입니다.",
    destUrl2: "CCTV 수집 API URL입니다.",
    destTerminalId2: "필수 terminalId 입니다.",
    destCctvId2: "카메라 매핑이 없을 때 쓸 기본 cctvId 입니다.",
    destCctvMap2: "cameraId -> cctvId 매핑 JSON입니다.",
    destAuthTokenEnv2: "Bearer 토큰 환경변수 이름입니다(선택).",
    routeCameraId2: "라우팅을 적용할 카메라를 선택합니다.",
    routeDestinationId2: "전송받을 목적지를 선택합니다.",
    networkRefreshBtn: "목적지/라우팅 목록을 다시 불러옵니다.",
  };
  Object.entries(hints).forEach(([id, text]) => {
    const el = q(id);
    if (el) el.title = text;
  });
}

async function refreshData() {
  const [cameras, destinations, routes] = await Promise.all([api("/cameras"), api("/destinations"), api("/routing-rules")]);

  q("routeCameraId2").innerHTML = cameras.map((c) => `<option value="${c.id}">${c.name} (${(c.id || "").slice(0, 8)})</option>`).join("");
  q("routeDestinationId2").innerHTML = destinations.map((d) => `<option value="${d.id}">${d.name}</option>`).join("");

  renderDestinationList(destinations);
  renderRouteList(routes);
}

q("destinationForm2").addEventListener("submit", async (e) => {
  e.preventDefault();
  const editId = q("destEditId2").value.trim();
  const name = q("destName2").value.trim();
  const url = q("destUrl2").value.trim();
  const terminalId = q("destTerminalId2").value.trim();
  const cctvIdRaw = q("destCctvId2").value.trim();
  const cctvMapRaw = q("destCctvMap2").value;
  const authTokenEnv = q("destAuthTokenEnv2").value.trim();

  try {
    const config = {
      url,
      apiMode: "cctv_img_v1",
      terminalId,
    };
    if (cctvIdRaw) config.cctvId = Number(cctvIdRaw);
    const parsedMap = parseJsonObject(cctvMapRaw, "cctvIdByCameraId");
    if (parsedMap) config.cctvIdByCameraId = parsedMap;
    if (authTokenEnv) config.auth = { type: "bearer", token_env: authTokenEnv };

    const body = { name, enabled: true, config };
    if (editId) {
      await api(`/destinations/${encodeURIComponent(editId)}`, { method: "PATCH", body: JSON.stringify(body) });
      fill(q("destinationInfo2"), ["목적지 업데이트 완료"], "ok");
    } else {
      await api("/destinations", { method: "POST", body: JSON.stringify({ ...body, type: "https_post" }) });
      fill(q("destinationInfo2"), ["목적지 저장 완료"], "ok");
    }

    resetDestinationForm();
    await refreshData();
  } catch (err) {
    fill(q("destinationInfo2"), [`목적지 저장 실패: ${err.message}`], "warn");
  }
});

q("routeForm2").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    cameraId: q("routeCameraId2").value,
    eventType: "*",
    artifactKind: "snapshot",
    destinationId: q("routeDestinationId2").value,
  };
  try {
    await api("/routing-rules", { method: "POST", body: JSON.stringify(body) });
    fill(q("routeInfo2"), ["라우팅 저장 완료"], "ok");
    await refreshData();
  } catch (err) {
    fill(q("routeInfo2"), [`라우팅 저장 실패: ${err.message}`], "warn");
  }
});

q("networkRefreshBtn").addEventListener("click", async () => {
  try {
    await refreshData();
  } catch (err) {
    fill(q("routeInfo2"), [`새로고침 실패: ${err.message}`], "warn");
  }
});

q("destinationList2").addEventListener("click", async (e) => {
  const toggleBtn = e.target.closest("button[data-destination-toggle]");
  if (toggleBtn) {
    const id = toggleBtn.getAttribute("data-destination-toggle") || "";
    const destination = destinationsState.find((d) => String(d.id) === String(id));
    if (!destination) {
      fill(q("destinationInfo2"), ["토글할 목적지를 찾지 못했습니다."], "warn");
      return;
    }
    try {
      const nextEnabled = !destination.enabled;
      await api(`/destinations/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ enabled: nextEnabled }) });
      fill(q("destinationInfo2"), [`목적지 통신 ${nextEnabled ? "ON" : "OFF"} 적용 완료`], "ok");
      await refreshData();
    } catch (err) {
      fill(q("destinationInfo2"), [`목적지 통신 토글 실패: ${err.message}`], "warn");
    }
    return;
  }

  const editBtn = e.target.closest("button[data-destination-edit]");
  if (editBtn) {
    const id = editBtn.getAttribute("data-destination-edit") || "";
    const destination = destinationsState.find((d) => String(d.id) === String(id));
    if (!destination) {
      fill(q("destinationInfo2"), ["편집할 목적지를 찾지 못했습니다."], "warn");
      return;
    }
    setDestinationEditMode(destination);
    fill(q("destinationInfo2"), [`편집 모드: ${destination.name}`], "ok");
    return;
  }

  const btn = e.target.closest("button[data-destination-delete]");
  if (!btn) return;
  const id = btn.getAttribute("data-destination-delete");
  if (!id) return;
  const ok = confirm("이 목적지를 삭제할까요? 연결된 라우팅/전송시도도 함께 제거됩니다.");
  if (!ok) return;
  try {
    await api(`/destinations/${encodeURIComponent(id)}`, { method: "DELETE" });
    fill(q("destinationInfo2"), ["목적지 삭제 완료"], "ok");
    if (q("destEditId2").value === String(id || "")) resetDestinationForm();
    await refreshData();
  } catch (err) {
    fill(q("destinationInfo2"), [`목적지 삭제 실패: ${err.message}`], "warn");
  }
});

q("destEditCancelBtn2").addEventListener("click", () => {
  resetDestinationForm();
  fill(q("destinationInfo2"), ["편집 모드 해제"], "ok");
});

q("routeList2").addEventListener("click", async (e) => {
  const toggleBtn = e.target.closest("button[data-route-toggle]");
  if (toggleBtn) {
    const id = toggleBtn.getAttribute("data-route-toggle") || "";
    const route = routesState.find((r) => String(r.id) === String(id));
    if (!route) {
      fill(q("routeInfo2"), ["토글할 라우팅을 찾지 못했습니다."], "warn");
      return;
    }
    try {
      const nextEnabled = !route.enabled;
      await api(`/routing-rules/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ enabled: nextEnabled }) });
      fill(q("routeInfo2"), [`라우팅 통신 ${nextEnabled ? "ON" : "OFF"} 적용 완료`], "ok");
      await refreshData();
    } catch (err) {
      fill(q("routeInfo2"), [`라우팅 통신 토글 실패: ${err.message}`], "warn");
    }
    return;
  }

  const btn = e.target.closest("button[data-route-delete]");
  if (!btn) return;
  const id = btn.getAttribute("data-route-delete");
  if (!id) return;
  const ok = confirm("이 라우팅을 삭제할까요?");
  if (!ok) return;
  try {
    await api(`/routing-rules/${encodeURIComponent(id)}`, { method: "DELETE" });
    fill(q("routeInfo2"), ["라우팅 삭제 완료"], "ok");
    await refreshData();
  } catch (err) {
    fill(q("routeInfo2"), [`라우팅 삭제 실패: ${err.message}`], "warn");
  }
});

applyNetworkFieldHints();
resetDestinationForm();
refreshData().catch((err) => {
  fill(q("routeInfo2"), [`초기화 실패: ${err.message}`], "warn");
});
