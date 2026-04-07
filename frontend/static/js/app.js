const S = {
  allMarkets: [], activeSport: 'all', activeEdge: 'all',
  userEmail: localStorage.getItem('eb_email') || null,
  sessionId: localStorage.getItem('eb_session') || null
};

let currentAlertMarket = null;

window.onload = async () => {
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');
    const email = urlParams.get('email');

    if (token && email) {
        try {
            const res = await fetch('/api/auth/verify', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, token })
            });
            if (res.ok) {
                const data = await res.json();
                localStorage.setItem('eb_session', data.session_id);
                localStorage.setItem('eb_email', data.email);
                S.sessionId = data.session_id;
                S.userEmail = data.email;
                window.history.replaceState({}, document.title, "/"); 
            }
        } catch (e) {
            console.error("Verification failed", e);
        }
    }

    if (S.sessionId) {
        startApp();
    } else {
        showScreen('screen-landing');
    }
    loadLandingStats();
};

async function handleAuth(type) {
    const email = document.getElementById('auth-email').value;
    const msg = document.getElementById('auth-msg');
    const inBtn = document.getElementById('signin-btn');
    const upBtn = document.getElementById('signup-btn');
    
    if (!email) return;

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
        
        if (!res.ok) {
            throw new Error(data.detail || "Server error");
        }
        
        msg.style.color = "var(--accent)";
        msg.textContent = "Magic link sent! Check your inbox.";
    } catch (e) {
        msg.style.color = "var(--danger)";
        msg.textContent = e.message;
    } finally {
        inBtn.disabled = false;
        upBtn.disabled = false;
    }
}

function signOut() {
    localStorage.removeItem('eb_session');
    localStorage.removeItem('eb_email');
    S.sessionId = null;
    S.userEmail = null;
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
  document.getElementById('user-display').textContent = S.userEmail || "";
  loadMarkets();
  updateAlertCountBadge();
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
  } catch (e) {
    console.error(e);
  }
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
  card.dataset.cacheKey = m.cache_key || '';
  card.dataset.question = m.question;
  card.dataset.yesPrice = m.yes_price;
  card.dataset.polyUrl  = m.poly_url || 'https://polymarket.com';

  card.innerHTML = `
    <div class="card-main">
      <div class="card-top">
        <span class="card-tag ${tType}">${tLabel}</span>
        <div class="card-question">${m.question}</div>
      </div>
      <div class="card-meta">
        <div class="price-block">
          <span class="price-label">YES</span>
          <span class="price-val yes">${(m.yes_price * 100).toFixed(0)}¢</span>
        </div>
        <div class="price-block">
          <span class="price-label">NO</span>
          <span class="price-val no">${((1 - m.yes_price) * 100).toFixed(0)}¢</span>
        </div>
        <div class="edge-badge ${eClass}">${eTxt} edge</div>
      </div>
      <div class="card-actions" style="margin-top:.9rem">
        <button class="action-btn yes-btn" onclick="window.open('${m.poly_url || 'https://polymarket.com'}','_blank')">Bet YES ↗</button>
        <button class="action-btn no-btn" onclick="window.open('${m.poly_url || 'https://polymarket.com'}','_blank')">Bet NO ↗</button>
        <button class="action-btn poly-btn" onclick="window.open('${m.poly_url || 'https://polymarket.com'}','_blank')">Polymarket ↗</button>
        <button class="action-btn poly-btn alert-btn">🔔 Alert</button>
        <button class="action-btn ai-btn">🤖 Analyze Market</button>
      </div>
    </div>
    <div class="ai-panel" style="display:none;"></div>`;

  card.querySelector('.ai-btn').addEventListener('click', () => triggerAnalysis(card));
  
  card.querySelector('.alert-btn').addEventListener('click', () => {
    currentAlertMarket = m;
    document.getElementById('alert-q').textContent = m.question;
    document.getElementById('alert-error').textContent = "";
    document.getElementById('alert-modal').style.display = "flex";
  });

  return card;
}

/* --- ALERT MANAGEMENT --- */

function closeAlertModal() {
    document.getElementById('alert-modal').style.display = "none";
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
                    <div style="font-size:0.85rem; margin-bottom:0.4rem; font-weight:600;">${a.question}</div>
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
        if(res.ok) {
            openManageAlerts(); // Refresh the list UI
            updateAlertCountBadge(); // Refresh the counter in header
        }
    } catch(e) {
        alert("Failed to delete alert. Please try again.");
    }
}

/* --- AI ANALYSIS --- */

async function triggerAnalysis(card) {
  const btn = card.querySelector('.ai-btn');
  const panel = card.querySelector('.ai-panel');
  const cacheKey = card.dataset.cacheKey;
  const question = card.dataset.question;
  const yesPrice = parseFloat(card.dataset.yesPrice);
  const polyUrl = card.dataset.polyUrl;

  btn.textContent = '⏳ Researching...';
  btn.disabled = true;
  panel.style.display = 'block';
  panel.innerHTML = `<div class="ai-skeleton"><div class="skel-header"><div class="skel-badge"></div><div class="skel-badge" style="width:60px"></div></div><div class="skel-line m"></div><div class="skel-line l"></div><div class="skel-line s"></div></div>`;

  try {
    const r = await fetch('/api/analyze-market', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cache_key: cacheKey, question, yes_price: yesPrice }),
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
  const fv = res.fair_value ?? yesPrice;
  const conf = (res.confidence || 'low').toLowerCase();
  const verdict = res.verdict || 'SKIP';
  const edgePct = res.edge_pct ?? ((fv - yesPrice) * 100);
  const sbImp = res.sportsbook_implied;
  const lvl = conf === 'high' ? 3 : conf === 'medium' ? 2 : 1;
  const dots = [1, 2, 3].map(i => `<div class="conf-dot ${i <= lvl ? 'on-' + conf : ''}"></div>`).join('');
  const verdictMap = { BUY_YES:'🟢 BUY YES', BUY_NO:'🔴 BUY NO', FAIR:'⚪ FAIR', SKIP:'⏭ SKIP' };
  const edgeClass = edgePct > 0.5 ? 'pos' : edgePct < -0.5 ? 'neg' : 'neu';
  const edgeStr = `${edgePct > 0 ? '+' : ''}${edgePct.toFixed(1)}¢`;
  const barFill = Math.round(fv * 100);
  const tickLeft = Math.round(yesPrice * 100);
  const barClass = fv >= yesPrice ? 'bull' : 'bear';

  const compareHTML = `<div class="compare-row">
      <div class="compare-item"><span class="compare-lbl">Market (YES)</span><span class="compare-val market">${(yesPrice * 100).toFixed(0)}¢</span></div>
      <div class="compare-item"><span class="compare-lbl">AI Fair Value</span><span class="compare-val ai">${(fv * 100).toFixed(0)}¢</span></div>
      ${sbImp != null ? `<div class="compare-item"><span class="compare-lbl">Sportsbooks</span><span class="compare-val sb">${(sbImp * 100).toFixed(0)}¢</span></div>` : ''}
    </div>`;
  const factsHTML = (res.key_facts || []).map(f => `<div class="fact-item"><span class="fact-dot">→</span>${f}</div>`).join('');

  panel.innerHTML = `<div class="ai-loaded">
      <div class="ai-header">
        <span class="ai-label">AI Analysis</span>
        <span class="verdict-pill verdict-${verdict}">${verdictMap[verdict] || verdict}</span>
        <span class="edge-chip ${edgeClass}">${edgeStr} edge</span>
        <div class="conf-wrap"><span class="ai-label">Confidence</span><div class="conf-dots">${dots}</div></div>
        ${fromCache ? '<span class="cache-badge">● cached</span>' : ''}
      </div>
      <div class="prob-section">
        <div class="prob-row-labels"><span>0%</span><span>AI fair value — ${(fv * 100).toFixed(0)}% YES</span><span>100%</span></div>
        <div class="prob-track"><div class="prob-fill ${barClass}" style="width:${barFill}%"></div><div class="market-marker" style="left:${tickLeft}%"></div></div>
      </div>
      ${compareHTML}
      <div class="ai-reasoning">${res.reasoning || ''}</div>
      ${factsHTML ? `<div class="key-facts">${factsHTML}</div>` : ''}
    </div>`;
}