// ─── State ────────────────────────────────────────────────────────────────────
const S = {
  allMarkets: [], activeSport: 'all', activeEdge: 'all',
};

// ─── Boot ─────────────────────────────────────────────────────────────────────
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
  loadMarkets();
}

// ─── Markets ──────────────────────────────────────────────────────────────────
async function loadMarkets() {
  document.getElementById('markets-list').innerHTML =
    '<div class="loading-state"><div class="spinner"></div><div class="loading-text">Scanning sports markets...</div></div>';

  try {
    const r    = await fetch('/api/markets', {
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
    renderDemoData();
  }
}

// ─── Filters ──────────────────────────────────────────────────────────────────
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
  document.querySelectorAll('.subfilter-chip').forEach(c =>
    c.classList.remove('active-all', 'active-yes', 'active-no')
  );
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

// ─── Cards ────────────────────────────────────────────────────────────────────
function renderMarkets(markets) {
  const list = document.getElementById('markets-list');
  document.getElementById('market-count').textContent = `${markets.length} markets`;
  if (!markets.length) {
    list.innerHTML = `<div class="no-results"><div class="no-results-icon">🔍</div><div class="no-results-text">No markets match your filters.<br>Try adjusting the sport or edge direction.</div></div>`;
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

  card.className        = `market-card ${(m.edge || 0) > 0 ? 'underpriced' : 'value'}`;
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
        <button class="action-btn ai-btn">🤖 Analyze Market</button>
      </div>
    </div>
    <div class="ai-panel" style="display:none;"></div>`;

  card.querySelector('.ai-btn').addEventListener('click', () => triggerAnalysis(card));
  return card;
}

// ─── AI analysis ──────────────────────────────────────────────────────────────
async function triggerAnalysis(card) {
  const btn      = card.querySelector('.ai-btn');
  const panel    = card.querySelector('.ai-panel');
  const cacheKey = card.dataset.cacheKey;
  const question = card.dataset.question;
  const yesPrice = parseFloat(card.dataset.yesPrice);
  const polyUrl  = card.dataset.polyUrl;

  btn.textContent     = '⏳ Researching...';
  btn.disabled        = true;
  panel.style.display = 'block';
  panel.innerHTML = `
    <div class="ai-skeleton">
      <div class="skel-header"><div class="skel-badge"></div><div class="skel-badge" style="width:60px"></div></div>
      <div class="skel-line m"></div>
      <div class="skel-line l"></div>
      <div class="skel-line s"></div>
    </div>`;

  try {
    const r = await fetch('/api/analyze-market', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cache_key: cacheKey, question, yes_price: yesPrice }),
    });

    let data;
    try { data = await r.json(); }
    catch (_) {
      panel.innerHTML = `<div class="ai-error">⚠ Server returned non-JSON (HTTP ${r.status}). Check uvicorn logs.</div>`;
      btn.textContent = '🤖 Analyze Market'; btn.disabled = false; return;
    }

    if (!r.ok) {
      panel.innerHTML = `<div class="ai-error">⚠ HTTP ${r.status}: ${data?.detail || JSON.stringify(data)}</div>`;
      btn.textContent = '🤖 Analyze Market'; btn.disabled = false; return;
    }

    const res = data.result || {};
    if (res.error) {
      panel.innerHTML = `<div class="ai-error">⚠ ${res.error}<br><small style="opacity:.6">Visit <code>/api/debug</code> to diagnose.</small></div>`;
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
  const isPos   = edgePct > 0.5;
  const isNeg   = edgePct < -0.5;
  const sbImp   = res.sportsbook_implied;

  const lvl  = conf === 'high' ? 3 : conf === 'medium' ? 2 : 1;
  const dots = [1, 2, 3].map(i =>
    `<div class="conf-dot ${i <= lvl ? 'on-' + conf : ''}"></div>`
  ).join('');

  const verdictMap   = { BUY_YES:'🟢 BUY YES', BUY_NO:'🔴 BUY NO', FAIR:'⚪ FAIR', SKIP:'⏭ SKIP' };
  const verdictLabel = verdictMap[verdict] || verdict;
  const edgeClass    = isPos ? 'pos' : isNeg ? 'neg' : 'neu';
  const edgeStr      = `${edgePct > 0 ? '+' : ''}${edgePct.toFixed(1)}¢`;
  const barFill      = Math.round(fv * 100);
  const tickLeft     = Math.round(yesPrice * 100);
  const barClass     = fv >= yesPrice ? 'bull' : 'bear';

  const compareHTML = `
    <div class="compare-row">
      <div class="compare-item">
        <span class="compare-lbl">Market (YES)</span>
        <span class="compare-val market">${(yesPrice * 100).toFixed(0)}¢</span>
      </div>
      <div class="compare-item">
        <span class="compare-lbl">AI Fair Value</span>
        <span class="compare-val ai">${(fv * 100).toFixed(0)}¢</span>
      </div>
      ${sbImp != null
        ? `<div class="compare-item"><span class="compare-lbl">Sportsbooks</span><span class="compare-val sb">${(sbImp * 100).toFixed(0)}¢</span></div>`
        : ''}
    </div>`;

  const factsHTML = (res.key_facts || [])
    .map(f => `<div class="fact-item"><span class="fact-dot">→</span>${f}</div>`)
    .join('');

  panel.innerHTML = `
    <div class="ai-loaded">
      <div class="ai-header">
        <span class="ai-label">AI Analysis</span>
        <span class="verdict-pill verdict-${verdict}">${verdictLabel}</span>
        <span class="edge-chip ${edgeClass}">${edgeStr} edge</span>
        <div class="conf-wrap">
          <span class="ai-label">Confidence</span>
          <div class="conf-dots">${dots}</div>
        </div>
        ${fromCache ? '<span class="cache-badge">● cached</span>' : ''}
      </div>
      <div class="prob-section">
        <div class="prob-row-labels">
          <span>0%</span>
          <span>AI fair value — ${(fv * 100).toFixed(0)}% YES</span>
          <span>100%</span>
        </div>
        <div class="prob-track">
          <div class="prob-fill ${barClass}" style="width:${barFill}%"></div>
          <div class="market-marker" style="left:${tickLeft}%"></div>
        </div>
      </div>
      ${compareHTML}
      <div class="ai-reasoning">${res.reasoning || ''}</div>
      ${factsHTML ? `<div class="key-facts">${factsHTML}</div>` : ''}
      <div class="card-actions">
        <button class="action-btn yes-btn"  onclick="window.open('${polyUrl}','_blank')">Bet YES ↗</button>
        <button class="action-btn no-btn"   onclick="window.open('${polyUrl}','_blank')">Bet NO ↗</button>
        <button class="action-btn poly-btn" onclick="window.open('${polyUrl}','_blank')">Polymarket ↗</button>
      </div>
    </div>`;
}

// ─── Demo fallback ─────────────────────────────────────────────────────────────
function renderDemoData() {
  const demo = [
    { question:"Will Bayern Munich advance past Atalanta in CL quarters?", yes_price:.60, edge:.22, category:'Soccer', poly_url:'https://polymarket.com', cache_key:'demo1' },
    { question:"Will the Lakers win their next game?",                      yes_price:.55, edge:-.08, category:'NBA',   poly_url:'https://polymarket.com', cache_key:'demo2' },
    { question:"Will Djokovic win the French Open 2026?",                   yes_price:.31, edge:.09, category:'Tennis', poly_url:'https://polymarket.com', cache_key:'demo3' },
  ];
  S.allMarkets = demo;
  renderSportFilters();
  applyFilters();
  document.getElementById('market-count').textContent = `${demo.length} markets (demo)`;
}

// ─── Init ──────────────────────────────────────────────────────────────────────
loadLandingStats();