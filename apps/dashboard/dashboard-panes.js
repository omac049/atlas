// Auxiliary panes: settlement polling queue, missing-credentials banner,
// learning label counts, and the 90-day study report.
//
// This file deliberately renders ONLY into its own containers (#polling-queue-*,
// #credentials-banner, #learning-counts, #study-*) — the main feed, watchboard,
// and alert renderers belong to dashboard.js. Overview data arrives via the
// `atlas:overview` CustomEvent dispatched at the top of dashboard.js render();
// the study pane fetches /api/study on its own slower cadence.
//
// Wrapped in an IIFE because both scripts share the page's global scope, and
// every renderer is null-safe so markup load order or absence never throws.
(() => {
  const byId = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const words = (value) => String(value ?? '').replaceAll('_', ' ').toLowerCase();
  const num = (value) => Number(value || 0).toLocaleString('en-US');
  // Gaps and fees are dollars per $1 basket; cents is how an operator reads them.
  const cents = (value) => {
    if (value === null || value === undefined || value === '') return '—';
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    const sign = number > 0 ? '+' : number < 0 ? '−' : '';
    return `${sign}${Math.abs(number * 100).toFixed(1)}¢`;
  };
  const until = (iso) => {
    if (!iso) return 'unscheduled';
    const ms = new Date(iso).getTime() - Date.now();
    if (!Number.isFinite(ms)) return 'unscheduled';
    if (ms <= 0) return 'due now';
    const minutes = Math.max(1, Math.round(ms / 60000));
    if (minutes < 90) return `in ${minutes}m`;
    if (minutes < 2880) return `in ${Math.round(minutes / 60)}h`;
    return `in ${Math.round(minutes / 1440)}d`;
  };
  const shortId = (id) => {
    const text = String(id ?? '');
    return text.length > 14 ? `${text.slice(0, 14)}…` : text;
  };

  function renderPollingQueue(validation) {
    const status = byId('polling-queue-status');
    const rows = byId('polling-queue-rows');
    const cases = (validation && validation.pending_cases) || [];
    const eligible = cases.filter((item) => item.poll_eligible).length;
    if (status) {
      status.textContent = cases.length
        ? `${cases.length} pending · ${eligible} poll-eligible`
        : 'No pending settlement checks.';
    }
    if (!rows) return;
    rows.innerHTML = cases.length ? cases.map((item) => `<tr>
      <th scope="row"><code title="${esc(item.pair_id)}">${esc(shortId(item.pair_id))}</code></th>
      <td>${esc(words(item.source_kind) || 'unknown')}</td>
      <td>${esc(words(item.pending_reason) || 'unspecified')}</td>
      <td class="num">${esc(until(item.next_poll_at))}</td>
      <td class="num">${num(item.retry_count)}/${num(item.max_retries)}</td>
      <td>${item.poll_eligible ? '<span class="badge badge--dot badge--ok">ELIGIBLE</span>' : ''}</td>
    </tr>`).join('') : '<tr><td colspan="6"><div class="empty">No pending settlement checks.</div></td></tr>';
  }

  function renderCredentialsBanner(credentials) {
    const banner = byId('credentials-banner');
    if (!banner) return;
    if (credentials && credentials.complete === false) {
      // Names only — the API never serves credential values.
      banner.hidden = false;
      banner.textContent = `LIVE STREAM CREDENTIALS MISSING: ${(credentials.missing || []).join(', ')} — live shadow monitoring idle until set.`;
    } else {
      banner.hidden = true;
    }
  }

  function renderLearningCounts(learning) {
    const el = byId('learning-counts');
    if (!el) return;
    const entries = Object.entries(learning || {});
    el.textContent = entries.length
      ? entries.map(([label, count]) => `${label} ${num(count)}`).join(' · ')
      : 'No labels recorded.';
  }

  document.addEventListener('atlas:overview', (event) => {
    // A panes bug must never break dashboard.js's render path, which
    // dispatches this event synchronously.
    try {
      const detail = (event && event.detail) || {};
      renderPollingQueue(detail.validation);
      renderCredentialsBanner(detail.live_stream_credentials);
      renderLearningCounts(detail.learning);
    } catch (err) {
      console.error('dashboard-panes overview render failed', err);
    }
  });

  // --- 90-day study pane -----------------------------------------------------

  function studyStat(label, value) {
    return value === null || value === undefined || value === ''
      ? ''
      : `<div class="metric"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
  }

  function renderStudy(payload) {
    const status = byId('study-status');
    const content = byId('study-content');
    if (payload.status === 'NO_REPORT' || !payload.report) {
      if (status) status.textContent = 'No study report yet — runs Mondays 07:00 via com.atlas.study, or: uv run atlas gaps study';
      if (content) content.innerHTML = '';
      return;
    }
    const report = payload.report;
    if (status) status.textContent = `${payload.source || 'report'} · generated ${payload.generated_at || 'unknown'}`;
    if (!content) return;
    const go = Boolean(report.meets_go_threshold);
    const survival = report.survival || {};
    const sizes = report.executable_size_contracts || {};
    const weekly = report.weekly || [];
    const weeklyRows = weekly.map((week) => `<tr>
      <th scope="row">${esc(week.week_of)}</th>
      <td class="num">${num(week.observations)}</td>
      <td class="num">${num(week.executable_observations)}</td>
      <td class="num">${num(week.opportunities)}</td>
      <td class="num">${num(week.venue_text_only_opportunities)}</td>
      <td class="num">${cents(week.median_gap)}</td>
      <td class="num">${cents(week.max_gap)}</td>
    </tr>`).join('');
    content.innerHTML = `
      <div class="study-headline">
        <strong>Day ${num(report.study_day)} of 90 · Phase ${num(report.phase)}</strong>
        <span class="badge badge--dot ${go ? 'badge--ok' : 'badge--warn'}">${go ? 'GO' : 'NO-GO'}</span>
        <span>${esc(report.verified_opportunities_per_30_days ?? '—')} verified opportunities / 30d vs threshold ${esc(report.go_threshold_per_30_days ?? '—')}</span>
      </div>
      <div class="metrics">
        ${studyStat('DISTINCT OPPORTUNITIES', num(report.distinct_opportunities))}
        ${studyStat('VENUE-TEXT-ONLY', num(report.venue_text_only_opportunities_total))}
        ${studyStat('CANDIDATE RATE / 30D', report.candidate_opportunities_per_30_days)}
        ${studyStat('SURVIVAL MEDIAN', survival.median_minutes ? `${survival.median_minutes}m` : null)}
        ${studyStat('SURVIVAL MAX', survival.max_minutes ? `${survival.max_minutes}m` : null)}
        ${studyStat('SINGLE-SWEEP ONLY', num(survival.single_sweep_only))}
        ${studyStat('SIZE MEDIAN (CONTRACTS)', sizes.median)}
        ${studyStat('OBSERVATIONS REVIEWED', num(report.observations_reviewed))}
      </div>
      ${weekly.length ? `<table class="detail-table">
        <thead><tr>
          <th scope="col">Week of</th>
          <th scope="col" class="num">Scans</th>
          <th scope="col" class="num">Exec</th>
          <th scope="col" class="num">Opps</th>
          <th scope="col" class="num">Text-only</th>
          <th scope="col" class="num">Median gap</th>
          <th scope="col" class="num">Max gap</th>
        </tr></thead>
        <tbody>${weeklyRows}</tbody>
      </table>` : '<div class="empty">No weekly data recorded yet.</div>'}`;
  }

  async function refreshStudy() {
    const status = byId('study-status');
    if (!status && !byId('study-content')) return;
    let payload;
    try {
      const response = await fetch('/api/study', {cache: 'no-store', signal: AbortSignal.timeout(10000)});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      payload = await response.json();
    } catch {
      if (status) status.textContent = 'Study report unavailable — API unreachable.';
      return;
    }
    try {
      renderStudy(payload);
    } catch (err) {
      console.error('dashboard-panes study render failed', err);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', refreshStudy);
  } else {
    refreshStudy();
  }
  // The report is regenerated weekly; 10 minutes keeps the pane fresh without
  // joining the 15s overview poll.
  setInterval(refreshStudy, 600000);
})();
