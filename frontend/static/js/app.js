const S = {
  allMarkets: [], activeSport: 'all', activeEdge: 'all',
  userEmail: localStorage.getItem('eb_email') || null,
  sessionId: localStorage.getItem('eb_session') || null,
  isGuest: localStorage.getItem('eb_guest') === 'true'
};

let currentAlertMarket = null;

window.onload = async () => {
    const urlParams = new URLSearchParams(window.location.search);
    const sid = urlParams.get('session_id');
    const email = urlParams.get('email');
    const error = urlParams.get('error');

    if (sid && email) {
        localStorage.setItem('eb_session', sid);
        localStorage.setItem('eb_email', email);
        localStorage.removeItem('eb_guest');
        S.sessionId = sid;
        S.userEmail = email;
        S.isGuest = false;
        window.history.replaceState({}, document.title, "/"); 
    } else if (error) {
        alert(error === 'expired_token' ? "Link expired. Please request a new one." : "Invalid login link.");
        window.history.replaceState({}, document.title, "/"); 
    }

    if (S.sessionId || S.isGuest) {
        startApp();
    } else {
        showScreen('screen-landing');
    }
    loadLandingStats();
};

async function handleAuth(type) {
    const emailInput = document.getElementById('auth-email');
    const msg = document.getElementById('auth-msg');
    const inBtn = document.getElementById('signin-btn');
    const upBtn = document.getElementById('signup-btn');
    
    const email = emailInput.value.trim();
    if (!email) {
        msg.style.color = "var(--danger)";
        msg.textContent = "Please enter an email address.";
        return;
    }

    inBtn.disabled = true;
    upBtn.disabled = true;
    msg.style.color = "var(--accent)";
    msg.textContent = "Sending...";

    const endpoint = type === 'in' ? '/api/auth/sign-in' : '/api/auth/sign-up';

    try {
        const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Server error");
        msg.style.color = "var(--accent)";
        msg.textContent = "Authorization link sent! Check your inbox.";
    } catch (e) {
        msg.style.color = "var(--danger)";
        msg.textContent = e.message;
    } finally {
        inBtn.disabled = false;
        upBtn.disabled = false;
    }
}

function enterAsGuest() {
    localStorage.setItem('eb_guest', 'true');
    S.isGuest = true;
    startApp();
}

function toggleAuth() {
    localStorage.removeItem('eb_session');
    localStorage.removeItem('eb_email');
    localStorage.removeItem('eb_guest');
    S.sessionId = null;
    S.userEmail = null;
    S.isGuest = false;
    showScreen('screen-landing');
}

function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}

async function loadLandingStats() {
  try {
    const d = await fetch('/api/stats').then(r => r.json());
    document.getElementById('live-count').textContent = typeof d.open_markets === 'number' ? d.open_markets.toLocaleString() : d.open_markets;
  } catch {
    document.getElementById('live-count').textContent = '500+';
  }
}

function startApp() {
  showScreen('screen-markets');
  
  if (S.isGuest) {
      document.getElementById('user-display').textContent = "Guest";
      document.getElementById('auth-action-btn').textContent = "[Sign In]";
      document.getElementById('header-alerts-btn').style.display = 'none';
  } else {
      document.getElementById('user-display').textContent = S.userEmail;
      document.getElementById('auth-action-btn').textContent = "[Sign Out]";
      document.getElementById('header-alerts-btn').style.display = 'block';
      updateAlertCountBadge();
  }
  
  loadMarkets();
}

async function loadMarkets() {
  document.getElementById('markets-list').innerHTML = '<div class="loading-state"><div class="spinner"></div><div class="loading-text">Scanning sports markets...</div></div>';
  try {
    const r = await fetch('/api/markets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile: {} }),
    });
    const data = await r.json();
    S.allMarkets = data.markets || [];
    renderSportFilters();
    applyFilters();
  } catch (e) {}
}

const SPORT_ICONS = {
  'all':'⚡','NFL':'🏈','NBA':'🏀','MLB':'⚾','NHL':'🏒','Soccer':'⚽',
  'Tennis':'🎾','UFC / MMA':'🥊','Golf':'⛳','Racing':'🏎',
  'Olympics':'🏅','Rugby / Cricket':'🏉','College Sports':'🎓','Other Sports':'🏆',
};

function renderSportFilters() {
  const cats = ['all', ...new Set(S.allMarkets.map(m => m.category).filter(Boolean).sort())];
  const row  = document.getElementById('sport-filters');
  row.innerHTML = '';
  cats.forEach(cat => {
    const c = document.createElement('button');
    c.className   = `filter-chip ${cat === 'all' ? 'active' : ''}`;
    c.textContent = `${SPORT_ICONS[cat] || '🏆'} ${cat === 'all' ? 'All Sports' : cat}`;
    c.onclick = () => setSportFilter(cat, c);
    row.appendChild(c);
  });
}

function setSportFilter(sport, btn) {
  document.querySelectorAll('#sport-filters .filter-chip').forEach(c => c.classList.remove('active'));
  btn.classList.add('active');
  S.activeSport = sport;
  applyFilters();
}

function setEdgeFilter(edge, btn) {
  document.querySelectorAll('.subfilter-chip').forEach(c => c.classList.remove('active-all', 'active-yes', 'active-no'));
  btn.classList.add(edge === 'all' ? 'active-all' : edge === 'yes' ? 'active-yes' : 'active-no');
  S.activeEdge = edge;
  applyFilters();
}

function applyFilters() {
  let m = S.allMarkets;
  if (S.activeSport !== 'all') m = m.filter(x => x.category === S.activeSport);
  if (S.activeEdge === 'yes')  m = m.filter(x => (x.edge || 0) > 0);
  else if (S.activeEdge === 'no') m = m.filter(x => (x.edge || 0) < 0);
  renderMarkets(m);
}

function renderMarkets(markets) {
  const list = document.getElementById('markets-list');
  document.getElementById('market-count').textContent = `${markets.length} markets`;
  if (!markets.length) {
    list.innerHTML = `<div class="no-results"><div class="no-results-text">No markets match your filters.</div></div>`;
    return;
  }
  list.innerHTML = '';
  markets.forEach(m => list.appendChild(buildCard(m)));
}

function buildCard(m) {
  const card = document.createElement('div');
  const eClass = (m.edge || 0) > 0 ? 'positive' : 'negative';
  const tType  = (m.edge || 0) > 0 ? 'edge' : 'value';
  const tLabel = (m.edge || 0) > 0 ? '📈 EDGE' : '🎯 VALUE';
  const eTxt   = m.edge != null ? `${m.edge > 0 ? '+' : ''}${(m.edge * 100).toFixed(1)}¢` : '—';

  card.className = `market-card ${(m.edge || 0) > 0 ? 'underpriced' : 'value'}`;
  card.id = `market-${m.market_slug}`;
  card.dataset.cacheKey  = m.cache_key || '';
  card.dataset.question  = m.question;
  card.dataset.yesPrice  = m.yes_price;
  card.dataset.polyUrl   = m.poly_url || 'https://polymarket.com';
  card.dataset.marketSlug = m.market_slug || '';
  card.dataset.endDate   = m.end_date || '';

  card.innerHTML = `
    <div class="card-main">
      <div class="card-top" style="align-items:flex-start;">
        <div style="display:flex; flex-direction:column; gap:.3rem; margin-top:2px; flex-shrink:0;">
            <span class="card-tag ${tType}">${tLabel}</span>
            ${m.category ? `<span class="card-tag" style="background:rgba(90,97,128,.12); color:var(--muted); border:1px solid var(--border);">${m.category}</span>` : ''}
        </div>
        <div class="card-question">${m.question}</div>
      </div>
      <div class="card-meta">
        <div class="price-block">
          <span class="price-label">YES</span>
          <span class="price-val yes">${(m.yes_price * 100).toFixed(0)}¢</span>
        </div>
        <div class="price-block">
          <span class="price-label">NO</span>
          <span class="price-val no">${(m.no_price != null ? (m.no_price * 100).toFixed(0) : ((1-m.yes_price)*100).toFixed(0))}¢</span>
        </div>
        ${m.end_date ? `<div class="price-block"><span class="price-label">Ends</span><span class="price-val" style="font-size:.85rem;">${m.end_date}</span></div>` : ''}
        <div class="edge-badge ${eClass}">${eTxt} edge</div>
      </div>
      <div class="card-actions" style="margin-top:.9rem">
        <button class="action-btn yes-btn" onclick="window.open('${m.poly_url || 'https://polymarket.com'}','_blank')">Bet YES ↗</button>
        <button class="action-btn no-btn" onclick="window.open('${m.poly_url || 'https://polymarket.com'}','_blank')">Bet NO ↗</button>
        <button class="action-btn poly-btn" onclick="window.open('${m.poly_url || 'https://polymarket.com'}','_blank')">Polymarket ↗</button>
        ${!S.isGuest ? `<button class="action-btn poly-btn alert-btn">🔔 Alert</button>` : ''}
        <button class="action-btn ai-btn">🤖 Analyze Market</button>
      </div>
    </div>
    <div class="ai-panel" style="display:none;"></div>`;

  card.querySelector('.ai-btn').addEventListener('click', () => triggerAnalysis(card));
  
  const alertBtn = card.querySelector('.alert-btn');
  if (alertBtn) {
    alertBtn.addEventListener('click', () => {
      if (S.isGuest) {
          alert("You must sign in to save alerts!");
          return;
      }
      currentAlertMarket = m;
      document.getElementById('alert-q').textContent = m.question;
      document.getElementById('alert-error').textContent = "";
      document.getElementById('alert-modal').style.display = "flex";
    });
  }

  return card;
}
/* ─── Alerts ─────────────────────────────────────────────── */

function openAlertModal(card) {
    currentAlertMarket = {
        market_slug: card.dataset.marketSlug,
        question: card.dataset.question,
    };
    document.getElementById('alert-q').textContent = card.dataset.question;
    document.getElementById('alert-error').textContent = '';
    document.getElementById('alert-modal').style.display = 'flex';
}

function closeAlertModal() {
    document.getElementById('alert-modal').style.display = 'none';
    currentAlertMarket = null;
}

async function updateAlertCountBadge() {
    if (!S.userEmail) return;
    try {
        const res = await fetch(`/api/alerts/${S.userEmail}`);
        const data = await res.json();
        document.getElementById('alert-usage').textContent = data.alerts.length;
    } catch (e) {}
}

async function saveAlert() {
    const side = document.getElementById('alert-side').value;
    const price = parseInt(document.getElementById('alert-price').value) / 100;
    const errorEl = document.getElementById('alert-error');
    errorEl.textContent = "Saving...";
    
    try {
        const res = await fetch('/api/alerts/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_email: S.userEmail,
                market_slug: currentAlertMarket.market_slug,
                question: currentAlertMarket.question,
                target_price: price,
                target_side: side
            })
        });
        if (!res.ok) {
            const data = await res.json();
            errorEl.textContent = data.detail || "Error saving alert.";
            return;
        }
        closeAlertModal();
        updateAlertCountBadge();
    } catch (e) {
        errorEl.textContent = "Network error.";
    }
}

async function openManageAlerts() {
    document.getElementById('manage-alerts-modal').style.display = 'flex';
    const list = document.getElementById('manage-alerts-list');
    list.innerHTML = `<div class="loading-state"><div class="spinner"></div></div>`;

    try {
        const res = await fetch(`/api/alerts/${S.userEmail}`);
        const data = await res.json();
        document.getElementById('alert-usage').textContent = data.alerts.length;

        if (data.alerts.length === 0) {
            list.innerHTML = "<p style='color:var(--muted); text-align:center;'>You have no active alerts.</p>";
            return;
        }
        list.innerHTML = data.alerts.map(a => `
            <div style="background:var(--surface2); padding:1rem; border-radius:8px; display:flex; justify-content:space-between; align-items:center; border: 1px solid var(--border);">
                <div style="padding-right: 1rem;">
                    <div style="font-size:0.85rem; margin-bottom:0.4rem; font-weight:600;">
                        <a href="javascript:void(0)" onclick="scrollToMarket('${a.market_slug}')" style="color:var(--text); text-decoration:none; border-bottom:1px dashed var(--muted);">${a.question}</a>
                    </div>
                    <span class="card-tag edge" style="font-size:0.7rem;">Target: ${a.target_side} at ${a.target_price * 100}¢</span>
                </div>
                <button class="action-btn no-btn" onclick="deleteAlert('${a._id}')" style="flex-shrink:0;">Remove</button>
            </div>
        `).join('');
    } catch (e) {
        list.innerHTML = "<p style='color:var(--danger);'>Failed to load alerts.</p>";
    }
}

async function deleteAlert(id) {
    try {
        const res = await fetch(`/api/alerts/${id}`, { method: 'DELETE' });
        if(res.ok) { openManageAlerts(); updateAlertCountBadge(); }
    } catch(e) { alert("Failed to delete alert."); }
}

function scrollToMarket(slug) {
    document.getElementById('manage-alerts-modal').style.display = 'none';
    const allSportBtn = document.querySelector('#sport-filters .filter-chip');
    if (allSportBtn) setSportFilter('all', allSportBtn);
    const allEdgeBtn = document.querySelector('.subfilter-chip.active-all') || document.querySelector('.subfilter-chip');
    if (allEdgeBtn) setEdgeFilter('all', allEdgeBtn);
    setTimeout(() => {
        const targetCard = document.getElementById(`market-${slug}`);
        if (targetCard) {
            targetCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
            targetCard.style.transition = 'box-shadow 0.3s ease-in-out';
            targetCard.style.boxShadow = '0 0 0 3px var(--accent)';
            setTimeout(() => { targetCard.style.boxShadow = ''; }, 2000);
        } else {
            alert("This market is no longer active on the main page.");
        }
    }, 50);
}

/* ─── AI ANALYSIS ────────────────────────────────────────── */

async function triggerAnalysis(card) {
  const btn      = card.querySelector('.ai-btn');
  const panel    = card.querySelector('.ai-panel');
  const cacheKey = card.dataset.cacheKey;
  const question = card.dataset.question;
  const yesPrice = parseFloat(card.dataset.yesPrice);
  const polyUrl  = card.dataset.polyUrl;
  const marketSlug = card.dataset.marketSlug || '';
  const endDate    = card.dataset.endDate || '';

  btn.textContent = '⏳ Debating...';
  btn.disabled = true;
  panel.style.display = 'block';
  panel.innerHTML = `
    <div class="ai-skeleton">
      <div class="debate-loading">
        <div class="agent-loading bull-loading">🐂 Bull building case...</div>
        <div class="agent-loading bear-loading">🐻 Bear building case...</div>
        <div class="agent-loading judge-loading">⚖️ Judge deliberating...</div>
      </div>
      <div class="skel-header" style="margin-top:1rem;"><div class="skel-badge"></div><div class="skel-badge" style="width:60px"></div></div>
      <div class="skel-line m"></div><div class="skel-line l"></div><div class="skel-line s"></div>
    </div>`;

  // Animate the loading agents sequentially
  const bullEl  = panel.querySelector('.bull-loading');
  const bearEl  = panel.querySelector('.bear-loading');
  const judgeEl = panel.querySelector('.judge-loading');
  setTimeout(() => bullEl  && (bullEl.classList.add('agent-done')),  3000);
  setTimeout(() => bearEl  && (bearEl.classList.add('agent-done')),  5000);
  setTimeout(() => judgeEl && (judgeEl.classList.add('agent-active')), 5500);

  try {
    const r = await fetch('/api/analyze-market', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cache_key: cacheKey, question, yes_price: yesPrice,
                             market_slug: marketSlug, end_date: endDate }),
    });
    let data;
    try { data = await r.json(); } catch (_) {
      panel.innerHTML = `<div class="ai-error">⚠ Server returned non-JSON.</div>`;
      btn.textContent = '🤖 Analyze Market'; btn.disabled = false; return;
    }
    if (!r.ok) {
      panel.innerHTML = `<div class="ai-error">⚠ HTTP ${r.status}: ${data?.detail || JSON.stringify(data)}</div>`;
      btn.textContent = '🤖 Analyze Market'; btn.disabled = false; return;
    }
    const res = data.result || {};
    if (res.error) {
      panel.innerHTML = `<div class="ai-error">⚠ ${res.error}</div>`;
      btn.textContent = '🤖 Analyze Market'; btn.disabled = false; return;
    }
    btn.textContent = '✅ Analyzed';
    renderAIPanel(panel, res, yesPrice, polyUrl, data.from_cache);
  } catch (e) {
    panel.innerHTML = `<div class="ai-error">⚠ Network error: ${e.message}</div>`;
    btn.textContent = '🤖 Analyze Market'; btn.disabled = false;
  }
}

function renderAIPanel(panel, res, yesPrice, polyUrl, fromCache) {
  const fv      = res.fair_value ?? yesPrice;
  const conf    = (res.confidence || 'low').toLowerCase();
  const verdict = res.verdict || 'SKIP';
  const edgePct = res.edge_pct ?? ((fv - yesPrice) * 100);
  const sbImp   = res.sportsbook_implied;
  const lvl     = conf === 'high' ? 3 : conf === 'medium' ? 2 : 1;
  const dots    = [1,2,3].map(i => `<div class="conf-dot ${i<=lvl ? 'on-'+conf : ''}"></div>`).join('');
  const verdictMap = { BUY_YES:'🟢 BUY YES', BUY_NO:'🔴 BUY NO', FAIR:'⚪ FAIR', SKIP:'⏭ SKIP' };
  const edgeClass  = edgePct > 0.5 ? 'pos' : edgePct < -0.5 ? 'neg' : 'neu';
  const edgeStr    = `${edgePct > 0 ? '+' : ''}${edgePct.toFixed(1)}¢`;
  const barFill    = Math.round(fv * 100);
  const tickLeft   = Math.round(yesPrice * 100);
  const barClass   = fv >= yesPrice ? 'bull' : 'bear';
  const isDebate   = res.debate_mode === true;

  const compareHTML = `<div class="compare-row">
    <div class="compare-item"><span class="compare-lbl">Market (YES)</span><span class="compare-val market">${(yesPrice*100).toFixed(0)}¢</span></div>
    <div class="compare-item"><span class="compare-lbl">AI Fair Value</span><span class="compare-val ai">${(fv*100).toFixed(0)}¢</span></div>
    ${sbImp != null ? `<div class="compare-item"><span class="compare-lbl">Sportsbooks</span><span class="compare-val sb">${(sbImp*100).toFixed(0)}¢</span></div>` : ''}
    ${isDebate && res.bull_prob != null ? `<div class="compare-item"><span class="compare-lbl">🐂 Bull</span><span class="compare-val" style="color:var(--accent)">${res.bull_prob}%</span></div>` : ''}
    ${isDebate && res.bear_prob != null ? `<div class="compare-item"><span class="compare-lbl">🐻 Bear</span><span class="compare-val" style="color:var(--danger)">${res.bear_prob}%</span></div>` : ''}
  </div>`;

  // Multi-agent debate summary block
  const debateHTML = isDebate && (res.bull_summary || res.bear_summary) ? `
    <div class="debate-summary">
      ${res.bull_summary ? `<div class="debate-side bull-side"><span class="debate-label">🐂 Bull</span><span class="debate-text">${res.bull_summary}</span></div>` : ''}
      ${res.bear_summary ? `<div class="debate-side bear-side"><span class="debate-label">🐻 Bear</span><span class="debate-text">${res.bear_summary}</span></div>` : ''}
      <div class="debate-divider">⚖️ Judge's Verdict</div>
    </div>` : '';

  const factsHTML = (res.key_facts || []).map(f => `<div class="fact-item"><span class="fact-dot">→</span>${f}</div>`).join('');

  panel.innerHTML = `<div class="ai-loaded">
    <div class="ai-header">
      <span class="ai-label">AI Analysis</span>
      ${isDebate ? '<span class="debate-badge">⚔️ Debate Mode</span>' : ''}
      <span class="verdict-pill verdict-${verdict}">${verdictMap[verdict] || verdict}</span>
      <span class="edge-chip ${edgeClass}">${edgeStr} edge</span>
      <div class="conf-wrap"><span class="ai-label">Confidence</span><div class="conf-dots">${dots}</div></div>
      ${fromCache ? '<span class="cache-badge">● cached</span>' : ''}
    </div>
    <div class="prob-section">
      <div class="prob-row-labels"><span>0%</span><span>AI fair value — ${(fv*100).toFixed(0)}% YES</span><span>100%</span></div>
      <div class="prob-track"><div class="prob-fill ${barClass}" style="width:${barFill}%"></div><div class="market-marker" style="left:${tickLeft}%"></div></div>
    </div>
    ${compareHTML}
    ${debateHTML}
    <div class="ai-reasoning">${res.reasoning || ''}</div>
    ${factsHTML ? `<div class="key-facts">${factsHTML}</div>` : ''}
  </div>`;
}


/* ═══════════════════════════════════════════════════════════
   FEATURE 1: PERFORMANCE / TRACK RECORD DASHBOARD
   ═══════════════════════════════════════════════════════════ */

async function loadPerformance() {
  // Reset to loading state
  ['kpi-winrate','kpi-roi','kpi-resolved','kpi-pending'].forEach(id => {
    document.getElementById(id).querySelector('.kpi-val').textContent = '...';
  });
  document.getElementById('perf-list').innerHTML =
    '<div class="loading-state"><div class="spinner"></div><div class="loading-text">Loading track record...</div></div>';

  try {
    const res  = await fetch('/api/predictions/stats');
    const data = await res.json();
    renderPerformance(data);
  } catch (e) {
    document.getElementById('perf-list').innerHTML =
      `<div class="ai-error">⚠ Failed to load performance data: ${e.message}</div>`;
  }
}

function renderPerformance(data) {
  // KPI Cards
  const wr = data.win_rate != null ? `${data.win_rate}%` : '--';
  const roi = data.roi_pct != null
    ? `${data.roi_pct > 0 ? '+' : ''}${data.roi_pct}%`
    : '--';

  setKpi('kpi-winrate',  wr,   data.win_rate  != null ? (data.win_rate  >= 55 ? 'good' : data.win_rate >= 45 ? 'neutral' : 'bad') : '');
  setKpi('kpi-roi',      roi,  data.roi_pct   != null ? (data.roi_pct   > 0  ? 'good' : 'bad') : '');
  setKpi('kpi-resolved', data.resolved ?? '--', '');
  setKpi('kpi-pending',  data.unresolved ?? '--', '');

  document.getElementById('perf-total-badge').textContent = `${data.total || 0} total AI calls`;

  // Draw chart
  if (data.chart_data && data.chart_data.length > 1) {
    drawWinRateChart(data.chart_data);
  } else {
    document.getElementById('perf-chart').style.display = 'none';
  }

  // Recent predictions list
  const list = document.getElementById('perf-list');
  if (!data.recent || data.recent.length === 0) {
    list.innerHTML = `<div class="no-results"><div class="no-results-text">No AI predictions recorded yet. Start analyzing markets to build the track record.</div></div>`;
    return;
  }

  list.innerHTML = data.recent.map(p => {
    const statusIcon = p.resolved
      ? (p.won ? '✅' : '❌')
      : '⏳';
    const statusLabel = p.resolved
      ? (p.won ? 'Won' : 'Lost')
      : 'Pending';
    const statusClass = p.resolved
      ? (p.won ? 'pred-won' : 'pred-lost')
      : 'pred-pending';
    const verdictClass = p.verdict === 'BUY_YES' ? 'edge' : 'value';
    const verdictLabel = p.verdict === 'BUY_YES' ? '🟢 BUY YES' : '🔴 BUY NO';
    const edgeStr = p.edge_pct != null
      ? `${p.edge_pct > 0 ? '+' : ''}${p.edge_pct.toFixed(1)}¢`
      : '';
    const resolveInfo = p.resolved && p.resolve_price != null
      ? `<span style="font-size:0.75rem; color:var(--muted);">Settled @ ${(p.resolve_price*100).toFixed(0)}¢</span>`
      : '';

    return `
      <div class="pred-card ${statusClass}">
        <div class="pred-status">${statusIcon}</div>
        <div class="pred-body">
          <div class="pred-question">${p.question || 'Unknown market'}</div>
          <div class="pred-meta">
            <span class="card-tag ${verdictClass}">${verdictLabel}</span>
            <span class="card-tag" style="background:rgba(90,97,128,.12); color:var(--muted); border:1px solid var(--border);">
              Entry: ${p.yes_price != null ? (p.yes_price*100).toFixed(0)+'¢ YES' : '--'}
            </span>
            ${edgeStr ? `<span class="card-tag" style="background:rgba(0,176,255,.1); color:var(--accent2); border:1px solid rgba(0,176,255,.2);">${edgeStr} edge</span>` : ''}
            <span class="card-tag conf-${p.confidence || 'low'}" style="font-size:0.65rem;">${(p.confidence || 'low').toUpperCase()}</span>
            ${resolveInfo}
          </div>
        </div>
        <div class="pred-result ${statusClass}">${statusLabel}</div>
      </div>`;
  }).join('');
}

function setKpi(id, value, quality) {
  const el = document.getElementById(id);
  const valEl = el.querySelector('.kpi-val');
  valEl.textContent = value;
  el.className = `kpi-card ${quality ? 'kpi-' + quality : ''}`;
}

function drawWinRateChart(chartData) {
  const canvas  = document.getElementById('perf-chart');
  canvas.style.display = 'block';
  const ctx     = canvas.getContext('2d');
  const W       = canvas.offsetWidth || 800;
  const H       = 180;
  canvas.width  = W;
  canvas.height = H;

  const pad   = { top: 20, right: 20, bottom: 30, left: 40 };
  const inner = { w: W - pad.left - pad.right, h: H - pad.top - pad.bottom };
  const n     = chartData.length;

  ctx.clearRect(0, 0, W, H);

  // Grid lines at 25%, 50%, 75%
  ctx.strokeStyle = '#1f2433';
  ctx.lineWidth   = 1;
  [25, 50, 75].forEach(pct => {
    const y = pad.top + inner.h - (pct / 100) * inner.h;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(pad.left + inner.w, y);
    ctx.stroke();
    ctx.fillStyle = '#5a6180';
    ctx.font = '10px DM Mono, monospace';
    ctx.fillText(pct + '%', 4, y + 4);
  });

  // 50% reference line (break-even)
  const y50 = pad.top + inner.h - 0.5 * inner.h;
  ctx.strokeStyle = 'rgba(255,179,0,0.4)';
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(pad.left, y50);
  ctx.lineTo(pad.left + inner.w, y50);
  ctx.stroke();
  ctx.setLineDash([]);

  // Win-rate line
  ctx.strokeStyle = '#00e676';
  ctx.lineWidth   = 2;
  ctx.beginPath();
  chartData.forEach((pt, i) => {
    const x = pad.left + (i / (n - 1)) * inner.w;
    const y = pad.top + inner.h - (pt.win_rate / 100) * inner.h;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Dots for each resolved call (green = won, red = lost)
  chartData.forEach((pt, i) => {
    const x = pad.left + (i / (n - 1)) * inner.w;
    const y = pad.top + inner.h - (pt.win_rate / 100) * inner.h;
    ctx.beginPath();
    ctx.arc(x, y, 3.5, 0, Math.PI * 2);
    ctx.fillStyle = pt.won ? '#00e676' : '#ff4444';
    ctx.fill();
  });

  // X-axis labels
  ctx.fillStyle = '#5a6180';
  ctx.font = '10px DM Mono, monospace';
  ctx.textAlign = 'center';
  [0, Math.floor(n/2), n-1].forEach(i => {
    if (chartData[i]) {
      const x = pad.left + (i / (n - 1)) * inner.w;
      ctx.fillText(`#${chartData[i].n}`, x, H - 6);
    }
  });
}