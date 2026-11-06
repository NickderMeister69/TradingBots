"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = (d) => new Intl.NumberFormat("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d });
const eur = (x, d = 2) => (x == null || isNaN(x) ? "–" : nf(d).format(x) + " €");
const z = (x, d) => (Math.abs(x) < 0.5 * Math.pow(10, -d) ? 0 : x);
const eurS = (x, d = 2) => (x == null || isNaN(x) ? "–" : (z(x, d) > 0 ? "+" : "") + nf(d).format(z(x, d)) + " €");
const pct = (x, d = 1) => (x == null || isNaN(x) ? "–" : (z(x, d) > 0 ? "+" : "") + nf(d).format(z(x, d)) + " %");
const cls = (x) => (x > 0.005 ? "pos" : x < -0.005 ? "neg" : "");
const usd = (x) => {
  if (x == null) return "–";
  const a = Math.abs(x);
  if (a >= 1e6) return nf(2).format(x / 1e6) + " Mio $";
  if (a >= 1e3) return nf(0).format(x / 1e3) + " Tsd $";
  return nf(0).format(x) + " $";
};
const price = (p) => {
  if (p == null) return "–";
  const a = Math.abs(p);
  const d = a >= 1000 ? 1 : a >= 10 ? 2 : a >= 1 ? 4 : a >= 0.01 ? 5 : 7;
  return nf(d).format(p);
};
const tfmt = (ts, withDate) => {
  if (!ts) return "–";
  const d = new Date(ts * 1000);
  const t = d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: withDate ? undefined : "2-digit" });
  return withDate ? d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" }) + " " + t : t;
};
const ago = (ts) => {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return "gerade eben";
  if (s < 3600) return `vor ${Math.round(s / 60)} min`;
  if (s < 172800) return `vor ${Math.round(s / 3600)} h`;
  return `vor ${Math.round(s / 86400)} Tagen`;
};
const dur = (s) => {
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86400 * 2) return `${nf(1).format(s / 3600)} h`;
  return `${nf(1).format(s / 86400)} Tage`;
};

let S = null;               // letzter Zustand
let range = "all";
let traders = [];
const openTraders = new Set();
const traderHist = {};
let settingsFilled = false;
let showAllCoins = false, showAllTraders = false;

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 3200);
}

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error((await r.text()) || r.status);
  return r.json();
}

// ------------------------------------------------------------------ Chart
function drawLine(canvas, pts, opt = {}) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const g = canvas.getContext("2d");
  g.scale(dpr, dpr);
  g.clearRect(0, 0, w, h);
  const css = getComputedStyle(document.documentElement);
  const muted = css.getPropertyValue("--muted"), line = css.getPropertyValue("--line");
  if (!pts || pts.length < 2) {
    g.fillStyle = muted; g.font = "13px sans-serif";
    g.fillText(opt.empty || "Noch zu wenig Daten – die Kurve wächst alle 5 Minuten.", 8, h / 2);
    return;
  }
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  let lo = Math.min(...ys, opt.base ?? Infinity), hi = Math.max(...ys, opt.base ?? -Infinity);
  if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.12; lo -= pad; hi += pad;
  const L = opt.axis === false ? 4 : 64, R = 8, T = 10, B = opt.axis === false ? 4 : 22;
  const X = (x) => L + ((x - xs[0]) / (xs[xs.length - 1] - xs[0] || 1)) * (w - L - R);
  const Y = (y) => T + (1 - (y - lo) / (hi - lo)) * (h - T - B);
  if (opt.axis !== false) {
    g.strokeStyle = line; g.lineWidth = 1; g.fillStyle = muted; g.font = "11px " + css.getPropertyValue("--mono");
    for (let i = 0; i <= 3; i++) {
      const v = lo + ((hi - lo) * i) / 3, y = Y(v);
      g.beginPath(); g.moveTo(L, y); g.lineTo(w - R, y); g.stroke();
      g.fillText(opt.fmt ? opt.fmt(v) : nf(0).format(v), 4, y + 4);
    }
    const span = xs[xs.length - 1] - xs[0];
    for (let i = 0; i <= 3; i++) {
      const x = xs[0] + (span * i) / 3;
      const d = new Date((opt.ms ? x : x * 1000));
      const lab = span > 86400 * (opt.ms ? 1000 : 1) * 1.5 ? d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" }) : d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
      g.fillText(lab, Math.min(w - 40, Math.max(L, X(x) - 16)), h - 6);
    }
  }
  if (opt.base != null) {
    g.setLineDash([4, 4]); g.strokeStyle = muted; g.beginPath(); g.moveTo(L, Y(opt.base)); g.lineTo(w - R, Y(opt.base)); g.stroke(); g.setLineDash([]);
  }
  const up = ys[ys.length - 1] >= (opt.base ?? ys[0]);
  const col = css.getPropertyValue(up ? "--green" : "--red");
  g.beginPath();
  pts.forEach((p, i) => (i ? g.lineTo(X(p[0]), Y(p[1])) : g.moveTo(X(p[0]), Y(p[1]))));
  g.strokeStyle = col; g.lineWidth = 2; g.stroke();
  g.lineTo(X(xs[xs.length - 1]), h - B); g.lineTo(X(xs[0]), h - B); g.closePath();
  const grd = g.createLinearGradient(0, T, 0, h - B);
  grd.addColorStop(0, up ? "rgba(47,179,128,.22)" : "rgba(226,87,76,.22)"); grd.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = grd; g.fill();
}

async function loadChart() {
  try {
    const d = await api(`/api/equity?range=${range}`);
    drawLine($("chart"), d.points, { base: S?.account?.start ?? 5000, fmt: (v) => nf(0).format(v) + " €" });
  } catch (e) { /* nächster Versuch */ }
}

// ------------------------------------------------------------------ Render
function renderHeader(s) {
  const c = $("pillConn");
  c.className = "pill " + (s.conn.ok ? "pill-ok" : "pill-bad");
  c.textContent = s.conn.ok ? "Verbunden · Hyperliquid (nur lesen)" : s.conn.msg;
  const st = $("pillStatus");
  const map = { running: ["pill-ok", "läuft"], paused: ["pill-warn", "pausiert"], stopped: ["pill-bad", "gestoppt (Not-Aus)"] };
  st.className = "pill " + map[s.status][0];
  st.textContent = map[s.status][1];
  $("pillTraders").textContent = s.refreshing ? "Profi-Auswahl wird aktualisiert …" : s.traders_count ? `kopiert ${s.traders_count} Profis` : "Profis werden gesucht …";
  const b = $("btnPause");
  b.textContent = s.status === "running" ? "Pause" : "Weiter";
  b.dataset.action = s.status === "running" ? "pause" : "resume";
  $("btnKill").disabled = s.status === "stopped" && !s.positions.length;
  $("lastCycle").textContent = s.last_cycle ? `Letzte Runde ${tfmt(s.last_cycle)} · ${nf(1).format((s.cycle_ms || 0) / 1000)} s` : "erste Runde läuft …";
}

function renderMoney(s) {
  const a = s.account;
  const started = new Date(a.started_at * 1000).toLocaleDateString("de-DE");
  $("potLabel").textContent = `Topf des Bots · eingezahlt ${eur(a.start, 0)} Spielgeld seit ${started}`;
  $("equity").textContent = eur(a.equity);
  $("equitySub").textContent = s.positions.length
    ? `${s.positions.length} Trade${s.positions.length > 1 ? "s" : ""} laufen · gebunden ${eur(a.margin, 0)} (${nf(0).format((a.margin / a.equity) * 100)} %)`
    : "gerade läuft kein Trade";
  const t = a.today;
  $("today").textContent = eurS(t.pnl); $("today").className = "big mono " + cls(t.pnl);
  $("todaySub").textContent = `seit Mitternacht · Trades ${eurS(t.realized)} · Gebühren ${eurS(t.fees)} · Funding ${eurS(t.funding)}`;
  const T = a.total;
  $("total").textContent = eurS(T.pnl); $("total").className = "big mono " + cls(T.pnl);
  $("totalSub").textContent = `${pct((T.pnl / a.start) * 100, 2)} · abgeschlossen ${eurS(T.realized)} · offen ${eurS(T.open)} · Gebühren ${eurS(T.fees)} · Funding ${eurS(T.funding)}`;
  const tr = a.trades;
  $("winrate").textContent = tr.n ? nf(0).format((tr.wins / tr.n) * 100) + " %" : "–";
  $("winrateSub").textContent = tr.n ? `${tr.wins} von ${tr.n} Trades · zusammen ${eurS(tr.net)} · Rückgang vom Höchststand ${nf(1).format(a.dd_pct)} %` : "noch kein Trade beendet";
}

function renderCoins(s) {
  $("coinsDot").className = "dot" + (s.conn.ok ? "" : " off");
  $("coinsMeta").textContent = s.last_cycle ? `Stand ${tfmt(s.last_cycle)}` : "";
  const thr = 50 + 50 * s.params.min_agreement;
  if (!s.coins.length) {
    $("coins").innerHTML = `<div class="empty">${s.traders_count ? "Die Profis haben gerade keine Positionen in liquiden Coins." : "Der Bot sucht gerade die Top-Profis aus der Bestenliste. Das dauert beim ersten Start etwa 1–2 Minuten."}</div>`;
    return;
  }
  const list = showAllCoins ? s.coins : s.coins.filter((c, i) => i < 15 || c.position !== 0);
  const moreBtn = s.coins.length > list.length || showAllCoins
    ? `<div class="more"><button class="btn btn-ghost btn-sm" id="moreCoins">${showAllCoins ? "Weniger anzeigen" : `Alle ${s.coins.length} Coins anzeigen`}</button></div>` : "";
  $("coins").innerHTML = list.map((c) => {
    const ls = c.long_share * 100;
    const held = c.position !== 0;
    const tag = held ? `<span class="tag ${c.position > 0 ? "tag-long" : "tag-short"}">${c.position > 0 ? "Bot long" : "Bot short"}</span>` : c.locked ? `<span class="tag tag-lock">Pause</span>` : "";
    const day = c.day_pct == null ? "" : ` <span class="${cls(c.day_pct)}">${pct(c.day_pct)}</span>`;
    const quiet = c.verdict === "few" || c.verdict === "split";
    return `<div class="coin ${held ? "held" : ""} ${quiet && !held ? "quiet" : ""}">
      <div class="head"><div class="name">${esc(c.coin)}${tag}</div><div class="price mono">${price(c.price)} $${day}</div></div>
      <div class="mood" title="gewichtet nach Vertrauen und Einsatz">
        <div class="mood-bar" role="img" aria-label="${nf(0).format(ls)} Prozent steigend"><div class="l" style="width:${ls}%"></div>
          <div class="mark" style="left:${thr}%"></div><div class="mark" style="left:${100 - thr}%"></div></div>
        <div class="mood-meta"><span class="pos">${c.n_long} steigend · ${nf(0).format(ls)} %</span><span class="neg">${nf(0).format(100 - ls)} % · ${c.n_short} fallend</span></div>
      </div>
      <div class="text">${esc(c.text)}${held ? ` <span class="${cls(c.upnl)} mono">${eurS(c.upnl)}</span>` : ""}</div>
    </div>`;
  }).join("") + moreBtn;
}

function renderPositions(s) {
  if (!s.positions.length) {
    $("positions").innerHTML = `<div class="empty">Gerade läuft kein Trade. Der Bot wartet, bis eine klare Mehrheit der Profis auf einen Coin setzt.</div>`;
    return;
  }
  $("positions").innerHTML = `<div class="positions">` + s.positions.map((p) => {
    const toStop = Math.max(0, Math.min(100, 100 - (p.stop_dist_pct / (s.params.stop_loss_pct * 100)) * 100));
    return `<div class="pos-card">
      <div class="title">${esc(p.coin)} <span class="tag ${p.side === "long" ? "tag-long" : "tag-short"}">${p.side === "long" ? "long · steigend" : "short · fallend"}</span></div>
      <div class="pnl mono ${cls(p.upnl)}">${eurS(p.upnl)} <span class="small">(${pct(p.upnl_margin_pct)} auf Einsatz)</span></div>
      <div class="row"><span class="muted">Position</span><span class="mono">${eur(p.notional, 0)} · Hebel ${nf(1).format(s.params.leverage)}</span></div>
      <div class="row"><span class="muted">Einstieg → jetzt</span><span class="mono">${price(p.entry)} → ${price(p.mark)}</span></div>
      <div class="row"><span class="muted">Profis jetzt</span><span>${p.n_now} drin, ${nf(0).format(p.share_now * 100)} % gleiche Seite</span></div>
      <div class="row"><span class="muted">Läuft seit</span><span>${dur(Date.now() / 1000 - p.opened_at)}${p.adds ? ` · ${p.adds}× aufgestockt` : ""}</span></div>
      <div class="row"><span class="muted">Gebühren / Funding</span><span class="mono">${eurS(-p.fees)} / ${eurS(p.funding)}</span></div>
      <div class="stopbar" title="Abstand zum Stop-Loss"><span style="width:${toStop}%"></span></div>
      <div class="row small"><span class="muted">Stop-Loss bei ${price(p.stop)}</span><span class="muted">noch ${nf(1).format(p.stop_dist_pct)} % entfernt</span></div>
    </div>`;
  }).join("") + `</div>`;
}

async function loadTrades() {
  try {
    const d = await api("/api/trades?limit=60");
    if (!d.trades.length) { $("trades").innerHTML = `<div class="empty">Noch kein Trade beendet.</div>`; return; }
    $("trades").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Geschlossen</th><th>Coin</th><th>Richtung</th><th class="num">Größe</th><th class="num">Einstieg → Ausstieg</th><th class="num">Ergebnis</th><th>Dauer</th><th>Grund</th></tr></thead><tbody>` +
      d.trades.map((t) => `<tr><td class="mono small">${tfmt(t.closed_at, true)}</td><td><b>${esc(t.coin)}</b></td>
        <td><span class="tag ${t.side === "long" ? "tag-long" : "tag-short"}">${t.side}</span></td>
        <td class="num">${eur(t.max_notional, 0)}</td><td class="num">${price(t.entry)} → ${price(t.exit)}</td>
        <td class="num ${cls(t.net)}" title="Kurs ${eurS(t.pnl)} · Gebühren ${eurS(-t.fees)} · Funding ${eurS(t.funding)}">${eurS(t.net)}</td>
        <td class="small">${dur(t.closed_at - t.opened_at)}</td><td class="small">${esc(t.reason)}</td></tr>`).join("") + `</tbody></table></div>`;
  } catch (e) { /* egal */ }
}

// ------------------------------------------------------------------ Profis
async function loadTraders() {
  try {
    const d = await api("/api/traders");
    traders = d.traders;
    renderTraders();
  } catch (e) { /* egal */ }
}

function renderTraders() {
  const only = $("onlyActive").checked;
  const all = traders.filter((t) => !only || t.n_pos);
  const list = showAllTraders ? all : all.slice(0, 20);
  if (!traders.length) { $("traders").innerHTML = `<div class="empty">Die Auswahl läuft noch …</div>`; return; }
  $("traders").innerHTML = list.map((t, i) => {
    const open = openTraders.has(t.address);
    const tc = t.trust >= 1.15 ? "pos" : t.trust <= 0.85 ? "neg" : "";
    const chips = t.positions.slice(0, 6).map((p) => `<span class="chip chip-${p.side}">${esc(p.coin)}</span>`).join("") + (t.n_pos > 6 ? `<span class="chip muted">+${t.n_pos - 6}</span>` : "") || `<span class="muted small">gerade keine Position</span>`;
    return `<div class="trader">
      <button aria-expanded="${open}" data-addr="${t.address}">
        <span class="rank">${i + 1}</span>
        <span class="who">${esc(t.name || t.short)}<small>${t.name ? t.short + " · " : ""}Monat ${pct(t.month_roi * 100, 0)} · ${usd(t.account)}</small></span>
        <span class="col"><span class="trust ${tc}">${nf(2).format(t.trust)}×</span><small>Vertrauen ${t.trust_24h ? (t.trust_24h > 0 ? "▲" : "▼") : ""}</small></span>
        <span class="col hide-m ${cls(t.month_roi)}">${pct(t.month_roi * 100, 0)}<small>Monat</small></span>
        <span class="col hide-m">${usd(t.account)}<small>Konto</small></span>
        <span class="chips">${chips}</span>
      </button>
      ${open ? traderDetail(t) : ""}
    </div>`;
  }).join("") + (all.length > 20 ? `<div class="more"><button class="btn btn-ghost btn-sm" id="moreTraders">${showAllTraders ? "Nur die ersten 20 zeigen" : `Alle ${all.length} Profis anzeigen`}</button></div>` : "")
    || `<div class="empty">Gerade hat keiner der Profis eine offene Position.</div>`;
  list.filter((t) => openTraders.has(t.address)).forEach(drawTraderChart);
}

function traderDetail(t) {
  const rows = t.positions.map((p) => `<tr><td><b>${esc(p.coin)}</b></td><td><span class="tag tag-${p.side}">${p.side}</span></td><td class="num">${usd(p.value)}</td><td class="num">${p.lev ?? "–"}×</td><td class="num ${cls(p.upnl)}">${usd(p.upnl)}</td></tr>`).join("");
  return `<div class="trader-detail">
    <div>
      <div class="small muted">Kopieren hätte seit Beobachtung <b class="${cls(t.copy_ret)}">${pct(t.copy_ret * 100, 2)}</b> gebracht (${t.obs} Runden). Monatsgewinn ${usd(t.month_pnl)}, insgesamt ${usd(t.alltime_pnl)}. Hebel gesamt ${nf(1).format(t.lev)}×.</div>
      <div class="table-wrap" style="margin-top:8px">${rows ? `<table><thead><tr><th>Coin</th><th>Seite</th><th class="num">Größe</th><th class="num">Hebel</th><th class="num">offen</th></tr></thead><tbody>${rows}</tbody></table>` : `<div class="empty">Keine offenen Positionen.</div>`}</div>
      <div class="small" style="margin-top:6px"><a href="https://app.hyperliquid.xyz/explorer/address/${t.address}" target="_blank" rel="noopener">Konto auf Hyperliquid ansehen</a></div>
    </div>
    <div><div class="small muted">Kontowert letzter Monat</div><canvas data-chart="${t.address}"></canvas></div>
  </div>`;
}

async function drawTraderChart(t) {
  const cv = document.querySelector(`canvas[data-chart="${t.address}"]`);
  if (!cv) return;
  if (!traderHist[t.address]) {
    drawLine(cv, null, { axis: false, empty: "lädt …" });
    try { traderHist[t.address] = (await api(`/api/trader/${t.address}`)).history; } catch (e) { traderHist[t.address] = []; }
  }
  const c2 = document.querySelector(`canvas[data-chart="${t.address}"]`);
  if (c2) drawLine(c2, traderHist[t.address], { ms: true, fmt: (v) => usd(v), empty: "keine Daten" });
}

// ------------------------------------------------------------------ Lernen
function renderLearning(s) {
  $("evalMeta").textContent = `Der Bot wertet sich alle 6 Stunden selbst aus. Nächste Auswertung ${tfmt(s.next_eval, true)}.`;
  $("learned").innerHTML = s.learned.length
    ? `<div class="learned">${s.learned.map((l) => `<p>${esc(l.text)}</p>`).join("")}</div><div class="muted small">Stand ${tfmt(s.learned[0].ts, true)}</div>`
    : `<div class="empty">Die erste Auswertung kommt nach 6 Stunden. Bis dahin sammelt der Bot Daten. Erst ab ${s.rules.switch_min_trades} Trades zieht er Schlüsse – vorher ist es zu viel Zufall.</div>`;
  const p = s.params;
  $("currentRules").innerHTML = `Aktuelle Regeln: Einstieg ab <b>${nf(0).format(50 + 50 * p.min_agreement)} %</b> Mehrheit und mind. <b>${p.min_traders}</b> Profis ·
    Ausstieg unter <b>${nf(0).format(50 + 50 * p.exit_agreement)} %</b> · Stop-Loss <b>${nf(1).format(p.stop_loss_pct * 100)} %</b> ·
    volle Größe ab <b>${p.full_traders}</b> Profis · Vertrauen <b>${p.use_trust ? "an" : "aus"}</b> · Hebel <b>${nf(1).format(p.leverage)}</b>`;
  $("variantRule").textContent = `Alle Konten starten mit 5.000 € und handeln dieselben Daten. Der Bot übernimmt bessere Regeln erst, wenn ein Schattenkonto mind. ${s.rules.switch_min_trades} Trades und ${s.rules.switch_min_hours} h hinter sich hat und mind. ${s.rules.switch_margin} Prozentpunkte vorne liegt (Rendite minus halber Rückgang). „Ohne Lernen“ zeigt, ob das Vertrauen wirklich etwas bringt.`;
  $("variants").innerHTML = `<div class="variants">` + s.variants.map((v) => `<div class="variant ${v.main ? "is-main" : ""}">
      <div class="v-head">${esc(v.name)}${v.main ? `<span class="badge badge-main">Hauptkonto</span>` : ""}${v.best ? `<span class="badge badge-best">führt</span>` : ""}${v.same_as_main ? `<span class="badge">gleiche Regeln wie Hauptkonto</span>` : ""}</div>
      <div class="v-nums">
        <div class="${cls(v.ret_pct)}">${pct(v.ret_pct, 2)}<small>seit Start · ${eur(v.equity, 0)}</small></div>
        <div>${v.window && v.window.score != null ? `<span class="${cls(v.window.ret_pct)}">${pct(v.window.ret_pct, 2)}</span>` : "–"}<small>im Vergleich${v.window && v.window.mdd_pct != null ? ` · Rückgang ${nf(1).format(v.window.mdd_pct)} %` : ""}</small></div>
        <div>${v.trades}<small>Trades${v.trades ? ` · ${nf(0).format((v.wins / v.trades) * 100)} % Gewinn` : ""}</small></div>
      </div>
      <div class="small muted">${esc(v.rules)}</div>
      <div class="small muted">${v.note ? esc(v.note) + " · " : ""}läuft seit ${dur(v.age_h * 3600)}</div>
      ${!v.main && !v.same_as_main ? `<div><button class="btn btn-ghost btn-sm" data-adopt="${v.id}">Regeln fürs Hauptkonto übernehmen</button></div>` : ""}
    </div>`).join("") + `</div>`;
  $("coinStats").innerHTML = s.coin_stats.length
    ? `<div class="table-wrap"><table><thead><tr><th>Coin</th><th class="num">Trades</th><th class="num">mit Gewinn</th><th class="num">zusammen</th></tr></thead><tbody>` +
      s.coin_stats.map((c) => `<tr><td><b>${esc(c.coin)}</b></td><td class="num">${c.n}</td><td class="num">${nf(0).format((c.wins / c.n) * 100)} %</td><td class="num ${cls(c.net)}">${eurS(c.net)}</td></tr>`).join("") + `</tbody></table></div>`
    : `<div class="empty">Noch keine abgeschlossenen Trades. Ab 5 Trades pro Coin sperrt der Bot Coins, die ihm dauerhaft Verluste bringen.</div>`;
  const st = s.selftest;
  $("selftest").innerHTML = st
    ? `<div class="checks">${st.checks.map((c) => `<div>${c.ok ? "✅" : "⚠️"} <b>${esc(c.name)}</b> <span class="muted">– ${esc(c.detail)}</span></div>`).join("")}</div><div class="muted small" style="margin-top:4px">Letzter Test ${ago(st.ts)}. Läuft automatisch einmal am Tag.</div>`
    : `<div class="empty">Läuft automatisch einmal am Tag – oder jetzt per Knopf.</div>`;
  $("events").innerHTML = s.events.length
    ? s.events.map((e) => `<div class="ev"><time>${tfmt(e.ts, true)}</time><div><b>${esc(e.title)}</b><br><span class="muted">${esc(e.detail)}</span></div></div>`).join("")
    : `<div class="empty">Noch nichts passiert.</div>`;
}

function renderLog(s) {
  const box = $("log");
  const atTop = box.scrollTop < 20;
  box.innerHTML = s.log.map((l) => `<div class="${l.level}"><time>${tfmt(l.ts)}</time>${esc(l.msg)}</div>`).join("");
  if (atTop) box.scrollTop = 0;
}

function renderSelection(s) {
  const sel = s.selection || {};
  $("selectionMeta").textContent = sel.at
    ? `Sortiert nach Vertrauen. Auswahl ${ago(sel.at)} aus ${nf(0).format(sel.rows)} Konten, nächste Prüfung ${tfmt(s.next_lb, true)}. ${sel.reserves} Nachrücker stehen bereit. Tippe auf einen Profi für Details.`
    : "Die Auswahl läuft beim ersten Start – das dauert 1–2 Minuten.";
  const c = s.rules.selection;
  $("criteria").innerHTML = `<p>Grundlage ist die öffentliche Bestenliste von Hyperliquid. Dort sind alle Konten und offenen Positionen für jeden einsehbar – deshalb kann der Bot wirklich sehen, was die Profis halten, statt nur Signale abzuschreiben.</p>
    <p>Ein Konto kommt in die engere Wahl, wenn es mindestens ${usd(c.min_account_usd)} im Perp-Konto hat, im letzten Monat zwischen +${c.min_month_roi * 100} % und +${c.max_month_roi * 100} % gemacht hat (mehr ist fast immer ein Einzahlungs-Effekt), insgesamt mindestens ${usd(c.min_alltime_pnl)} Gewinn hat, diese Woche nicht mehr als ${-c.min_week_roi * 100} % verloren hat und wirklich handelt (Monatsvolumen zwischen ${c.min_turnover}× und ${c.max_turnover}× vom Konto – darüber sind es Market-Maker, die sich nicht kopieren lassen). Wer mehr als ${c.max_open_positions} Coins gleichzeitig hält, fliegt ebenfalls raus.</p>
    <p>Die Passenden werden nach Monatsgewinn, Monatsrendite, Gesamtgewinn und Wochenrendite bewertet, live geprüft, und die besten ${s.rules.top_n} kopiert. Profis, denen der Bot schon vertraut, behalten einen kleinen Bonus. Alle 6 Stunden wird neu ausgewählt.</p>
    <p>Wichtig: Eine gute Vergangenheit ist keine Garantie. Viele Top-Trader von heute stehen morgen nicht mehr oben. Genau deshalb misst der Bot selbst nach, wem sich das Kopieren wirklich lohnt.</p>`;
}

function fillSettings(s) {
  const f = $("settingsForm");
  const p = s.params, st = s.settings;
  f.leverage.value = p.leverage;
  f.max_coin_pct.value = Math.round(p.max_coin_pct * 100);
  f.max_margin_pct.value = Math.round(p.max_margin_pct * 100);
  f.stop_loss_pct.value = +(p.stop_loss_pct * 100).toFixed(1);
  f.max_positions.value = p.max_positions;
  f.min_traders.value = p.min_traders;
  f.min_agreement.value = Math.round(50 + 50 * p.min_agreement);
  f.exit_agreement.value = Math.round(50 + 50 * p.exit_agreement);
  f.max_drawdown_pct.value = Math.round(st.max_drawdown_pct * 100);
  f.cycle_seconds.value = st.cycle_seconds;
  f.auto_learn.checked = st.auto_learn;
  settingsFilled = true;
}

async function refresh() {
  try {
    S = await api("/api/state");
  } catch (e) {
    $("pillConn").className = "pill pill-bad";
    $("pillConn").textContent = "Bot nicht erreichbar – läuft das Fenster mit start.bat noch?";
    return;
  }
  renderHeader(S); renderMoney(S); renderCoins(S); renderPositions(S); renderLearning(S); renderLog(S); renderSelection(S);
  if (!settingsFilled) fillSettings(S);
}

// ------------------------------------------------------------------ Events
$("btnPause").onclick = async () => {
  const a = $("btnPause").dataset.action;
  await api("/api/control", { action: a });
  toast(a === "pause" ? "Pausiert. Stop-Loss bleibt aktiv." : "Läuft wieder.");
  refresh();
};
$("btnKill").onclick = async () => {
  if (!confirm("Not-Aus: alle Positionen schließen und den Bot stoppen?")) return;
  await api("/api/control", { action: "kill" });
  toast("Not-Aus ausgeführt. Mit „Weiter“ startest du wieder.");
  refresh(); loadTrades();
};
$("btnEval").onclick = async () => {
  const d = await api("/api/evaluate", {});
  toast("Ausgewertet."); refresh();
};
$("btnSelftest").onclick = async () => {
  $("btnSelftest").disabled = true;
  try { const r = await api("/api/selftest", {}); toast(r.ok ? "Selbsttest: alles in Ordnung." : "Selbsttest: Auffälligkeiten – siehe unten."); }
  finally { $("btnSelftest").disabled = false; refresh(); }
};
$("btnRefresh").onclick = async () => {
  const r = await api("/api/refresh_traders", {});
  toast(r.ok ? "Bestenliste wird neu geladen (1–2 Minuten)." : "Läuft schon.");
};
$("btnReset").onclick = async () => {
  const keep = $("keepLearning").checked;
  if (!confirm(`Testkonto auf 5.000 € zurücksetzen? Alle Trades werden gelöscht.${keep ? " Das Vertrauen in die Profis bleibt." : " Auch alles Gelernte wird gelöscht."}`)) return;
  await api("/api/reset", { confirm: "RESET", keep_learning: keep });
  settingsFilled = false;
  toast("Testkonto zurückgesetzt.");
  refresh(); loadTrades(); loadChart();
};
$("settingsForm").onsubmit = async (ev) => {
  ev.preventDefault();
  const f = ev.target, n = (k) => parseFloat(f[k].value);
  const body = {
    params: {
      leverage: n("leverage"), max_coin_pct: n("max_coin_pct") / 100, max_margin_pct: n("max_margin_pct") / 100,
      stop_loss_pct: n("stop_loss_pct") / 100, max_positions: Math.round(n("max_positions")), min_traders: Math.round(n("min_traders")),
      min_agreement: (n("min_agreement") - 50) / 50, exit_agreement: (n("exit_agreement") - 50) / 50,
    },
    settings: { max_drawdown_pct: n("max_drawdown_pct") / 100, cycle_seconds: Math.round(n("cycle_seconds")), auto_learn: f.auto_learn.checked },
  };
  if (body.params.exit_agreement >= body.params.min_agreement) { $("settingsMsg").textContent = "Ausstieg muss unter der Einstiegs-Mehrheit liegen."; return; }
  const r = await api("/api/settings", body);
  $("settingsMsg").textContent = r.changed.length ? "Gespeichert." : "Keine Änderung.";
  settingsFilled = false; refresh();
};
$("rangeSeg").onclick = (ev) => {
  const b = ev.target.closest("button"); if (!b) return;
  range = b.dataset.range;
  [...$("rangeSeg").children].forEach((x) => x.classList.toggle("on", x === b));
  loadChart();
};
$("onlyActive").onchange = renderTraders;
$("coins").addEventListener("click", (ev) => { if (ev.target.id === "moreCoins") { showAllCoins = !showAllCoins; renderCoins(S); } });
$("traders").onclick = (ev) => {
  if (ev.target.id === "moreTraders") { showAllTraders = !showAllTraders; renderTraders(); return; }
  const b = ev.target.closest("button[data-addr]"); if (!b) return;
  const a = b.dataset.addr;
  openTraders.has(a) ? openTraders.delete(a) : openTraders.add(a);
  renderTraders();
};
$("variants").onclick = async (ev) => {
  const b = ev.target.closest("button[data-adopt]"); if (!b) return;
  if (!confirm("Regeln dieses Schattenkontos für das Hauptkonto übernehmen?")) return;
  await api("/api/adopt", { id: b.dataset.adopt });
  toast("Regeln übernommen."); settingsFilled = false; refresh();
};
window.addEventListener("resize", () => { loadChart(); });

refresh().then(() => { loadChart(); loadTrades(); loadTraders(); });
// Alle 1,5 Sekunden neu laden (nicht im Hintergrund, nie doppelt)
let _busy = false;
setInterval(async () => { if (_busy || document.hidden) return; _busy = true; try { await refresh(); } finally { _busy = false; } }, 1500);
setInterval(() => { if (!document.hidden) loadTrades(); }, 5000);
setInterval(loadTraders, 30000);
setInterval(loadChart, 60000);
