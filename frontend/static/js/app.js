const S = {
  allMarkets: [], activeSport: 'all', activeEdge: 'all',
  userEmail: localStorage.getItem('eb_email') || null,
  sessionId: localStorage.getItem('eb_session') || null,
  isGuest: localStorage.getItem('eb_guest') === 'true'
};

let currentAlertMarket = null;

window.onload = async () => {
    const urlParams = new URLSearchParams(window.location.search);
    const sid   = urlParams.get('session_id');
    const email = urlParams.get('email');
    const error = urlParams.get('error');

    if (sid && email) {
        localStorage.setItem('eb_session', sid);
        localStorage.setItem('eb_email', email);
        localStorage.removeItem('eb_guest');
        S.sessionId = sid;
        S.userEmail = email;
        S.isGuest = false;
        window.history.replaceState({}, document.title, '/');
    } else if (error) {
        alert(error === 'expired_token' ? 'Link expired. Please request a new one.' : 'Invalid login link.');
        window.history.replaceState({}, document.title, '/');
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
    const msg   = document.getElementById('auth-msg');
    const inBtn = document.getElementById('signin-btn');
    const upBtn = document.getElementById('signup-btn');
    const email = emailInput.value.trim();

    if (!email) {
        msg.style.color = 'var(--danger)';
        msg.textContent = 'Please enter an email address.';
        return;
    }

    inBtn.disabled = upBtn.disabled = true;
    msg.style.color = 'var(--accent)';
    msg.textContent = 'Sending...';

    try {
        const res  = await fetch(type === 'in' ? '/api/auth/sign-in' : '/api/auth/sign-up', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Server error');
        msg.style.color = 'var(--accent)';
        msg.textContent = 'Authorization link sent! Check your inbox.';
    } catch (e) {
        msg.style.color = 'var(--danger)';
        msg.textContent = e.message;
    } finally {
        inBtn.disabled = upBtn.disabled = false;
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
    S.sessionId = S.userEmail = null;
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
        document.getElementById('live-count').textContent =
            typeof d.open_markets === 'number' ? d.open_markets.toLocaleString() : d.open_markets;
    } catch {
        document.getElementById('live-count').textContent = '500+';
    }
}

function startApp() {
    showScreen('screen-markets');
    if (S.isGuest) {
        document.getElementById('user-display').textContent = 'Guest';
        document.getElementById('auth-action-btn').textContent = '[Sign In]';
        document.getElementById('header-alerts-btn').style.display = 'none';
    } else {
        document.getElementById('user-display').textContent = S.userEmail;
        document.getElementById('auth-action-btn').textContent = '[Sign Out]';
        document.getElementById('header-alerts-btn').style.display = 'block';
        updateAlertCountBadge();
    }
    loadMarkets();
}

/* ── Markets ─────────────────────────────────────────────── */

async function loadMarkets() {
    document.getElementById('markets-list').innerHTML =
        '<div class="loading-state"><div class="spinner"></div><div class="loading-text">Scanning sports markets...</div></div>';
    try {
        const r    = await fetch('/api/markets', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile: {} })
        });
        const data = await r.json();
        S.allMarkets = data.markets || [];
        renderSportFilters();
        applyFilters();
    } catch (e) {}
}

const SPORT_ICONS = {
    'all': '⚡', 'NFL': '🏈', 'NBA': '🏀', 'MLB': '⚾', 'NHL': '🏒', 'Soccer': '⚽',
    'Tennis': '🎾', 'UFC / MMA': '🥊', 'Golf': '⛳', 'Racing': '🏎',
    'Olympics': '🏅', 'Rugby / Cricket': '🏉', 'College Sports': '🎓', 'Other Sports': '🏆'
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
        list.innerHTML = '<div class="no-results"><div class="no-results-text">No markets match your filters.</div></div>';
        return;
    }
    list.innerHTML = '';
    markets.forEach(m => list.appendChild(buildCard(m)));
}

function buildCard(m) {
    const card   = document.createElement('div');
    const eClass = (m.edge || 0) > 0 ? 'positive' : 'negative';
    const tType  = (m.edge || 0) > 0 ? 'edge' : 'value';
    const tLabel = (m.edge || 0) > 0 ? '📈 EDGE' : '🎯 VALUE';
    const eTxt   = m.edge != null ? `${m.edge > 0 ? '+' : ''}${(m.edge * 100).toFixed(1)}¢` : '—';

    card.id        = m.market_slug ? `market-${m.market_slug}` : '';
    card.className = 'market-card';
    card.dataset.cacheKey   = m.cache_key || '';
    card.dataset.question   = m.question;
    card.dataset.yesPrice   = m.yes_price;
    card.dataset.polyUrl    = m.poly_url || 'https://polymarket.com';
    card.dataset.marketSlug = m.market_slug || '';

    card.innerHTML = `
        <div class="card-body">
          <div class="card-top">
            <span class="card-tag ${tType}">${tLabel}</span>
            <span class="card-tag" style="background:rgba(90,97,128,.12);color:var(--muted);border:1px solid var(--border);">${m.category || ''}</span>
          </div>
          <div class="card-question">${m.question}</div>
          <div class="card-meta">
            <div class="price-block">
              <span class="price-label">YES</span>
              <span class="price-val yes">${(m.yes_price * 100).toFixed(0)}¢</span>
            </div>
            <div class="price-block">
              <span class="price-label">NO</span>
              <span class="price-val no">${((1 - m.yes_price) * 100).toFixed(0)}¢</span>
            </div>
            ${m.end_date ? `<div class="price-block"><span class="price-label">Ends</span><span class="price-val" style="font-size:.85rem;">${m.end_date}</span></div>` : ''}
            <div class="edge-badge ${eClass}">${eTxt} edge</div>
          </div>
          <div class="card-actions">
            <button class="action-btn ai-btn">🤖 Analyze Market</button>
            <a href="${m.poly_url || 'https://polymarket.com'}" target="_blank">
              <button class="action-btn poly-btn">Polymarket ↗</button>
            </a>
            ${!S.isGuest ? '<button class="action-btn poly-btn alert-btn">🔔 Set Alert</button>' : ''}
          </div>
        </div>
        <div class="ai-panel" style="display:none;"></div>`;

    card.querySelector('.ai-btn').addEventListener('click', () => triggerAnalysis(card));

    if (!S.isGuest) {
        card.querySelector('.alert-btn').addEventListener('click', () => openAlertModal(m));
    }

    return card;
}

/* ── Alert Modal ─────────────────────────────────────────── */

function openAlertModal(m) {
    currentAlertMarket = m;
    document.getElementById('alert-q').textContent     = m.question;
    document.getElementById('alert-error').textContent = '';
    document.getElementById('alert-price').value       = 50;
    document.getElementById('alert-side').value        = 'YES';
    document.getElementById('alert-direction').value   = 'above';
    document.getElementById('alert-modal').style.display = 'flex';
    updateDirectionHint();
}

function closeAlertModal() {
    document.getElementById('alert-modal').style.display = 'none';
    currentAlertMarket = null;
}

function updateDirectionHint() {
    const side      = document.getElementById('alert-side').value;
    const direction = document.getElementById('alert-direction').value;
    const price     = document.getElementById('alert-price').value;
    const hint      = document.getElementById('alert-direction-hint');
    if (!hint) return;
    hint.textContent = direction === 'above'
        ? `Alert fires when ${side} price rises to ${price}c or higher.`
        : `Alert fires when ${side} price drops to ${price}c or lower.`;
}

async function saveAlert() {
    const sideEl      = document.getElementById('alert-side');
    const directionEl = document.getElementById('alert-direction');
    const priceEl     = document.getElementById('alert-price');
    const errorEl     = document.getElementById('alert-error');

    const side      = sideEl.value;
    const direction = directionEl.value;
    const price     = parseInt(priceEl.value) / 100;

    if (direction !== 'above' && direction !== 'below') {
        errorEl.textContent = 'Invalid direction. Please reload the page and try again.';
        return;
    }

    const payload = {
        user_email:       S.userEmail,
        market_slug:      currentAlertMarket.market_slug,
        question:         currentAlertMarket.question,
        target_price:     price,
        target_side:      side,
        target_direction: direction
    };

    errorEl.textContent = 'Saving...';

    try {
        const res = await fetch('/api/alerts/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            const data = await res.json();
            if (Array.isArray(data.detail)) {
                errorEl.textContent = data.detail.map(e => `${e.loc?.slice(-1)[0] || ''}: ${e.msg}`).join(' | ');
            } else {
                errorEl.textContent = typeof data.detail === 'string' ? data.detail : JSON.stringify(data);
            }
            return;
        }

        closeAlertModal();
        updateAlertCountBadge();
    } catch (e) {
        errorEl.textContent = 'Network error: ' + e.message;
    }
}

async function updateAlertCountBadge() {
    if (!S.userEmail) return;
    try {
        const res  = await fetch(`/api/alerts/${S.userEmail}`);
        const data = await res.json();
        document.getElementById('alert-usage').textContent = data.alerts.length;
    } catch (e) {}
}

async function openManageAlerts() {
    document.getElementById('manage-alerts-modal').style.display = 'flex';
    const list = document.getElementById('manage-alerts-list');
    list.innerHTML = '<div class="loading-state"><div class="spinner"></div></div>';

    try {
        const res  = await fetch(`/api/alerts/${S.userEmail}`);
        const data = await res.json();
        document.getElementById('alert-usage').textContent = data.alerts.length;

        if (!data.alerts.length) {
            list.innerHTML = "<p style='color:var(--muted);text-align:center;'>You have no active alerts.</p>";
            return;
        }

        list.innerHTML = data.alerts.map(a => {
            const dirLabel = a.target_direction === 'above'
                ? `rises above ${(a.target_price * 100).toFixed(0)}c`
                : `drops below ${(a.target_price * 100).toFixed(0)}c`;
            const dirSymbol = a.target_direction === 'above' ? '↑' : '↓';
            return `
            <div style="background:var(--surface2);padding:1rem;border-radius:8px;display:flex;justify-content:space-between;align-items:center;border:1px solid var(--border);">
              <div style="padding-right:1rem;">
                <div style="font-size:0.85rem;margin-bottom:0.4rem;font-weight:600;">
                  <a href="javascript:void(0)" onclick="scrollToMarket('${a.market_slug}')"
                     style="color:var(--text);text-decoration:none;border-bottom:1px dashed var(--muted);">
                    ${a.question}
                  </a>
                </div>
                <span class="card-tag edge" style="font-size:0.7rem;">
                  ${dirSymbol} ${a.target_side} ${dirLabel}
                </span>
              </div>
              <button class="action-btn no-btn" onclick="deleteAlert('${a._id}')" style="flex-shrink:0;">Remove</button>
            </div>`;
        }).join('');
    } catch (e) {
        list.innerHTML = "<p style='color:var(--danger);'>Failed to load alerts.</p>";
    }
}

async function deleteAlert(id) {
    try {
        const res = await fetch(`/api/alerts/${id}`, { method: 'DELETE' });
        if (res.ok) { openManageAlerts(); updateAlertCountBadge(); }
    } catch (e) {
        alert('Failed to delete alert.');
    }
}

function scrollToMarket(slug) {
    document.getElementById('manage-alerts-modal').style.display = 'none';
    const allSportBtn = document.querySelector('#sport-filters .filter-chip');
    if (allSportBtn) setSportFilter('all', allSportBtn);
    const allEdgeBtn = document.querySelector('.subfilter-chip');
    if (allEdgeBtn) setEdgeFilter('all', allEdgeBtn);
    setTimeout(() => {
        const targetCard = document.getElementById(`market-${slug}`);
        if (targetCard) {
            targetCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
            targetCard.style.transition = 'box-shadow 0.3s ease-in-out';
            targetCard.style.boxShadow  = '0 0 0 3px var(--accent)';
            setTimeout(() => { targetCard.style.boxShadow = ''; }, 2000);
        } else {
            alert('This market is no longer active on the main page.');
        }
    }, 50);
}

/* ── AI Analysis ─────────────────────────────────────────── */

async function triggerAnalysis(card) {
    const btn      = card.querySelector('.ai-btn');
    const panel    = card.querySelector('.ai-panel');
    const cacheKey = card.dataset.cacheKey;
    const question = card.dataset.question;
    const yesPrice = parseFloat(card.dataset.yesPrice);
    const polyUrl  = card.dataset.polyUrl;

    btn.textContent  = '⏳ Researching...';
    btn.disabled     = true;
    panel.style.display = 'block';
    panel.innerHTML  = `<div class="ai-skeleton">
        <div class="skel-header"><div class="skel-badge"></div><div class="skel-badge" style="width:60px"></div></div>
        <div class="skel-line m"></div><div class="skel-line l"></div><div class="skel-line s"></div>
    </div>`;

    try {
        const r = await fetch('/api/analyze-market', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cache_key: cacheKey, question, yes_price: yesPrice })
        });
        let data;
        try { data = await r.json(); } catch (_) {
            panel.innerHTML = '<div class="ai-error">Server returned non-JSON.</div>';
            btn.textContent = '🤖 Analyze Market'; btn.disabled = false; return;
        }
        if (!r.ok) {
            panel.innerHTML = `<div class="ai-error">HTTP ${r.status}: ${data?.detail || JSON.stringify(data)}</div>`;
            btn.textContent = '🤖 Analyze Market'; btn.disabled = false; return;
        }
        const res = data.result || {};
        if (res.error) {
            panel.innerHTML = `<div class="ai-error">${res.error}</div>`;
            btn.textContent = '🤖 Analyze Market'; btn.disabled = false; return;
        }
        btn.textContent = '✅ Analyzed';
        renderAIPanel(panel, res, yesPrice, polyUrl, data.from_cache);
    } catch (e) {
        panel.innerHTML = `<div class="ai-error">Network error: ${e.message}</div>`;
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
    const dots    = [1, 2, 3].map(i => `<div class="conf-dot ${i <= lvl ? 'on-' + conf : ''}"></div>`).join('');
    const verdictMap = { BUY_YES: '🟢 BUY YES', BUY_NO: '🔴 BUY NO', FAIR: '⚪ FAIR', SKIP: '⏭ SKIP' };
    const edgeClass  = edgePct > 0.5 ? 'pos' : edgePct < -0.5 ? 'neg' : 'neu';
    const edgeStr    = `${edgePct > 0 ? '+' : ''}${edgePct.toFixed(1)}¢`;
    const barFill    = Math.round(fv * 100);
    const tickLeft   = Math.round(yesPrice * 100);
    const barClass   = fv >= yesPrice ? 'bull' : 'bear';

    const compareHTML = `
        <div class="compare-row">
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
          <div class="prob-track">
            <div class="prob-fill ${barClass}" style="width:${barFill}%"></div>
            <div class="market-marker" style="left:${tickLeft}%"></div>
          </div>
        </div>
        ${compareHTML}
        <div class="ai-reasoning">${res.reasoning || ''}</div>
        ${factsHTML ? `<div class="key-facts">${factsHTML}</div>` : ''}
    </div>`;
}

/* ── AI Track Record ─────────────────────────────────────── */

async function openTrackRecord() {
  document.getElementById('track-record-modal').style.display = 'flex';
  const list = document.getElementById('track-record-list');
  list.innerHTML = '<div class="loading-state"><div class="spinner"></div><div class="loading-text">Loading predictions...</div></div>';

  try {
      const res = await fetch('/api/predictions');
      if (!res.ok) throw new Error('Failed to fetch data');
      const data = await res.json();
      
      const preds = data.predictions || data;

      // If database is completely empty
      if (!preds || !preds.length) {
          list.innerHTML = "<p style='color:var(--muted);text-align:center;padding:2rem;'>Waiting to acquire data from closed markets...</p>";
          return;
      }

      // Split predictions into Pending and Closed arrays
      const pending = preds.filter(p => !p.resolved);
      const closed  = preds.filter(p => p.resolved);

      let html = '';

      // --- SECTION 1: CLOSED MARKETS (The actual track record) ---
      html += `<div style="margin-bottom:2rem;">
                 <h4 style="margin:0 0 0.8rem 0;color:var(--text);border-bottom:1px solid var(--border);padding-bottom:0.4rem;">
                   Closed Markets (${closed.length})
                 </h4>`;
      
      if (closed.length === 0) {
          html += `<p style="color:var(--muted);font-size:0.85rem;padding:0.5rem 0;">Waiting for analyzed markets to close to build your track record...</p>`;
      } else {
          html += `<div style="display:flex;flex-direction:column;gap:0.8rem;">` + 
                  closed.map(p => createTrackRecordCard(p)).join('') + 
                  `</div>`;
      }
      html += `</div>`;

      // --- SECTION 2: PENDING PREDICTIONS ---
      html += `<div>
                 <h4 style="margin:0 0 0.8rem 0;color:var(--text);border-bottom:1px solid var(--border);padding-bottom:0.4rem;">
                   Pending Predictions (${pending.length})
                 </h4>`;
      
      if (pending.length === 0) {
          html += `<p style="color:var(--muted);font-size:0.85rem;padding:0.5rem 0;">No pending markets.</p>`;
      } else {
          html += `<div style="display:flex;flex-direction:column;gap:0.8rem;">` + 
                  pending.map(p => createTrackRecordCard(p)).join('') + 
                  `</div>`;
      }
      html += `</div>`;

      list.innerHTML = html;
      
  } catch (e) {
      // Safe fallback if the fetch fails
      list.innerHTML = "<p style='color:var(--muted);text-align:center;padding:2rem;'>Waiting to acquire data from closed markets...</p>";
  }
}

// Helper function to build the cards cleanly
function createTrackRecordCard(p) {
  const isCorrect = p.resolved && p.won;
  let statusBadge = '<span style="color:var(--accent);font-weight:bold;">⏳ PENDING</span>';
  if (p.resolved) {
      statusBadge = isCorrect 
          ? '<span style="color:#00e676;font-weight:bold;">✅ WON</span>' 
          : '<span style="color:var(--danger);font-weight:bold;">❌ LOST</span>';
  }

  const verdictColor = p.ai_verdict === 'BUY_YES' ? '#00e676' : 'var(--danger)';
  const verdictText  = p.ai_verdict.replace('_', ' ');

  return `
  <div style="background:var(--surface2);padding:1rem;border-radius:8px;border:1px solid var(--border);">
    <div style="font-size:0.9rem;margin-bottom:0.5rem;font-weight:600;color:var(--text);">${p.question}</div>
    <div style="display:flex;justify-content:space-between;align-items:center;font-size:0.8rem;color:var(--muted);">
      <div>
        <strong style="color:${verdictColor};">${verdictText}</strong> at ${(p.entry_price * 100).toFixed(0)}¢
        <span style="margin:0 5px;">|</span>
        Fair Value: ${(p.fair_value * 100).toFixed(0)}¢
      </div>
      <div>${statusBadge}</div>
    </div>
  </div>`;
}

function closeTrackRecord() {
  document.getElementById('track-record-modal').style.display = 'none';
}