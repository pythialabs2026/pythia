/* ============================================================
   Stoa — 7:3 Portfolio Monitor (frontend logic)
   ============================================================ */

// 프로덕션 기본 = LIVE 모드 (서버에서 직접 서빙). ?mock=1로 모킹.
const FORCE_LIVE = window.__FORCE_LIVE__ === true;
const USE_MOCK = new URLSearchParams(location.search).get("mock") === "1";
const LIVE = FORCE_LIVE && !USE_MOCK;
const API_BASE = LIVE ? "" : "./mock_data";
const ENDPOINTS = LIVE ? {
  portfolio: "/api/portfolio",
  shield:    "/api/shield",
  prices:    "/api/prices",
  journal:   "/api/journal",
  bot:       "/api/bot_status",
  glide:     "/api/glide",
  stress:    "/api/stress",
} : {
  portfolio: `${API_BASE}/portfolio.json`,
  shield:    `${API_BASE}/shield.json`,
  prices:    `${API_BASE}/prices.json`,
  journal:   `${API_BASE}/journal.json`,
  bot:       `${API_BASE}/bot_status.json`,
  glide:     `${API_BASE}/glide.json`,
  stress:    `${API_BASE}/stress.json`,
};

// ── helpers ─────────────────────────────────────────
const $  = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => Array.from(root.querySelectorAll(s));
const fmtKRW = n => "₩" + Math.round(n).toLocaleString();
const fmtKRWcompact = n => {
  const abs = Math.abs(n);
  if (abs >= 1e8) return "₩" + (n/1e8).toFixed(2) + "억";
  if (abs >= 1e4) return "₩" + (n/1e4).toFixed(0) + "만";
  return "₩" + Math.round(n).toLocaleString();
};
const fmtUSD = n => "$" + (n||0).toLocaleString(undefined, {maximumFractionDigits: 2});

// 데이터 최신성 — 절대시각 X / 상대시각 O
let lastDataAt = null;
function relativeTime(d) {
  const t = (d instanceof Date) ? d : new Date(d || Date.now());
  const mins = Math.round((Date.now() - t.getTime()) / 60000);
  if (mins < 1) return "방금 갱신";
  if (mins < 60) return `${mins}분 전`;
  const h = Math.floor(mins / 60);
  if (h < 24) return `${h}시간 전`;
  return t.toLocaleDateString("ko-KR", {month:"short", day:"numeric"});
}
function tickFreshness() {
  const el = document.getElementById("freshness-tag");
  if (!el || !lastDataAt) return;
  const txt = " · " + relativeTime(lastDataAt);
  el.textContent = txt;
  // 2분 초과시 stale 표시 (라이브 dot도 동기화)
  const mins = (Date.now() - lastDataAt.getTime()) / 60000;
  el.classList.toggle("stale", mins > 2);
  const liveDot = document.getElementById("live-dot");
  if (liveDot) liveDot.classList.toggle("stale", mins > 2);
}
// 미국 정규장 판정 (동부시각 기준, DST 자동 처리) → 시장가/지정가 전환
function marketSession() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", weekday: "short",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(new Date());
  const get = t => (parts.find(p => p.type === t) || {}).value;
  const wd = get("weekday");
  let hh = parseInt(get("hour")); if (hh === 24) hh = 0;
  const mm = parseInt(get("minute"));
  const mins = hh * 60 + mm;
  const isWeekday = !["Sat", "Sun"].includes(wd);
  const weekend = !isWeekday;  // 토/일 = 시간외도 없음, 월요일 정규장까지 전면 휴장
  const open = isWeekday && mins >= (9 * 60 + 30) && mins < (16 * 60);  // 09:30~16:00 ET
  // 다음 개장까지 대략 표시
  let untilOpen = "휴장";
  if (!open) {
    if (weekend) untilOpen = "주말";
    else if (mins < (9 * 60 + 30)) untilOpen = "개장 전";
    else untilOpen = "마감 후";
  }
  return { open, weekend, untilOpen };
}
const fmtPct = (n, dec=2) => (n).toFixed(dec) + "%";
const fmtSign = (n, fmt) => (n>=0?"+":"−") + fmt(Math.abs(n));
const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));

async function fetchJSON(url) {
  try {
    const r = await fetch(url, {cache: "no-store"});
    if (r.status === 401) { location.href = "/login"; return { ok: false, why: "auth" }; }
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.json();
  } catch (e) {
    return { ok: false, why: e.toString() };
  }
}

// ── /api/portfolio ──────────────────────────────────
async function loadPortfolio() {
  const d = await fetchJSON(ENDPOINTS.portfolio);
  if (!d.ok) {
    $("#total-krw").textContent = "—";
    $("#total-usd").textContent = d.why || "데이터 없음";
    return null;
  }
  const a = d.aggregate || {};

  // Totals
  $("#total-krw").innerHTML = fmtKRWcompact(a.total_krw) +
    `<span class="unit">${fmtKRW(a.total_krw).replace("₩","₩ ")}</span>`;
  // 데이터 최신성: 서버 last_updated 저장 → 30초마다 "N분 전"으로 재렌더
  lastDataAt = new Date(d.last_updated || Date.now());
  $("#total-usd").innerHTML = fmtUSD(a.total_usd) +
    `<span class="freshness" id="freshness-tag" data-iso="${lastDataAt.toISOString()}"> · ${relativeTime(lastDataAt)}</span>`;

  // PnL today / cumulative
  let dailyKRW = 0, cumKRW = 0;
  for (const p of (d.positions || [])) {
    dailyKRW += (p.market_value || 0) * (p.daily_profit_rate || 0);
    cumKRW   += (p.unrealized_pnl || 0);
  }
  const dailyEl = $("#daily-pnl");
  const cumEl   = $("#cum-pnl");
  dailyEl.textContent = fmtSign(dailyKRW, fmtKRW);
  dailyEl.className = "v " + (dailyKRW >= 0 ? "ok" : "bad");
  cumEl.textContent = fmtSign(cumKRW, fmtKRW);
  cumEl.className = "v " + (cumKRW >= 0 ? "ok" : "bad");

  // 원금 vs 평가수익 분리 (적립 동기 / 단기 노이즈 둔감)
  const total = a.total_krw || 0;
  const principal = total - cumKRW;     // 원금 = 총자산 - 누적평가손익
  const pEl = $("#pg-principal"), gEl = $("#pg-gain"), rEl = $("#cum-rate");
  if (pEl) pEl.textContent = fmtKRWcompact(principal);
  if (gEl) { gEl.textContent = fmtSign(cumKRW, fmtKRWcompact); gEl.className = cumKRW >= 0 ? "ok" : "bad"; }
  if (rEl && principal > 0) {
    const rate = (cumKRW / principal) * 100;
    rEl.textContent = (rate >= 0 ? "+" : "−") + Math.abs(rate).toFixed(1) + "%";
    rEl.className = "kv-rate " + (rate >= 0 ? "ok" : "bad");
  }
  // 분리 바: 원금 비율 vs 수익 비율 (수익<0이면 원금만 가득)
  const pbar = $("#pg-bar-principal"), gbar = $("#pg-bar-gain");
  if (pbar && gbar) {
    if (cumKRW >= 0 && total > 0) {
      pbar.style.width = (principal / total * 100) + "%";
      gbar.style.width = (cumKRW / total * 100) + "%";
      gbar.className = "pg-gain";
    } else {
      pbar.style.width = "100%"; gbar.style.width = "0%";
    }
  }

  // Ratio
  const qq = (a.qqqm_ratio || 0) * 100;
  const tq = (a.tqqq_ratio || 0) * 100;
  $("#ratio-qq").textContent = qq.toFixed(1) + "%";
  $("#ratio-tq").textContent = tq.toFixed(1) + "%";
  $("#seg-stable").style.width = qq + "%";
  $("#seg-aggro").style.width  = tq + "%";

  // Holdings table
  renderHoldings(d.positions || []);
  return d;
}

// ── 다음달 DCA 입력 (글라이드 실시간 반영) ──────────────
let userDca = (() => {
  const s = localStorage.getItem("stoa_dca");
  return s !== null && s !== "" ? parseInt(s) : null;
})();
function setupDcaInput() {
  const input = document.getElementById("dca-input");
  const btn = document.getElementById("dca-save");
  if (!input) return;
  if (userDca !== null && !Number.isNaN(userDca)) input.value = userDca.toLocaleString();
  const parse = () => parseInt((input.value || "").replace(/[^0-9]/g, "")) || 0;
  const apply = async () => {
    const v = parse();
    if (v < 0) return;
    input.value = v.toLocaleString();
    userDca = v;
    localStorage.setItem("stoa_dca", String(v));
    // 서버 설정도 갱신 (봇과 동기화) — 미리보기 즉시 + 저장
    try { await fetch(`/api/glide_config?dca=${v}`, { method: "POST" }); } catch (e) {}
    loadGlide();
  };
  btn && btn.addEventListener("click", apply);
  input.addEventListener("keydown", e => { if (e.key === "Enter") apply(); });
  input.addEventListener("blur", apply);
  // 입력 중 천단위 콤마
  input.addEventListener("input", () => {
    const v = parse();
    input.value = v ? v.toLocaleString() : "";
  });
}

// ── /api/glide → 동적 글라이드 목표 + 리밸 신호 ──────────
async function loadGlide() {
  const url = ENDPOINTS.glide + (userDca ? `?dca=${userDca}` : "");
  const d = await fetchJSON(url);
  if (!d.ok) return;
  _glide = d;
  const badge = $("#glide-target-badge");
  if (badge) badge.textContent = `동적 목표 ${d.target_qqqm_pct.toFixed(0)} : ${d.target_tqqq_pct.toFixed(0)}`;
  const rEl = $("#glide-r"); if (rEl) rEl.textContent = d.r_pct.toFixed(0) + "%";
  const curLev = $("#cur-lev");
  if (curLev) curLev.textContent = d.current_leverage.toFixed(2) + "x";
  const tgtLev = $("#target-lev");
  if (tgtLev) tgtLev.textContent = d.target_leverage.toFixed(2) + "x (TQQQ " + d.target_tqqq_pct.toFixed(0) + "%)";
  // 목표 마커 위치 + 라벨 (안정 비중 기준 = 좌측부터)
  const mark = $("#glide-target-mark");
  if (mark) {
    mark.style.left = d.target_qqqm_pct + "%";
    mark.setAttribute("data-target-label", "목표 " + d.target_qqqm_pct.toFixed(0) + "%");
  }
  // 리밸 신호
  const chip = $("#drift-chip");
  if (chip) {
    const drift = d.drift_pp || 0;
    let cls = "ok", txt;
    const moveAbs = Math.abs(d.rebal_move_krw || 0);
    if (d.rebal_status === "red") {
      cls = "bad";
      const dir = d.rebal_move_krw > 0 ? "TQQQ 매수" : "TQQQ 매도";
      txt = `목표 대비 ${Math.abs(drift).toFixed(1)}%p · ${dir} ₩${Math.round(moveAbs).toLocaleString()}`;
    } else if (d.rebal_status === "yellow") {
      cls = "warn";
      txt = `목표 대비 ${Math.abs(drift).toFixed(1)}%p · 다음 입금시 조정`;
    } else {
      cls = "ok";
      txt = `목표 ±${Math.abs(drift).toFixed(1)}%p · 그대로 유지`;
    }
    chip.className = "chip " + cls;
    chip.innerHTML = `<span class="dot"></span>${txt}`;
  }
  // 구체적 주문 박스 (리밸 필요시만) — KST/장시간 따라 시장가↔지정가 자동전환
  const box = $("#order-box");
  const list = $("#order-list");
  if (box && list) {
    if (d.rebal_status === "red" && (d.orders || []).length) {
      const sess = marketSession();
      let tag;
      if (sess.open) {
        tag = `<div class="order-sess open">🟢 미국 정규장 · <strong>시장가</strong> 즉시 체결</div>`;
      } else if (sess.weekend) {
        tag = `<div class="order-sess closed">🌴 주말 휴장 · 월요일 정규장(KST 22:30~) <strong>시장가</strong> 추천 · 아래 가격은 금요일 종가 참고치(월요일엔 변동)</div>`;
      } else {
        tag = `<div class="order-sess closed">🌙 시간외(${sess.untilOpen}) · <strong>지정가</strong> 권장</div>`;
      }
      const rows = d.orders.map(o => {
        const cls = o.side === "buy" ? "buy" : "sell";
        let px;
        if (sess.open) px = `시장가 ≈$${o.current_usd}`;
        else if (sess.weekend) px = `참고가 $${o.current_usd}`;
        else px = `지정가 $${o.limit_usd}`;
        return `<div class="order-row ${cls}">
          <span class="o-side">${o.side_kr}</span>
          <span class="o-tk">${o.ticker}</span>
          <span class="o-sh">${o.shares}주</span>
          <span class="o-px">${px}</span>
        </div>`;
      }).join("");
      list.innerHTML = tag + rows;
      box.style.display = "block";
    } else {
      box.style.display = "none";
    }
  }
  checkRebalNotify(d);
  renderVerdict();  // 글라이드 갱신 → 권장 행동 재계산
}

// ── 계정 / 설정 (로그인·토스 등록·로그아웃) ─────────────
async function loadMe() {
  const d = await fetchJSON("/api/auth/me");
  if (!d.ok) { location.href = "/login"; return; }
  const em = document.getElementById("acct-email");
  if (em) em.textContent = d.email || "";
  renderTossStatus(d.toss);
}
function renderTossStatus(toss) {
  const el = document.getElementById("toss-status");
  if (!el) return;
  if (toss && toss.registered) {
    el.textContent = "✅ 연동됨"; el.className = "set-status ok";
  } else {
    el.textContent = "⚠️ 미등록"; el.className = "set-status warn";
  }
}
function setupSettings() {
  const btn = document.getElementById("settings-btn");
  const panel = document.getElementById("settings-panel");
  if (btn && panel) btn.addEventListener("click", () => {
    panel.style.display = panel.style.display === "none" ? "block" : "none";
    if (panel.style.display === "block") panel.scrollIntoView({ behavior: "smooth" });
  });
  const save = document.getElementById("toss-save");
  const del = document.getElementById("toss-del");
  const input = document.getElementById("toss-input");
  const msg = document.getElementById("toss-msg");
  if (save) save.addEventListener("click", async () => {
    const session = (input.value || "").trim();
    if (!session) { msg.textContent = "세션 JSON을 붙여넣으세요"; msg.className = "set-msg err"; return; }
    msg.textContent = "등록 중…"; msg.className = "set-msg";
    const r = await fetch("/api/toss/register", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session }) });
    const d = await r.json();
    if (d.ok) { msg.textContent = "✅ 등록 완료"; msg.className = "set-msg ok"; input.value = "";
                loadMe(); refreshAll(true); }
    else { msg.textContent = d.why || "실패"; msg.className = "set-msg err"; }
  });
  if (del) del.addEventListener("click", async () => {
    if (!confirm("토스 연동을 해제할까요?")) return;
    await fetch("/api/toss", { method: "DELETE" });
    msg.textContent = "연동 해제됨"; msg.className = "set-msg"; loadMe();
  });
  const lo = document.getElementById("logout-btn");
  if (lo) lo.addEventListener("click", async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    location.href = "/login";
  });
}

// ── /api/stress → 과거 폭락장 스트레스 테스트 (패닉셀 예방) ──
async function loadStress() {
  const rows = $("#stress-rows");
  if (!rows) return;
  const d = await fetchJSON(ENDPOINTS.stress);
  if (!d.ok || !(d.windows || []).length) {
    rows.innerHTML = `<div class="stress-loading">데이터 없음</div>`;
    return;
  }
  const lev = $("#stress-lev");
  if (lev) lev.textContent = `${d.target_leverage}x · TQQQ ${d.tqqq_pct}%`;
  rows.innerHTML = d.windows.map(w => {
    const naked = Math.abs(w.naked_mdd), shield = Math.abs(w.shield_mdd);
    return `<div class="stress-row">
      <div class="s-name">${w.name}<span class="s-period">${w.period}</span></div>
      <div class="s-bars">
        <div class="s-bar-row"><span class="s-lbl naked">무방패</span>
          <div class="s-track"><div class="s-fill naked" style="width:${Math.min(naked,100)}%"></div></div>
          <span class="s-val bad">−${naked.toFixed(0)}%</span></div>
        <div class="s-bar-row"><span class="s-lbl shield">방패ON</span>
          <div class="s-track"><div class="s-fill shield" style="width:${Math.min(shield,100)}%"></div></div>
          <span class="s-val">−${shield.toFixed(0)}%</span></div>
      </div>
    </div>`;
  }).join("");
}

// ── 웹 알림 (Web Notifications API) ────────────────────
let notifyEnabled = (typeof Notification !== "undefined" && Notification.permission === "granted");

async function requestNotify() {
  if (typeof Notification === "undefined") {
    alert("이 브라우저는 알림을 지원하지 않습니다."); return false;
  }
  if (Notification.permission === "granted") { notifyEnabled = true; updateNotifyBtn(); return true; }
  if (Notification.permission === "denied") {
    alert("알림이 차단됨. 브라우저 설정에서 이 사이트 알림을 허용하세요."); return false;
  }
  const p = await Notification.requestPermission();
  notifyEnabled = (p === "granted");
  updateNotifyBtn();
  if (notifyEnabled) {
    fireNotify("🔔 알림 켜짐", "방패 경보·리밸런싱 신호를 여기로 보냅니다.", "test");
    subscribeToPush();  // 앱 닫혀도 받도록 서버 푸시 구독
  }
  return notifyEnabled;
}

// ── Web Push 구독 (앱 종료 상태에서도 알림 수신) ──────────
function urlB64ToUint8(b64) {
  const pad = "=".repeat((4 - b64.length % 4) % 4);
  const s = (b64 + pad).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(s);
  return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}
async function subscribeToPush() {
  try {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;
    const reg = await navigator.serviceWorker.ready;
    const r = await fetchJSON("/api/push/key");
    if (!r.ok || !r.public_key) return;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlB64ToUint8(r.public_key),
      });
    }
    await fetch("/api/push/subscribe", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sub),
    });
  } catch (e) { console.warn("push subscribe fail", e); }
}

function fireNotify(title, body, tag) {
  if (!notifyEnabled || Notification.permission !== "granted") return;
  try {
    const n = new Notification(title, { body, tag, renotify: true,
      icon: "/static/icon-192.png", badge: "/static/icon-192.png" });
    n.onclick = () => { window.focus(); n.close(); };
  } catch (e) { console.warn("notify fail", e); }
}

function updateNotifyBtn() {
  const btn = document.getElementById("notify-btn");
  if (!btn) return;
  if (typeof Notification === "undefined") { btn.style.display = "none"; return; }
  if (Notification.permission === "granted") {
    btn.textContent = "🔔 알림 켜짐"; btn.classList.add("on");
  } else if (Notification.permission === "denied") {
    btn.textContent = "🔕 알림 차단됨"; btn.classList.remove("on");
  } else {
    btn.textContent = "🔔 알림 켜기"; btn.classList.remove("on");
  }
}

// 신호 변화 감지 → 알림 (localStorage로 직전 상태 추적)
function checkShieldNotify(shieldOk) {
  const cur = shieldOk ? "on" : "off";
  const last = localStorage.getItem("stoa_shield");
  if (last && last !== cur) {
    if (cur === "off")
      fireNotify("🛑 방패 경보 발령", "공격형(TQQQ) 매도 → 현금 대피. 다음 정규장에 실행하세요.", "shield");
    else
      fireNotify("🟢 방패 회복", "공격형(TQQQ) 재매수 신호. 다음 정규장에 실행하세요.", "shield");
  }
  localStorage.setItem("stoa_shield", cur);
}
function checkRebalNotify(g) {
  const last = localStorage.getItem("stoa_rebal");
  if (g.rebal_status === "red" && last !== "red") {
    const dir = g.rebal_move_krw > 0 ? "TQQQ 매수" : "TQQQ 매도";
    fireNotify("⚖️ 리밸런싱 필요",
      `${dir} ₩${Math.round(Math.abs(g.rebal_move_krw)).toLocaleString()} (목표 TQQQ ${g.target_tqqq_pct.toFixed(0)}%)`,
      "rebal");
  }
  localStorage.setItem("stoa_rebal", g.rebal_status);
}

const NAMES = {
  QQQM: { kor: "안정",   long: "Nasdaq-100 ETF",            cls: "sw-stable" },
  TQQQ: { kor: "공격",   long: "3× 레버리지 ETF",          cls: "sw-aggro"  },
};

function renderHoldings(positions) {
  const body = $("#holdings-body");
  if (!body) return;  // 미니멀 모드: 보유 종목 카드 없음
  body.innerHTML = "";
  for (const p of positions) {
    const meta = NAMES[p.symbol] || { kor: p.symbol, long: p.symbol, cls: "sw-stable" };
    const pct = (p.daily_profit_rate || 0) * 100;
    const pctCls = pct >= 0 ? "ok" : "bad";
    const row = document.createElement("div");
    row.className = "h-row";
    row.innerHTML = `
      <div class="h-name">
        <div class="swatch ${meta.cls}"></div>
        <div class="h-name-text">
          <div class="nm">${meta.kor} <span class="ticker">${p.symbol}</span></div>
          <div class="h-sub">${meta.long}</div>
        </div>
      </div>
      <div class="h-meta">
        <span class="m-num">${(p.quantity||0).toFixed(4)}</span><span class="m-unit">주</span>
        <span class="m-sep">·</span>
        <span class="m-num">${fmtUSD(p.current_price_usd)}</span>
      </div>
      <div class="h-mv">${fmtKRW(p.market_value||0)}</div>
      <div class="h-pct ${pctCls}">${fmtSign(pct, n=>n.toFixed(2)+"%")}</div>
      <div class="h-spark"><svg viewBox="0 0 100 32" preserveAspectRatio="none" data-spark="${p.symbol}"></svg></div>
    `;
    body.appendChild(row);
    drawSpark(row.querySelector(`svg[data-spark="${p.symbol}"]`), p.symbol, pct >= 0);
  }
}

// 30-day deterministic synthetic sparkline (seeded by symbol)
function drawSpark(svg, symbol, isUp) {
  const N = 30;
  let seed = 0;
  for (let i=0;i<symbol.length;i++) seed = (seed*31 + symbol.charCodeAt(i)) >>> 0;
  const rnd = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return (seed >>> 8) / 16777216; };
  const vols = symbol === "TQQQ" ? 1.8 : 0.7;
  const drift = isUp ? 0.18 : -0.18;
  const pts = [];
  let v = 50;
  for (let i = 0; i < N; i++) {
    v += (rnd() - 0.5) * 6 * vols + drift;
    pts.push(v);
  }
  // last value reflects today's direction
  if (isUp && pts[N-1] < pts[0]) pts[N-1] = pts[0] + 2;
  if (!isUp && pts[N-1] > pts[0]) pts[N-1] = pts[0] - 2;
  const min = Math.min(...pts), max = Math.max(...pts);
  const range = (max - min) || 1;
  const W = 100, H = 32, pad = 2;
  const xs = i => (i / (N-1)) * (W - 2*pad) + pad;
  const ys = v => H - pad - ((v - min) / range) * (H - 2*pad);
  let d = "";
  pts.forEach((p, i) => { d += (i === 0 ? "M" : "L") + xs(i).toFixed(1) + " " + ys(p).toFixed(1) + " "; });
  const color = isUp ? "var(--ok)" : "var(--bad)";
  const colorSoft = isUp ? "rgba(74,222,128,0.16)" : "rgba(248,113,113,0.16)";
  const area = d + `L${xs(N-1)} ${H} L${xs(0)} ${H} Z`;
  svg.innerHTML = `
    <path d="${area}" fill="${colorSoft}" />
    <path d="${d}" fill="none" stroke="${color}" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round" />
    <circle cx="${xs(N-1)}" cy="${ys(pts[N-1])}" r="2" fill="${color}" />
  `;
}

// ── /api/shield ─────────────────────────────────────
// ── 권장 행동: 방패 + 글라이드 통합 (단일 진실원) ──────
//  우선순위 1) 방패 경보 → 현금 대피  2) 리밸 필요 → 매수/매도  3) 유지
let _shieldOk = null;   // true / false / null(미수신)
let _glide = null;      // 최신 /api/glide 응답
function renderVerdict() {
  const verdict = $("#hero-verdict");
  if (!verdict) return;
  const vtxt = verdict.querySelector(".vtxt");
  const vhint = verdict.querySelector(".vhint");
  if (_shieldOk === null) return;  // 방패 미수신 — loadShield가 곧 다시 호출

  // 1순위: 방패 경보 (다른 모든 신호에 우선)
  if (_shieldOk === false) {
    vtxt.innerHTML = `공격형 전량 매도<br><span style="color:var(--bad)">현금 대피</span>`;
    vhint.textContent = "다음 정규장에서 TQQQ를 전량 매도해 현금으로 대피하세요.";
    return;
  }
  // 2순위: 방패 정상 + 글라이드 리밸 필요(빨강)
  if (_glide && _glide.rebal_status === "red") {
    const buy = _glide.rebal_move_krw > 0;
    const amt = "₩" + Math.round(Math.abs(_glide.rebal_move_krw)).toLocaleString();
    const dir = buy ? "TQQQ 매수" : "TQQQ 매도";
    vtxt.innerHTML = `${dir}<br><span style="color:var(--accent)">${amt}</span>`;
    vhint.textContent = `목표 ${_glide.target_qqqm_pct.toFixed(0)}:${_glide.target_tqqq_pct.toFixed(0)} 복귀 · 아래 주문대로 실행하세요.`;
    return;
  }
  // 3순위: 방패 정상 + 리밸 불필요 → 진짜 do nothing
  const tq = _glide ? _glide.target_tqqq_pct.toFixed(0) : "30";
  vtxt.innerHTML = `목표 비중 유지<br><span style="color:var(--text-dim)">보유 유지</span>`;
  vhint.textContent = `목표 TQQQ ${tq}%에 도달해 있습니다. 아무것도 하지 마세요.`;
}

async function loadShield() {
  const d = await fetchJSON(ENDPOINTS.shield);
  const hero = $("#hero");
  const headline = $("#hero-headline");
  const checks = $("#hero-checks");
  const verdict = $("#hero-verdict");
  const eyebrow = $("#hero-eyebrow");
  if (!d.ok) {
    hero.className = "hero state-bad";
    headline.innerHTML = `<span class="accent">데이터 오류</span>`;
    checks.innerHTML = `<div class="check bad"><span class="tick">!</span>${d.why || "shield 데이터 없음"}</div>`;
    verdict.querySelector(".vtxt").textContent = "확인 필요";
    return null;
  }

  const ok = !!d.shield_ok;
  checkShieldNotify(ok);
  const aboveOk = d.qqq_above_pct >= 0;
  const vixOk = d.vix_ratio < 1.0;

  hero.className = "hero " + (ok ? "" : "state-bad");
  eyebrow.textContent = ok ? "자동 안전장치" : "자동 안전장치 · 경보";

  if (ok) {
    headline.innerHTML = `<span class="accent">정상 작동 중</span>`;
  } else {
    headline.innerHTML = `<span class="accent">경보 발령</span>`;
  }

  checks.innerHTML = `
    <div class="check ${aboveOk ? "" : "bad"}">
      <span class="tick">${aboveOk ? "✓" : "!"}</span>
      나스닥 ${aboveOk ? "상승 추세" : "하락 추세"}
    </div>
    <div class="check ${vixOk ? "" : "bad"}">
      <span class="tick">${vixOk ? "✓" : "!"}</span>
      공포지수 ${vixOk ? "안정" : "불안"}
    </div>
  `;

  _shieldOk = ok;
  renderVerdict();

  // detail card 1: QQQ vs SMA200 (미니멀 모드에선 카드 제거됨, 가드)
  const qqqPct = $("#qqq-pct");
  if (qqqPct) {
    qqqPct.textContent = (d.qqq_above_pct >= 0 ? "+" : "") + d.qqq_above_pct.toFixed(2) + "%";
    qqqPct.className = "big-stat " + (aboveOk ? "ok" : "bad");
    $("#qqq-sub").innerHTML = `종가 <strong style="color:var(--text-2)">$${d.qqq_close.toFixed(2)}</strong>  ·  200일선 <strong style="color:var(--text-2)">$${d.qqq_sma200.toFixed(2)}</strong>`;
    drawQqqViz(d.qqq_close, d.qqq_sma200);
  }

  // detail card 2: VIX ratio gauge (미니멀 모드 가드)
  const vixVal = $("#vix-val");
  if (vixVal) {
    vixVal.textContent = d.vix_ratio.toFixed(3);
    vixVal.className = "big-stat " + (vixOk ? "ok" : "bad");
    $("#vix-sub").innerHTML = `VIX <strong style="color:var(--text-2)">${d.vix.toFixed(2)}</strong>  ·  VIX3M <strong style="color:var(--text-2)">${d.vix3m.toFixed(2)}</strong>  ·  기준 <span style="color:var(--text-mute)">1.000</span>`;
    const pct = clamp(((d.vix_ratio - 0.6) / (1.2 - 0.6)) * 100, 1, 99);
    $("#vix-needle").style.left = pct + "%";
  }

  return d;
}


function drawQqqViz(close, sma) {
  const svg = $("#qqq-viz");
  if (!svg) return;  // 미니멀 모드 가드
  // synthesize a 120-day path that ends at `close`, with sma drifting up
  const N = 120;
  let seed = 7919;
  const rnd = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return (seed >>> 8) / 16777216; };
  const series = [];
  let p = sma * 0.92;
  for (let i = 0; i < N; i++) {
    const trend = (close - sma*0.92) / N;
    p += trend + (rnd() - 0.48) * sma * 0.012;
    series.push(p);
  }
  // scale endpoint to match close
  const scale = close / series[N-1];
  for (let i = 0; i < N; i++) series[i] *= scale;
  const smaLine = series.map((_, i) => sma * (0.95 + i * 0.0005));
  const all = [...series, ...smaLine];
  const min = Math.min(...all) * 0.985, max = Math.max(...all) * 1.005;
  const W = 100, H = 40;
  const xs = i => (i / (N-1)) * W;
  const ys = v => H - ((v - min) / (max - min)) * H;
  let pd = "", sd = "";
  series.forEach((v, i) => { pd += (i === 0 ? "M" : "L") + xs(i).toFixed(2) + " " + ys(v).toFixed(2) + " "; });
  smaLine.forEach((v, i) => { sd += (i === 0 ? "M" : "L") + xs(i).toFixed(2) + " " + ys(v).toFixed(2) + " "; });
  const lastY = ys(series[N-1]);
  const smaY  = ys(smaLine[N-1]);
  svg.innerHTML = `
    <defs>
      <linearGradient id="qg" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0%" stop-color="rgba(74,222,128,0.18)"/>
        <stop offset="100%" stop-color="rgba(74,222,128,0)"/>
      </linearGradient>
    </defs>
    <path d="${pd} L${xs(N-1)} ${H} L${xs(0)} ${H} Z" fill="url(#qg)"/>
    <path d="${sd}" fill="none" stroke="rgba(178,186,198,0.7)" stroke-width="0.6" stroke-dasharray="1.5 1.5"/>
    <path d="${pd}" fill="none" stroke="var(--ok)" stroke-width="0.9" stroke-linejoin="round"/>
    <circle cx="${xs(N-1)}" cy="${lastY}" r="1.4" fill="var(--ok)"/>
    <text x="${xs(N-1) - 1}" y="${lastY - 2}" fill="var(--ok)" font-size="3" text-anchor="end" font-family="ui-monospace">종가</text>
    <text x="0" y="${smaY - 1.2}" fill="rgba(178,186,198,0.95)" font-size="2.8" font-family="ui-monospace">200일선</text>
  `;
}

// ── /api/prices → main chart (Apple Stocks 인라인 툴팁) ─────────
function applyChartSummary(s) {
  if (!s) return;
  const lab = $("#chart-cursor-date");
  if (lab) lab.textContent = s.label || "";
  const b = $("#sum-blend");  if (b) b.textContent = s.blend;
  const q = $("#sum-qqq");    if (q) q.textContent = s.qqq;
  const t = $("#sum-tqqq");   if (t) t.textContent = s.tqqq;
}
let equityChart = null;
let chartDefaultSummary = null;   // 마우스 떠나면 복원
async function loadPrices() {
  const d = await fetchJSON(ENDPOINTS.prices);
  if (!d.ok) { console.warn("prices err", d.why); return; }
  const ctx = $("#chart-equity").getContext("2d");
  if (equityChart) equityChart.destroy();

  // pull final returns for default summary
  const lastQ = d.qqq[d.qqq.length - 1];
  const lastT = d.tqqq[d.tqqq.length - 1];
  const lastB = d.glide_path[d.glide_path.length - 1];
  const fmtP = v => (v >= 100 ? "+" : "") + (v - 100).toFixed(1) + "%";
  chartDefaultSummary = {
    label: "최근 12개월",
    blend: fmtP(lastB), qqq: fmtP(lastQ), tqqq: fmtP(lastT),
  };
  applyChartSummary(chartDefaultSummary);

  const css = getComputedStyle(document.body);
  const COL = {
    text:    css.getPropertyValue("--text-dim").trim() || "#8E95A2",
    mute:    css.getPropertyValue("--text-mute").trim() || "#5C636F",
    accent:  css.getPropertyValue("--accent").trim() || "#A7B5FF",
    violet:  css.getPropertyValue("--violet").trim() || "#C4B5FD",
  };

  // 크로스헤어 라인 플러그인 (수직선만, 박스 없음)
  const crosshair = {
    id: "crosshair",
    afterDatasetsDraw(chart) {
      const active = chart.tooltip?._active;
      if (!active || !active.length) return;
      const x = active[0].element.x;
      const { top, bottom } = chart.scales.y;
      const c = chart.ctx;
      c.save();
      c.beginPath();
      c.moveTo(x, top); c.lineTo(x, bottom);
      c.lineWidth = 1;
      c.strokeStyle = "rgba(167,181,255,0.45)";
      c.setLineDash([2, 3]);
      c.stroke();
      c.restore();
    }
  };

  equityChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: d.dates,
      datasets: [
        { label: "qqq",   data: d.qqq,              borderColor: COL.text,   borderWidth: 1,   pointRadius: 0, tension: 0.05, borderDash: [3, 3] },
        { label: "tqqq",  data: d.tqqq,             borderColor: COL.violet, borderWidth: 1.2, pointRadius: 0, tension: 0.05 },
        { label: "blend", data: d.glide_path, borderColor: COL.accent, borderWidth: 2.4, pointRadius: 0, tension: 0.05 },
      ]
    },
    plugins: [crosshair],
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      events: ["mousemove", "mouseout", "touchstart", "touchmove"],
      plugins: {
        legend: { display: false },
        tooltip: {
          enabled: false,    // 박스 비활성
          external(ctx) {
            const tt = ctx.tooltip;
            if (!tt || tt.opacity === 0) {
              applyChartSummary(chartDefaultSummary);
              return;
            }
            const date = tt.title?.[0];
            const items = {};
            (tt.dataPoints || []).forEach(p => {
              items[p.dataset.label] = fmtP(p.raw);
            });
            applyChartSummary({
              label: date || "",
              blend: items.blend || "—",
              qqq: items.qqq || "—",
              tqqq: items.tqqq || "—",
            });
          },
        },
      },
      scales: {
        x: {
          grid: { display: false, drawBorder: false },
          ticks: {
            color: COL.mute, font: { family: "JetBrains Mono", size: 11 },
            maxTicksLimit: 6, autoSkipPadding: 20,
            callback: function(val) {
              const date = this.getLabelForValue(val);
              return date ? date.slice(2, 7).replace("-", "/") : "";
            }
          },
          border: { display: false }
        },
        y: {
          grid: {
            color: "rgba(35,40,47,0.55)",
            drawBorder: false,
            drawTicks: false,
          },
          ticks: {
            color: COL.mute, font: { family: "JetBrains Mono", size: 11 },
            padding: 8, maxTicksLimit: 5,
            callback: v => v === 100 ? "100" : (v > 100 ? "+" + (v-100) + "%" : (v-100) + "%")
          },
          border: { display: false }
        }
      }
    }
  });
}

// ── /api/journal → CEO-readable cards ──────────────
async function loadJournal() {
  const el = $("#journal-list");
  if (!el) return;  // 미니멀 모드: 일지 카드 제거됨
  const d = await fetchJSON(ENDPOINTS.journal);
  if (!d.ok) {
    el.innerHTML = `<div class="j-entry"><div class="j-body" style="color:var(--bad)">${d.why || "저널 읽기 실패"}</div></div>`;
    return;
  }
  el.innerHTML = "";
  const entries = parseJournal(d.lines || []);
  for (const e of entries) el.appendChild(renderJournalEntry(e));
}

// ── Translation tables ─────────────────────────────
const KIND_MAP = {
  PX:  { kor: "단기 가격",  cls: "kind-px"  },
  MAC: { kor: "거시 분석",  cls: "kind-mac" },
};
function parseStatus(raw) {
  const r = (raw || "").trim();
  if (r === "_대기_" || r === "대기" || /대기/.test(r)) return { kor: "관찰 중", cls: "status-wait" };
  if (/적중|hit/i.test(r))                              return { kor: "적중",    cls: "status-hit"  };
  if (/실패|miss/i.test(r))                             return { kor: "실패",    cls: "status-miss" };
  if (!r || r === "—" || r === "-")                     return { kor: "—",      cls: "status-none" };
  return { kor: r.replace(/[_*]/g, ""), cls: "status-none" };
}

function parseJournal(lines) {
  const blocks = { actions: [], transitions: [], predictions: [], notes: [], rationale: [] };
  let mode = "predictions";
  let lastTransition = null;
  let sawTableHeader = false;
  let lastWasHr = false;

  for (const raw of lines) {
    const line = (raw || "").trim();
    if (line === "" || line === "---") { lastWasHr = (line === "---"); continue; }

    // # h1 → predictions section start
    if (/^#\s/.test(line)) { mode = "predictions"; continue; }

    // ### transition
    const tm = line.match(/^###\s+(\S+)\s+(\d{4}-\d{2}-\d{2})\s*[—–-]\s*(\S+)\s*$/);
    if (tm) {
      lastTransition = { actor: tm[1], date: tm[2], transition: tm[3], items: [] };
      blocks.transitions.push(lastTransition);
      mode = "transition";
      continue;
    }

    // blockquote → action callout
    if (line.startsWith(">")) {
      blocks.actions.push(line.replace(/^>\s*/, ""));
      continue;
    }

    // table row
    if (line.startsWith("|")) {
      const cells = line.split("|").map(s => s.trim()).filter(c => c.length > 0);
      // header / divider
      if (cells[0] === "ID" || /^-+$/.test(cells[0]) || cells.every(c => /^[-:]+$/.test(c))) {
        sawTableHeader = true;
        continue;
      }
      if (sawTableHeader && cells.length >= 4) {
        const [id, kind, claim, prob, result, brier] = cells;
        blocks.predictions.push({ id, kind, claim, prob, result: result || "—", brier: brier || "—" });
      }
      continue;
    }

    // bullet under transition
    if (line.startsWith("-") && mode === "transition" && lastTransition) {
      lastTransition.items.push(line.replace(/^-\s+/, ""));
      continue;
    }

    // prose
    if (mode === "transition") {
      // following transition - skip unless it's something structural
      blocks.notes.push(line);
    } else {
      blocks.rationale.push(line);
    }
  }

  // Order: action (top) → newest transition → predictions → rationale
  const out = [];
  if (blocks.actions.length)      out.push({ type: "action", lines: blocks.actions });
  for (const t of blocks.transitions.slice().reverse()) out.push({ type: "transition", ...t });
  if (blocks.predictions.length)  out.push({ type: "predictions", items: blocks.predictions });
  if (blocks.rationale.length)    out.push({ type: "notes", lines: blocks.rationale });
  return out;
}

function parseTransitionItems(items) {
  const kpis = [];
  for (const raw of items) {
    const t = raw.trim();
    // QQQ $701.53 vs SMA200 $611.30 (+14.76%)
    let m = t.match(/QQQ\s+\$?([\d.,]+)\s+vs\s+SMA200\s+\$?([\d.,]+)\s*\(([+\-]?[\d.]+%)\)/i);
    if (m) {
      kpis.push({
        label: "나스닥 vs 200일선",
        value: `+${m[3].replace(/^\+/, "")}`.replace("++", "+"),
        sub: `종가 $${m[1]}  ·  200일선 $${m[2]}`,
        cls: "ok",
      }); continue;
    }
    // VIX/VIX3M 0.859
    m = t.match(/VIX[\/／]VIX3M\s+([\d.]+)/i);
    if (m) {
      const v = parseFloat(m[1]);
      kpis.push({
        label: "공포지수 비율",
        value: v.toFixed(3),
        sub: v < 1 ? "기준 1.0 미만 · 안정" : "기준 1.0 이상 · 불안",
        cls: v < 1 ? "ok" : "bad",
      }); continue;
    }
    // 액션: ...
    m = t.match(/^액션\s*[:：]\s*(.+)/);
    if (m) {
      const body = m[1];
      const paren = body.match(/^(.+?)\s*\((.+)\)\s*$/);
      kpis.push({
        label: "실행 액션",
        value: paren ? paren[1].trim() : body.trim(),
        sub: paren ? paren[2].trim() : null,
        cls: "accent",
      }); continue;
    }
    // 카테고리: MAC. 거래 트리거 아님 — ...
    m = t.match(/^카테고리\s*[:：]\s*(\w+)\.?\s*(.*)/);
    if (m) {
      const k = KIND_MAP[m[1]] || { kor: m[1] };
      kpis.push({
        label: "분류",
        value: k.kor,
        sub: /거래 트리거 아님/.test(m[2]) ? "거래 트리거 아님 · 본인이 직접 실행" : (m[2] || null),
        cls: "neutral",
      }); continue;
    }
    // fallback: render as prose under kpis
    kpis.push({ label: "메모", value: t, cls: "neutral", isProse: true });
  }
  return kpis;
}

function renderJournalEntry(e) {
  const card = document.createElement("div");

  if (e.type === "action") {
    const body = e.lines.join(" ");
    const noneMatch = /행동\s*함의\s*[:：]\s*\*\*?없음\.?\*\*?/i.test(body);
    const text = body.replace(/^행동\s*함의\s*[:：]\s*/i, "").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    card.className = "j-entry j-action " + (noneMatch ? "do-nothing" : "do-something");
    card.innerHTML = `
      <div class="j-act-eyebrow">${noneMatch ? "오늘의 권장 행동" : "지금 해야 할 일"}</div>
      <div class="j-act-body">${text}</div>
      ${noneMatch ? `<div class="j-act-hint">두 안전장치 모두 정상이므로, 추가 매매·매도 없음.</div>` : ""}
    `;
    return card;
  }

  if (e.type === "transition") {
    const isUp = /cash[\s→\->]+asset/i.test(e.transition);
    const tagCls = isUp ? "cash2asset" : "asset2cash";
    const headline = isUp ? "공격형 재매수 신호" : "현금 대피 신호";
    const arrow = isUp ? "현금 → 공격형" : "공격형 → 현금";
    const kpis = parseTransitionItems(e.items);
    const tiles = kpis.filter(k => !k.isProse);
    const proseItems = kpis.filter(k => k.isProse);

    card.className = "j-entry j-trans";
    card.innerHTML = `
      <div class="j-head">
        <div class="j-date">${e.date}</div>
        <div class="j-title">${headline}</div>
        <div class="j-tag ${tagCls}">${arrow}</div>
      </div>
      <div class="j-kpis">
        ${tiles.map(k => `
          <div class="j-kpi ${k.cls||""}">
            <div class="j-kpi-label">${k.label}</div>
            <div class="j-kpi-value">${escapeHtml(k.value)}</div>
            ${k.sub ? `<div class="j-kpi-sub">${escapeHtml(k.sub)}</div>` : ""}
          </div>
        `).join("")}
      </div>
      ${proseItems.length ? `<div class="j-trans-prose">${proseItems.map(p => `<div>${escapeHtml(p.value)}</div>`).join("")}</div>` : ""}
    `;
    return card;
  }

  if (e.type === "predictions") {
    const items = e.items.map(p => {
      const kind = KIND_MAP[p.kind] || { kor: p.kind, cls: "kind-other" };
      const status = parseStatus(p.result);
      const probF = parseFloat(p.prob);
      const probStr = !isNaN(probF) ? Math.round(probF * 100) + "%" : p.prob;
      return `
        <div class="j-pred">
          <div class="j-pred-claim">${escapeHtml(p.claim)}</div>
          <div class="j-pred-meta">
            <span class="j-pred-kind ${kind.cls}">${kind.kor}</span>
            <span class="j-pred-prob">확률 <strong>${probStr}</strong></span>
            <span class="j-pred-status ${status.cls}">${status.kor}</span>
          </div>
        </div>
      `;
    }).join("");
    card.className = "j-entry j-preds";
    card.innerHTML = `
      <div class="j-head">
        <div class="j-title">예측 추적</div>
        <div class="j-tag">${e.items.length}건 · Brier 누적</div>
      </div>
      <div class="j-help">짧게: AI가 시장에 대해 미리 적어둔 예측들. <strong>확률</strong>이 얼마나 잘 맞았는지 시간이 지나면 점수(Brier)가 매겨집니다.</div>
      <div class="j-preds-list">${items}</div>
    `;
    return card;
  }

  if (e.type === "notes") {
    const noteHtml = e.lines.map(l => `<p>${formatInline(l)}</p>`).join("");
    card.className = "j-entry j-notes";
    card.innerHTML = `
      <details>
        <summary>분석 메모 더 보기 <span class="j-notes-count">${e.lines.length}줄</span></summary>
        <div class="j-notes-body">${noteHtml}</div>
      </details>
    `;
    return card;
  }
  return card;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;" }[c]));
}
function formatInline(s) {
  let out = escapeHtml(s);
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong style="color:var(--text)">$1</strong>');
  out = out.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  // money / numbers monospace highlight
  out = out.replace(/(\$[0-9][0-9,.]*|\d+\.\d{2,3}%?)/g, '<span class="mono">$1</span>');
  return out;
}

// ── /api/bot_status → timeline ─────────────────────
async function loadBot() {
  const el = $("#timeline");
  if (!el) return;  // 미니멀 모드: 타임라인 카드 제거됨
  const d = await fetchJSON(ENDPOINTS.bot);
  if (!d.ok) {
    el.innerHTML = `<div class="tl-item bad"><div class="tl-title">상태 파일 없음</div><div class="tl-sub">${d.why||""}</div></div>`;
    return;
  }
  const s = d.state || d;
  const stateStr = s.new_state || s.state || "—";
  const stateOk = stateStr === "asset";

  // events: historical transitions only (현재 상태는 히어로 카드가 담당)
  const events = [];
  const lastTrans = d.last_transition_date || s.last_transition_date;
  if (lastTrans) {
    events.push({
      when: lastTrans,
      title: stateOk ? "현금 → 공격형 재매수" : "공격형 → 현금 대피",
      sub: stateOk ? "두 조건 모두 회복 — 위성 TQQQ 재진입" : "방패 OFF — 위성 TQQQ 청산",
      state: stateOk ? "ok" : "bad",
    });
  } else {
    // demo timeline
    events.push({ when: "2026-05-19", title: "현금 → 공격형 재매수", sub: "QQQ +14.76% vs SMA200 · VIX비 0.859",  state: "ok" });
    events.push({ when: "2026-04-12", title: "공격형 → 현금 대피",  sub: "VIX비 1.18 — 단기 공포 급등",            state: "bad"  });
    events.push({ when: "2026-04-03", title: "현금 → 공격형 재매수", sub: "조건 회복 · 데이터 안정",                state: "ok"   });
    events.push({ when: "2026-01-22", title: "주의 신호",            sub: "SMA200 근접 (-0.4%) · 경계만",          state: "warn" });
    events.push({ when: "2025-09-10", title: "정상 작동 확인",       sub: "분기 점검 통과",                          state: "ok"   });
  }

  el.innerHTML = events.map(e => `
    <div class="tl-item ${e.state} ${e.now?"now":""}">
      <div class="tl-date">${e.when}</div>
      <div class="tl-title">${e.title}</div>
      <div class="tl-sub">${e.sub}</div>
    </div>
  `).join("");
}

// ── countdown to next quarterly review ─────────────
function updateCountdown() {
  const now = new Date();
  // 분기 말일: 3/31, 6/30, 9/30, 12/31
  const Q = [
    new Date(now.getFullYear(), 2, 31),
    new Date(now.getFullYear(), 5, 30),
    new Date(now.getFullYear(), 8, 30),
    new Date(now.getFullYear(), 11, 31),
  ];
  let next = Q.find(d => d >= now);
  if (!next) next = new Date(now.getFullYear()+1, 2, 31);

  const days = Math.ceil((next - now) / (1000*60*60*24));
  $("#count-days").textContent = days;
  $("#count-date").textContent =
    next.toLocaleDateString("ko-KR", { year:"numeric", month:"long", day:"numeric" });

  // progress (from previous quarter end → next)
  const prevIdx = Q.indexOf(next) - 1;
  const prev = prevIdx >= 0 ? Q[prevIdx] : new Date(now.getFullYear()-1, 11, 31);
  const span = next - prev;
  const elapsed = now - prev;
  const pct = clamp((elapsed / span) * 100, 2, 98);
  $("#count-fill").style.width = pct + "%";
}

// ── last-update header ─────────────────────────────
function setLastUpdate(label = null) {
  const el = $("#last-update");
  if (label) {
    el.textContent = label;
    return;
  }
  el.textContent = new Date().toLocaleTimeString("ko-KR", { hour:"2-digit", minute:"2-digit" }) + " 갱신";
}

// ── refresh orchestrator ───────────────────────────
let refreshing = false;
async function refreshAll(full = false) {
  if (refreshing) return;
  refreshing = true;
  setLastUpdate("갱신 중…");
  try {
    updateCountdown();
    // 미니멀 모드: 일지·봇이력 제거 (Hero에서 방패 상태로 통합)
    await Promise.all([loadShield(), loadPortfolio(), loadGlide()]);
    if (full) { await loadPrices(); loadStress(); }
    setLastUpdate();
  } catch (e) {
    setLastUpdate("오류: " + e.toString());
  } finally {
    refreshing = false;
  }
}

// ── 데이터 종류별 자동 갱신 주기 (LIVE 느낌) ─────────────
// tossctl 보유 자산:  60초  (가장 빈번하게 변함)
// shield (yfinance):  5분  (일봉 + ratio, yf rate-limit 회피)
// 가격 차트:          1시간 (1년 시계열, 분 단위 변화 무의미)
// 카운트다운:         1분
async function refreshPortfolioOnly() {
  if (refreshing) return;
  setLastUpdate("갱신 중…");
  try {
    await loadPortfolio();
    await loadGlide();
    setLastUpdate();
  } catch (e) {
    setLastUpdate("오류");
  }
}

window.addEventListener("load", () => {
  // 서비스워커 (PWA 설치)
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(e => console.warn("sw", e));
    // 이미 알림 허용 상태면 푸시 구독 갱신 (재방문/구독 만료 대비)
    if (notifyEnabled) subscribeToPush();
  }
  setupDcaInput();
  setupSettings();
  loadMe();
  // 알림 버튼
  const nb = document.getElementById("notify-btn");
  if (nb) { updateNotifyBtn(); nb.addEventListener("click", requestNotify); }
  refreshAll(true);
  // 토스 보유 자산 — 60초 (LIVE 느낌)
  setInterval(refreshPortfolioOnly,    60 * 1000);
  // shield — 5분 (yfinance 일봉, 분 단위 변화 X)
  setInterval(() => loadShield(),      5 * 60 * 1000);
  // 가격 차트 — 1시간
  setInterval(() => loadPrices(),      60 * 60 * 1000);
  // 카운트다운 — 1분
  setInterval(updateCountdown,         60 * 1000);
  // 상대시각 표시("N분 전") — 30초마다 재렌더, fetch 없음
  setInterval(tickFreshness,           30 * 1000);
});

// 📱 모바일 브라우저 백그라운드 → setInterval throttle 회피
// 탭 다시 활성화·창 포커스·BFCache 복원 시 즉시 강제 갱신
function onResume() {
  // 마지막 데이터가 2분 이상 오래됐으면 갱신
  if (!lastDataAt || (Date.now() - lastDataAt.getTime()) > 120 * 1000) {
    refreshAll(false);
  } else {
    tickFreshness();
  }
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") onResume();
});
window.addEventListener("focus", onResume);
window.addEventListener("pageshow", e => { if (e.persisted) onResume(); });

document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
  if (e.key === "r" || e.key === "R") refreshAll(true);
});

