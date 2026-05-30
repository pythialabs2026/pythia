/* ============================================================
   Stoa — Tweaks panel (vanilla, follows host protocol)
   ============================================================ */
(function () {
  const DEFAULTS = (window.__TWEAK_DEFAULTS__ && typeof window.__TWEAK_DEFAULTS__ === "object")
    ? window.__TWEAK_DEFAULTS__
    : { mood: "default", numfont: "serif", accent: "periwinkle" };

  let state = { ...DEFAULTS };

  // ── apply ───────────────────────────────────────────
  function applyTweaks() {
    const b = document.body;
    b.dataset.mood    = state.mood;
    b.dataset.numfont = state.numfont;
    b.dataset.accent  = state.accent;
    // chart line colors are baked at draw — re-render
    if (window.loadPrices) setTimeout(window.loadPrices, 30);
  }
  applyTweaks();

  // ── build panel ─────────────────────────────────────
  const panel = document.createElement("aside");
  panel.id = "tweaks-panel";
  panel.className = "tweaks";
  panel.hidden = true;
  panel.innerHTML = `
    <div class="tw-head">
      <span class="tw-title">Tweaks</span>
      <button class="tw-close" aria-label="닫기">×</button>
    </div>
    <div class="tw-body">

      <section class="tw-section">
        <div class="tw-lbl">분위기</div>
        <div class="tw-help">정보 밀도와 장식의 양을 통째로 바꿉니다.</div>
        <div class="tw-seg" data-key="mood">
          <button data-v="zen">Zen</button>
          <button data-v="default">Standard</button>
          <button data-v="pro">Pro</button>
        </div>
      </section>

      <section class="tw-section">
        <div class="tw-lbl">숫자 표현</div>
        <div class="tw-help">대시보드 강조 숫자의 폰트 성격.</div>
        <div class="tw-seg" data-key="numfont">
          <button data-v="serif">Serif</button>
          <button data-v="mono">Mono</button>
          <button data-v="sans">Sans</button>
        </div>
      </section>

      <section class="tw-section">
        <div class="tw-lbl">액센트 색</div>
        <div class="tw-help">차트·게이지·핀·바 모든 강조 색.</div>
        <div class="tw-swatches" data-key="accent">
          <button data-v="periwinkle" style="--c:#A7B5FF" aria-label="Periwinkle"></button>
          <button data-v="emerald"    style="--c:#5EEAD4" aria-label="Emerald"></button>
          <button data-v="amber"      style="--c:#FBBF24" aria-label="Amber"></button>
          <button data-v="slate"      style="--c:#CBD5E1" aria-label="Slate"></button>
        </div>
      </section>

    </div>
  `;
  document.body.appendChild(panel);

  function syncActive() {
    panel.querySelectorAll("[data-key]").forEach(group => {
      const key = group.dataset.key;
      group.querySelectorAll("button[data-v]").forEach(b => {
        b.classList.toggle("active", b.dataset.v === state[key]);
      });
    });
  }
  syncActive();

  // ── interactions ───────────────────────────────────
  panel.addEventListener("click", e => {
    if (e.target.closest(".tw-close")) { dismiss(); return; }
    const b = e.target.closest("button[data-v]");
    if (!b) return;
    const group = b.closest("[data-key]");
    if (!group) return;
    const key = group.dataset.key;
    const v   = b.dataset.v;
    state = { ...state, [key]: v };
    applyTweaks();
    syncActive();
    try {
      window.parent && window.parent.postMessage(
        { type: "__edit_mode_set_keys", edits: { [key]: v } }, "*"
      );
    } catch (_) {}
  });

  // ── open / close ───────────────────────────────────
  let openState = false;
  function open()  {
    panel.hidden = false;
    requestAnimationFrame(() => panel.classList.add("open"));
    openState = true;
  }
  function close() {
    panel.classList.remove("open");
    setTimeout(() => { panel.hidden = true; }, 240);
    openState = false;
  }
  function dismiss() {
    close();
    try {
      window.parent && window.parent.postMessage({ type: "__edit_mode_dismissed" }, "*");
    } catch (_) {}
  }

  // ── host protocol: listener FIRST, then announce ──
  window.addEventListener("message", e => {
    const d = e.data || {};
    if (d.type === "__activate_edit_mode")   open();
    if (d.type === "__deactivate_edit_mode") close();
  });
  try {
    window.parent && window.parent.postMessage({ type: "__edit_mode_available" }, "*");
  } catch (_) {}
})();
