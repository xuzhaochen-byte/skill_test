const data = window.BENCHMARK_REPORT_DATA || {};
const metricOrder = ['ctr', 'cvr', 'cost', 'play_3s_ratio'];
const metricNames = { ctr: 'CTR', cvr: 'CVR', cost: 'Spend', play_3s_ratio: '3s Play Rate' };

function pct(n) { return typeof n === 'number' && Number.isFinite(n) ? `${n.toFixed(1)}` : '--'; }
function esc(x) { return String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function badgeClass(score) { if (score >= 70) return 'good'; if (score >= 40) return 'warn'; return 'bad'; }
function arr(x) { return Array.isArray(x) ? x : []; }
function addList(id, items) { document.getElementById(id).innerHTML = arr(items).map(x => `<li>${esc(x)}</li>`).join(''); }
function asUrl(url) { const text = String(url || '').trim(); return /^https?:\/\//i.test(text) ? text : ''; }
function link(url, text) { const href = asUrl(url); return href ? `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(text || href)}</a>` : esc(text || url || '--'); }
function fmtCtr(value, fallback) { const n = Number(value); return Number.isFinite(n) ? `${(n * 100).toFixed(2)}%` : (fallback || '--'); }
function fmtNum(value) { const n = Number(value); return Number.isFinite(n) ? n.toLocaleString() : '--'; }
function shortUrl(url) { try { const u = new URL(url); return `${u.hostname.replace(/^www\./, '')}${u.pathname === '/' ? '' : u.pathname}`.slice(0, 70); } catch { return String(url || '').slice(0, 70); } }
function renderPills(items) { return arr(items).slice(0, 5).map(x => `<span class="pill">${esc(x)}</span>`).join(''); }
function renderList(items) { return arr(items).length ? `<ul>${arr(items).map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '<p class="muted">--</p>'; }

function renderBenchmark() {
  const input = data.input || {};
  const cls = data.industry_classification || {};
  const summary = data.summary || {};
  const benchmark = data.benchmark || {};
  document.getElementById('title').textContent = `${input.adv_id || '--'} / ${input.country || '--'}`;
  document.getElementById('subtitle').textContent = `${cls.industry || benchmark.industry || '--'} benchmark analysis`;
  document.getElementById('overallScore').textContent = pct(summary.overall_score);
  document.getElementById('overallText').textContent = summary.overall || '';
  addList('strengths', summary.strengths);
  addList('risks', summary.risks);
  addList('recommendations', summary.recommendations);

  const advContext = data.adv_context || {};
  const facts = [
    ['ADV ID', input.adv_id],
    ['Country', input.country],
    ['Landing Page', link(input.url, shortUrl(input.url))],
    ['Matched Industry', cls.industry],
    ['Aeolus Primary/Secondary', [advContext.selected_primary_industry, advContext.selected_secondary_industry].filter(Boolean).join(' / ')],
    ['Confidence', pct((cls.confidence || 0) * 100) + '%'],
    ['Method', cls.method || 'llm'],
    ['Reason', cls.reason],
    ['Benchmark Support', benchmark.support],
  ];
  document.getElementById('inputFacts').innerHTML = facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${k === 'Landing Page' ? v : esc(v || '--')}</dd>`).join('');

  document.getElementById('metricCards').innerHTML = metricOrder.map(key => {
    const m = (data.waterline || {})[key] || {};
    return `<article class="metric">
      <div class="label">${esc(metricNames[key])}</div>
      <div class="value">${esc(m.formatted_value)}</div>
      <span class="badge ${badgeClass(m.score)}">${esc(m.band)} / score ${pct(m.score)}</span>
      <p>${esc(m.interpretation)}</p>
    </article>`;
  }).join('');

  document.getElementById('bars').innerHTML = metricOrder.map(key => {
    const m = (data.waterline || {})[key] || {};
    const b = ((data.benchmark || {}).metrics || {})[key] || {};
    const pos = Math.max(0, Math.min(100, m.raw_percentile || 0));
    return `<div class="bar-row">
      <div class="bar-head"><strong>${esc(metricNames[key])}</strong><span>Value ${esc(m.formatted_value)} · percentile ${pct(m.raw_percentile)}</span></div>
      <div class="track"><div class="marker" style="left:${pos}%"></div></div>
      <div class="ticks"><span>P10 ${fmtBench(key,b.p10)}</span><span>P50 ${fmtBench(key,b.p50)}</span><span>P90 ${fmtBench(key,b.p90)}</span></div>
    </div>`;
  }).join('');
}

function renderReferenceCreatives() {
  const ref = data.reference_creatives || {};
  const items = arr(ref.items);
  if (!items.length) return;
  document.getElementById('referenceSection').classList.remove('hidden');
  document.getElementById('referenceMeta').textContent = `Top ${ref.top_count || items.length} queried, ${ref.accepted_count || items.length} similar landing pages accepted, showing ${ref.displayed_count || items.length} playable downloaded videos.`;
  document.getElementById('creativeCards').innerHTML = items.slice(0, 24).map((item, idx) => {
    const video = item.local_video_path
      ? `<video class="creative-video" controls preload="metadata" src="${esc(item.local_video_path)}"></video>`
      : `<div class="video-placeholder">${link(item.video_url, 'Open preview video')}</div>`;
    return `<article class="creative-card">
      <div class="creative-media">${video}</div>
      <div class="creative-body">
        <div class="creative-title"><span>Top ${idx + 1}</span><strong>${esc(item.formatted_ctr || fmtCtr(item.ctr))} CTR</strong></div>
        <p class="muted small">${esc(item.advertiser_name || item.domain || 'Reference creative')}</p>
        <dl class="mini-facts">
          <dt>Video ID</dt><dd>${esc(item.video_id)}</dd>
          <dt>Landing Page</dt><dd>${link(item.external_url, shortUrl(item.external_url))}</dd>
          <dt>Domain</dt><dd>${esc(item.domain || '--')}</dd>
          <dt>Similarity</dt><dd>${item.similarity_score == null ? '--' : esc(Number(item.similarity_score).toFixed(2))}</dd>
          <dt>Impr./Clicks</dt><dd>${fmtNum(item.impressions)} / ${fmtNum(item.clicks)}</dd>
        </dl>
        ${item.similarity_reason ? `<p class="reason">${esc(item.similarity_reason)}</p>` : ''}
      </div>
    </article>`;
  }).join('');
}

function renderVideoAnalysis() {
  const summary = data.video_analysis_summary || {};
  const items = arr(summary.items);
  if (!items.length) return;
  document.getElementById('analysisSection').classList.remove('hidden');
  document.getElementById('analysisMeta').textContent = `${summary.videos_analyzed || items.length} downloaded reference videos analyzed with MLLM.`;
  document.getElementById('analysisCards').innerHTML = items.map((item, idx) => {
    const first = item.first_3_seconds || {};
    const media = item.local_video_path ? `<video class="analysis-video" controls preload="metadata" src="${esc(item.local_video_path)}"></video>` : '';
    return `<article class="analysis-card">
      <div>${media}</div>
      <div>
        <div class="analysis-head"><h3>Top ${idx + 1} · ${esc(item.formatted_ctr || fmtCtr(item.ctr))} CTR</h3>${link(item.preview_url, 'Preview')}</div>
        <div class="module-grid">
          <section><h4>First 3 Seconds</h4><p>${esc(first.what_is_shown || '--')}</p><p class="reason">${esc(first.likely_message_or_overlay || '')}</p><span class="badge">${esc(first.hook_type || 'hook')}</span></section>
          <section><h4>Why It Drives CTR</h4><p>${esc(first.why_it_may_drive_ctr || '--')}</p></section>
          <section><h4>Structure</h4>${renderList(arr(item.creative_structure).map(x => `${x.stage || ''}: ${x.description || x}`))}</section>
          <section><h4>Selling Points</h4><div class="pill-wrap">${renderPills(item.selling_points)}</div></section>
          <section><h4>Visual Patterns</h4>${renderList(item.visual_patterns)}</section>
          <section><h4>Copy Patterns</h4>${renderList(item.copy_patterns)}</section>
          <section><h4>Transferable Actions</h4>${renderList(item.transferable_to_customer)}</section>
          <section><h4>Do Not Copy Blindly</h4>${renderList(item.not_recommended_to_copy)}</section>
        </div>
      </div>
    </article>`;
  }).join('');
}

function renderCreativeRecommendations() {
  const rec = ((data.video_analysis_summary || {}).recommendations) || {};
  if (!Object.keys(rec).length) return;
  document.getElementById('creativeRecommendationSection').classList.remove('hidden');
  const angles = arr(rec.creative_angles).map(angle => `<article class="angle-card">
    <h3>${esc(angle.angle_name || 'Creative Angle')}</h3>
    <p><strong>Why:</strong> ${esc(angle.why || '--')}</p>
    <p><strong>First 3s:</strong> ${esc(angle.first_3s_script || '--')}</p>
    <h4>Storyboard</h4>${renderList(angle.storyboard)}
    <h4>Selling Points</h4><div class="pill-wrap">${renderPills(angle.selling_points)}</div>
    <p><strong>CTA:</strong> ${esc(angle.cta || '--')}</p>
  </article>`).join('');
  document.getElementById('creativeRecommendations').innerHTML = `
    <section class="rec-block wide"><h3>Strategy Summary</h3><p>${esc(rec.strategy_summary || '--')}</p></section>
    <section class="rec-block"><h3>Priority Opportunities</h3>${renderList(rec.priority_opportunities)}</section>
    <section class="rec-block wide"><h3>Creative Angles</h3><div class="angle-grid">${angles}</div></section>
    <section class="rec-block"><h3>Production Checklist</h3>${renderList(rec.production_checklist)}</section>
    <section class="rec-block"><h3>Testing Plan</h3>${renderList(rec.testing_plan)}</section>
    <section class="rec-block"><h3>Risks</h3>${renderList(rec.risks)}</section>`;
}

function fmtBench(key, value) {
  if (typeof value !== 'number') return '--';
  if (key === 'ctr' || key === 'cvr' || key === 'play_3s_ratio') return `${(value * 100).toFixed(2)}%`;
  return Number(value).toPrecision(3);
}

renderBenchmark();
renderReferenceCreatives();
renderVideoAnalysis();
renderCreativeRecommendations();
