const $ = (id) => document.getElementById(id);
const money = (value, digits = 2) => `$${Number(value).toFixed(digits)}`;
const pct = (value) => `${(Number(value) * 100).toFixed(1)}%`;
const rate = (numerator, denominator) => denominator > 0 ? pct(Number(numerator) / Number(denominator)) : '—';
const fmt = (value) => Number(value || 0).toLocaleString('en-US');
const safe = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const sentence = (value) => {
  const text = String(value ?? '').replaceAll('_', ' ').toLowerCase();
  return (text.charAt(0).toUpperCase() + text.slice(1))
    .replace(/\bkalshi\b/g, 'Kalshi')
    .replace(/\bpolymarket\b/g, 'Polymarket');
};
function terms(value = {}) { const strike = `${value.threshold_operator || ''}${value.threshold || '—'} ${value.threshold_unit || ''}`.trim(); return `${strike} · ${value.market_type || 'unknown'} · ${value.measurement_period || 'no period'}`; }
function age(iso) {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 2) return 'just now';
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 129600) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}
function settlementTime(iso) { if (!iso) return 'not published'; const date = new Date(iso); return Number.isNaN(date.getTime()) ? 'not published' : `${date.toISOString().replace('T', ' ').slice(0, 16)}Z`; }
let lastGoodUpdate = null;
function render(data) {
  lastGoodUpdate = data.last_updated;
  $('connection').textContent = 'CONNECTED'; $('connection').classList.remove('offline');
  $('updated').textContent = `UPDATED ${age(data.last_updated).toUpperCase()}`;
  const evidence = data.evidence || {};
  const livePairs = Number(evidence.live_pair_count || 0);
  $('evidence-mode').textContent = livePairs > 0 ? 'LIVE PAIR VALIDATED / PAPER ONLY' : 'NO LIVE PAIR VALIDATED';
  $('evidence-detail').textContent = livePairs > 0 ? `${livePairs} deterministic live pair(s) are eligible for paper monitoring.` : 'The displayed opportunity is fixture research; live catalogs currently have no deterministic equivalent pair.';
  const agent = data.agent || {};
  const demoAgent = Boolean(evidence.demo_opportunity);
  $('agent-label').textContent = demoAgent ? 'Fixture agent trace · paper only' : 'Live agent research loop · paper only';
  $('agent-status').textContent = `${sentence(agent.status || 'unknown')} · ${agent.steps?.length || 0} steps`;
  $('agent-detail').textContent = agent.state?.catalog ? `${demoAgent ? 'Fixture trace' : 'Live trace'} reviewed ${agent.state.proposal_count || 0} candidate(s) via ${String(agent.state.proposal_source || 'unknown').replaceAll('_', ' ')},${agent.state.proposal_error ? ` with safe fallback (${agent.state.proposal_error})` : ''} and stayed within the paper-only policy.` : 'Agent trace unavailable.';
  $('agent-opportunities').textContent = demoAgent ? `0 LIVE / ${agent.opportunities?.length || 0} DEMO` : agent.opportunities?.length || 0;
  $('agent-steps').innerHTML = (agent.steps || []).length ? agent.steps.map((step, index) => `<div class="agent-step"><b>${index + 1}</b><div><strong>${safe(step.action.replaceAll('_', ' '))}</strong><span>${safe(step.reason)}</span></div></div>`).join('') : '<div class="empty">No agent steps recorded.</div>';
  const shadow = data.shadow_observation;
  const shadowValidation = data.shadow_validation || {};
  $('shadow-observations').textContent = fmt(shadowValidation.observation_count || 0);
  $('shadow-pairs').textContent = fmt(shadowValidation.unique_pairs || 0);
  $('shadow-positive').textContent = pct(shadowValidation.positive_edge_rate || 0);
  if (shadow) {
    const quote = shadow.best_direction || {};
    $('shadow-title').textContent = `${shadow.kalshi_title} ↔ ${shadow.polymarket_title}`;
    $('shadow-detail').textContent = `${quote.kalshi_side || '—'} + ${quote.polymarket_side || '—'} · ${quote.contracts || '0'} contracts · observation only`;
    $('shadow-cost').textContent = money(quote.gross_cost || 0);
    $('shadow-edge').textContent = money(quote.raw_edge_if_complementary || 0);
    $('shadow-status').textContent = shadow.rule_status || 'BLOCKED';
    $('shadow-blockers').textContent = (shadow.blockers || []).map((code) => code.replace('_MISMATCH', '').replaceAll('_', ' ')).join(' · ');
  }
  $('venues').textContent = `${data.venues.kalshi.toUpperCase()} / ${data.venues.polymarket_us.toUpperCase()}`;
  $('detected').textContent = fmt(data.research.detected_opportunities);
  $('executable').textContent = fmt(data.research.executable_opportunities);
  $('phantom').textContent = pct(data.research.phantom_rate);
  $('failed-loss').textContent = money(data.research.failed_hedge_losses);
  $('lifetime').textContent = `${data.research.median_lifetime_ms}ms`;
  $('rule-check').textContent = data.opportunity?.rule_check || '—'; $('pair-status').textContent = data.pair.status;
  $('pair-source').textContent = data.pair.source.toUpperCase();
  $('paper-trades').textContent = fmt(data.paper_trades.count);
  const scan = data.discovery_scan;
  $('discovery-status').textContent = scan ? `${fmt(scan.polymarket_active)} ACTIVE / ${fmt(scan.approved)} CANDIDATE` : '—';
  const training = data.training_readiness || {};
  $('learning-status').textContent = training.ready ? `${training.labels} LABELS / READY` : `${training.labels || 0}/50 LABELS / BLOCKED`;
  const catalog = data.catalog_compatibility;
  $('catalog-fit').textContent = catalog ? (catalog.event_compatible ? `EXACT: ${catalog.compatible_families.join(', ').toUpperCase()}` : catalog.shared_event_count ? `EVENTS OVERLAP: ${fmt(catalog.shared_event_count)}` : `FAMILY ONLY: ${catalog.compatible_families.join(', ').toUpperCase() || 'NONE'}`) : '—';
  const settlement = catalog?.settlement_discovery || {};
  const safeShared = Number(settlement.guaranteed_shared_events || 0);
  const executionReady = Number(settlement.execution_ready_events || 0);
  $('settlement-status').textContent = executionReady > 0 ? 'Execution review available' : safeShared > 0 ? 'Guaranteed events need rule alignment' : 'No guaranteed shared event';
  $('settlement-detail').textContent = executionReady > 0
    ? 'Settlement-ready pairs are ranked by the later venue deadline so the earliest fully reconcilable pair stays first.'
    : safeShared > 0
      ? 'Both venues have deterministic settlement, but contract terms still differ.'
      : 'Shared events are currently discretionary or insufficiently specified; none can become a paper trade.';
  $('settlement-shared').textContent = settlement.shared_events_ranked != null ? fmt(settlement.shared_events_ranked) : '—';
  $('settlement-guaranteed').textContent = fmt(safeShared);
  $('settlement-evidence-complete').textContent = settlement.evidence_complete_shared_events != null ? fmt(settlement.evidence_complete_shared_events) : '—';
  $('settlement-ready').textContent = fmt(executionReady);
  $('settlement-contracts').textContent = `${fmt(settlement.kalshi_counts?.GUARANTEED || 0)} / ${fmt(settlement.polymarket_counts?.GUARANTEED || 0)}`;
  const normalization = catalog?.normalization_coverage || {};
  const normalizedTotal = Number(normalization.normalized_contracts || 0);
  const normalizedShared = Number(normalization.shared_events || 0);
  const normalizedGuaranteed = Number(normalization.guaranteed_shared_events || 0);
  const normalizedExact = Number(normalization.exact_rule_matches || 0);
  $('normalization-status').textContent = normalizedExact > 0 ? 'Exact cross-venue rule match found' : normalizedGuaranteed > 0 ? 'Safe events need term alignment' : normalizedShared > 0 ? 'Shared events need settlement evidence' : `${fmt(normalizedTotal)} contracts normalized`;
  $('normalization-detail').textContent = normalizedExact > 0
    ? 'At least one family has identical event, threshold, source, timing, revision, and settlement terms on both venues.'
    : normalizedShared > 0
      ? 'Atlas found the same underlying events, but no family has cleared every deterministic rule gate yet.'
      : 'Normalization is active. Atlas is waiting for the same rule-complete event to appear on both venues.';
  $('normalization-total').textContent = fmt(normalizedTotal);
  $('normalization-shared').textContent = fmt(normalizedShared);
  $('normalization-guaranteed').textContent = fmt(normalizedGuaranteed);
  $('normalization-exact').textContent = fmt(normalizedExact);
  const weatherAudit = catalog?.weather_rule_enrichment || {};
  const weatherRefreshed = Number(weatherAudit.pairs_refreshed || 0);
  const weatherUnresolved = Number(weatherAudit.unresolved_pairs || 0);
  $('normalization-audit').textContent = weatherRefreshed > 0
    ? `FULL-DETAIL WEATHER AUDIT · ${weatherRefreshed} shared pair(s) refreshed · ${weatherUnresolved} unresolved · ${Number(weatherAudit.new_evidence_versions || 0)} new rule version(s)`
    : 'FULL-DETAIL WEATHER AUDIT · waiting for the next live scan';
  const sharedRuleAudit = catalog?.shared_rule_enrichment || {};
  $('shared-rule-audit').textContent = Number(sharedRuleAudit.shared_events_considered || 0) > 0
    ? `BOUNDED CROSS-FAMILY AUDIT · ${Number(sharedRuleAudit.pairs_refreshed || 0)} pair(s) refreshed · ${Number(sharedRuleAudit.complete_policy_pairs || 0)} complete policy pair(s) · ${Number(sharedRuleAudit.shared_events_skipped_non_guaranteed || 0)} known discretionary event(s) skipped · ${Number(sharedRuleAudit.detail_failures || 0)} detail failure(s)`
    : Number(sharedRuleAudit.shared_events_skipped_non_guaranteed || 0) > 0
      ? `BOUNDED CROSS-FAMILY AUDIT · ${Number(sharedRuleAudit.shared_events_skipped_non_guaranteed || 0)} known discretionary event(s) skipped · waiting for a candidate with explicit deterministic policy`
      : 'BOUNDED CROSS-FAMILY AUDIT · waiting for the next live scan';
  const identity = catalog?.identity_discovery || {};
  $('identity-audit').textContent = `STRUCTURED IDENTITY AUDIT · ${Number(identity.structured_shared_keys || 0)} shared key(s) · ${Number(identity.novel_alias_candidates || 0)} novel alias(es) · ${String(identity.status || 'WAITING_FOR_SCAN').replaceAll('_', ' ')}`;
  const familyCoverage = normalization.families || {};
  const familyOrder = ['economic', 'weather', 'election', 'crypto'];
  $('normalization-families').innerHTML = familyOrder.map((family) => {
    const item = familyCoverage[family] || {};
    const blockerEntries = Object.entries(item.blocker_counts || {}).slice(0, 2);
    const blockers = blockerEntries.length
      ? blockerEntries.map(([code, count]) => `${code.replaceAll('_', ' ')} ×${count}`).join(' · ')
      : 'NO NORMALIZATION BLOCKERS';
    const status = String(item.status || 'NOT_PRESENT').replaceAll('_', ' ');
    const statusClass = item.exact_rule_matches ? 'exact' : item.guaranteed_shared_events ? 'safe' : 'waiting';
    return `<div class="normalization-row"><strong>${safe(family.toUpperCase())}</strong><span>${fmt(item.kalshi_contracts)} / ${fmt(item.polymarket_contracts)}</span><span>${fmt(item.kalshi_normalized)} / ${fmt(item.polymarket_normalized)}</span><span>${fmt(item.shared_events)}</span><span>${fmt(item.guaranteed_shared_events)}</span><span>${fmt(item.exact_rule_matches)}</span><div><em class="${statusClass}">${safe(status)}</em><small>${safe(blockers)}</small></div></div>`;
  }).join('');
  const validation = data.validation || {};
  const backfill = data.historical_backfill || {};
  const venueCoverage = backfill.venue_coverage || {};
  const coverageSummary = Object.entries(venueCoverage).map(([venue, counts]) => {
    const label = venue === 'polymarket_global' ? 'GLOBAL' : venue === 'polymarket_us' ? 'US' : venue.toUpperCase();
    return `${label} ${Number(counts.final_binary_markets || 0)}/${Number(counts.closed_markets || 0)}`;
  }).join(' · ');
  const trustedLabels = Number(validation.trusted_labels || 0);
  const validationCases = Number(validation.cases || 0);
  const structuredShared = Number(identity.structured_shared_keys || 0);
  const awaitingSettlement = Number(validation.awaiting_settlement || 0);
  const resolvedCases = Number(validation.resolved_cases || 0);
  const blockerCounts = Object.keys(weatherAudit.policy_blocker_counts || {}).length
    ? weatherAudit.policy_blocker_counts
    : weatherAudit.blocker_counts || {};
  const blockerEntries = Object.entries(blockerCounts).filter(([, count]) => Number(count) > 0);
  const unresolvedBlockers = blockerEntries.reduce((total, [, count]) => total + Number(count), 0);
  const proposals = data.candidate_proposals || [];
  const reviewQueue = proposals.filter((proposal) => proposal.status === 'REVIEW_REQUIRED').length;
  const trainingLabels = Number(training.labels ?? trustedLabels);
  const approvedLabels = Number(training.approved || backfill.approved_labels || 0);
  const rejectedLabels = Number(training.rejected || backfill.rejected_labels || 0);
  const confirmedOutcomes = Number(validation.confirmed || 0);
  const divergedOutcomes = Number(validation.diverged || 0);
  const inconclusiveOutcomes = Number(validation.inconclusive || 0);
  const resolvedOutcomes = confirmedOutcomes + divergedOutcomes + inconclusiveOutcomes;
  const decisiveOutcomes = confirmedOutcomes + divergedOutcomes;
  $('validation-coverage').textContent = rate(resolvedCases, validationCases);
  $('validation-precision').textContent = rate(confirmedOutcomes, decisiveOutcomes);
  $('validation-inconclusive').textContent = rate(inconclusiveOutcomes, resolvedOutcomes);
  $('validation-readiness').textContent = training.ready ? 'READY' : String(training.status || 'BLOCKED').replaceAll('_', ' ');
  $('validation-readiness-detail').textContent = training.ready
    ? 'Minimum label count and both outcome classes are present; evaluation still gates promotion.'
    : (training.reasons || ['Settlement-verified labels and both outcome classes are required.'])[0];
  $('validation-provenance-state').textContent = backfill.completed_at
    ? `${String(backfill.status || 'VERIFIED RUN').replaceAll('_', ' ')} · ${age(backfill.completed_at).toUpperCase()}`
    : validationCases > 0
      ? 'SETTLEMENT LEDGER · ACTIVE'
      : 'NO VERIFIED RUN';
  const executionEnabled = training.execution_enabled ?? training.loop?.execution_enabled ?? true;
  $('validation-safety').textContent = data.paper_only && executionEnabled === false
    ? 'PAPER ONLY / NEVER EXECUTED · SETTLEMENT-VERIFIED LABELS ONLY'
    : 'SAFETY STATE REQUIRES REVIEW';
  const trainingTarget = 50;
  const labelsRemaining = Math.max(0, trainingTarget - trainingLabels);
  $('hero-normalized').textContent = fmt(normalizedTotal);
  $('hero-shared').textContent = fmt(normalizedShared);
  $('hero-ready').textContent = fmt(executionReady);
  $('hero-labels').textContent = `${fmt(trainingLabels)} / ${trainingTarget}`;
  $('hero-labels-bar').style.width = `${Math.min(100, (trainingLabels / trainingTarget) * 100)}%`;
  $('cohort-shared').textContent = fmt(structuredShared);
  $('cohort-ready').textContent = `${fmt(safeShared)} / ${fmt(executionReady)}`;
  $('cohort-blocker-count').textContent = fmt(unresolvedBlockers);
  $('cohort-awaiting').textContent = fmt(awaitingSettlement);
  $('cohort-resolved').textContent = fmt(resolvedCases);
  $('cohort-labels').textContent = fmt(trainingLabels);
  $('cohort-label-target').textContent = training.ready
    ? 'minimum and class mix reached'
    : labelsRemaining > 0
      ? `${labelsRemaining} needed for training review`
      : 'approved examples still required';
  const cohortSteps = document.querySelectorAll('[data-cohort-step]');
  cohortSteps.forEach((step) => step.classList.remove('active', 'complete'));
  const setCohortStep = (name, state) => document.querySelector(`[data-cohort-step="${name}"]`)?.classList.add(state);
  if (structuredShared > 0) setCohortStep('shared', 'complete');
  if (executionReady > 0) setCohortStep('ready', 'complete');
  if (unresolvedBlockers > 0) setCohortStep('blockers', 'active');
  else if (structuredShared > 0) setCohortStep('blockers', 'complete');
  if (awaitingSettlement > 0) setCohortStep('awaiting', 'active');
  if (resolvedCases > 0) setCohortStep('resolved', 'complete');
  if (trainingLabels > 0) setCohortStep('labels', training.ready ? 'complete' : 'active');
  if (training.ready) {
    $('cohort-status').textContent = 'Minimum evidence gate reached';
    $('cohort-detail').textContent = `${trainingLabels} settlement-verified labels are available. Model evaluation remains separate from live trading.`;
    $('cohort-next-gate').textContent = 'Evaluate labels before any model promotion';
  } else if (resolvedCases > 0 && approvedLabels === 0) {
    $('cohort-status').textContent = 'Negative cohort built — positive evidence missing';
    $('cohort-detail').textContent = `${rejectedLabels} settlement-backed rejection(s) are available, but zero approved-equivalent labels exist. Training remains blocked.`;
    $('cohort-next-gate').textContent = 'Find and verify settled rule-equivalent contracts';
  } else if (resolvedCases > 0) {
    $('cohort-status').textContent = 'First cohort is producing labels';
    $('cohort-detail').textContent = `${resolvedCases} case(s) resolved and ${trainingLabels} trusted label(s) recorded. All evidence remains paper-only.`;
    $('cohort-next-gate').textContent = `Collect ${labelsRemaining} more trusted labels, including approvals and rejections`;
  } else if (awaitingSettlement > 0) {
    $('cohort-status').textContent = 'First cohort is awaiting settlement';
    $('cohort-detail').textContent = `${awaitingSettlement} paper-only case(s) are waiting for both venues to publish final outcomes.`;
    $('cohort-next-gate').textContent = 'Reconcile both venue outcomes into trusted labels';
  } else if (executionReady > 0) {
    $('cohort-status').textContent = 'First cohort is ready to track';
    $('cohort-detail').textContent = `${executionReady} event(s) cleared deterministic equivalence and settlement-safety checks.`;
    $('cohort-next-gate').textContent = 'Open paper-only validation cases and wait for settlement';
  } else if (safeShared > 0) {
    $('cohort-status').textContent = 'First cohort needs final rule alignment';
    $('cohort-detail').textContent = `${safeShared} event(s) have guaranteed settlement, but none are ready for outcome tracking.`;
    $('cohort-next-gate').textContent = 'Clear every remaining contract-rule mismatch';
  } else if (structuredShared > 0) {
    $('cohort-status').textContent = 'First cohort identified — rules unresolved';
    $('cohort-detail').textContent = `${structuredShared} shared event(s) have the same structured identity; zero are guaranteed or ready.`;
    $('cohort-next-gate').textContent = 'Resolve settlement guarantees and revision-policy differences';
  } else if (backfill.status === 'EXTERNAL_EVIDENCE_BLOCKED') {
    $('cohort-status').textContent = 'Historical evidence inventory exhausted';
    $('cohort-detail').textContent = `Atlas checked ${fmt(backfill.polymarket_closed_markets)} closed Polymarket contracts across ${Object.keys(venueCoverage).length || 1} evidence source(s) and found ${fmt(backfill.polymarket_final_binary_markets)} with final binary evidence; none produced a verified cross-venue label.`;
    $('cohort-next-gate').textContent = 'Keep backfilling as both venues publish final overlapping settlements';
  } else {
    $('cohort-status').textContent = 'Waiting for the first shared event';
    $('cohort-detail').textContent = 'No cross-venue event has enough structured identity evidence to begin settlement validation.';
    $('cohort-next-gate').textContent = 'Find the same rule-complete event on both venues';
  }
  $('cohort-blockers').textContent = blockerEntries.length
    ? blockerEntries.map(([code, count]) => `${code.replace('_MISMATCH', '').replaceAll('_', ' ')} ×${Number(count)}`).join(' · ')
    : reviewQueue > 0
      ? `${reviewQueue} contract review(s) remain outside the first settlement-ready cohort.`
      : 'No unresolved cohort blocker is recorded.';
  $('validation-status').textContent = training.ready
    ? 'Training evidence ready'
    : trustedLabels >= 50 && approvedLabels === 0
      ? 'Label mix blocked'
    : validationCases > 0
      ? 'Settlement monitoring active'
      : backfill.status === 'EXTERNAL_EVIDENCE_BLOCKED'
        ? 'Historical overlap blocked'
        : 'Waiting for guaranteed pair';
  $('validation-detail').textContent = training.ready
    ? 'The minimum settlement-verified label gate is met; evaluation must still pass before any model promotion.'
    : trustedLabels >= 50 && approvedLabels === 0
      ? `${rejectedLabels} settlement-backed rejections are stored, but no approved-equivalent labels exist. Atlas will not train on a one-class dataset.`
    : validationCases > 0
      ? 'Tracked cases remain paper-only. Matching outcomes confirm approved relationships; divergence creates a rejection label.'
      : backfill.status === 'EXTERNAL_EVIDENCE_BLOCKED'
        ? `Backfill completed across ${fmt(backfill.kalshi_events_scanned)} Kalshi events and ${fmt(backfill.polymarket_closed_markets)} closed Polymarket contracts; no shared terminal event can create a trusted label yet.`
        : 'Validation-universe rules are being versioned, but no guaranteed cross-venue pair is eligible for outcome validation yet.';
  const milestoneAlerts = data.milestone_alerts || [];
  if (milestoneAlerts.length) {
    const alert = milestoneAlerts[0];
    const transition = alert.transition_kind === 'DETERMINISTIC_RULE_GATE'
      ? 'DETERMINISTIC RULE GATE CLEARED'
      : `${String(alert.queue_status || 'TRANSITION').replaceAll('_', ' ')}`;
    $('milestone-alert').textContent = `MILESTONE ALERT · ${transition} · ${safe(alert.candidate_id)}`;
  } else {
    $('milestone-alert').textContent = 'No milestone transition yet';
  }
  $('validation-markets').textContent = fmt(validation.markets_tracked ?? 0);
  $('validation-versions').textContent = fmt(validation.evidence_versions ?? 0);
  $('validation-changes').textContent = fmt(validation.rule_changes ?? 0);
  $('validation-awaiting').textContent = fmt(validation.awaiting_settlement ?? 0);
  $('validation-resolved').textContent = fmt(validation.resolved_cases ?? 0);
  $('validation-labels').textContent = fmt(trustedLabels);
  $('validation-label-mix').textContent = `${fmt(approvedLabels)} / ${fmt(rejectedLabels)}`;
  const backfillBlockers = Object.entries(backfill.blockers || {});
  $('validation-backfill').textContent = backfill.completed_at
    ? `HISTORICAL BACKFILL · ${String(backfill.status || 'UNKNOWN').replaceAll('_', ' ')} · ${coverageSummary || 'SOURCE DETAIL UNAVAILABLE'} · ${Number(backfill.new_labels || 0)} new label(s) · ${backfillBlockers.length ? backfillBlockers.map(([code, count]) => `${code.replaceAll('_', ' ')} ×${Number(count)}`).join(' · ') : 'no recorded blocker'}`
    : 'HISTORICAL BACKFILL · waiting for the first verified run';
  const globalScope = venueCoverage.polymarket_global?.catalog_scope || 'not reported';
  const candidateEvents = Number(backfill.historical_candidate_events ?? backfill.cross_venue_event_candidates ?? 0);
  const candidateEventsFound = Number(backfill.historical_candidate_events_found ?? candidateEvents);
  const pairsReviewed = Number(backfill.market_pairs_reviewed || 0);
  const pairsCap = Number(backfill.max_market_pairs || 0);
  const resolvedPairs = Number(backfill.resolved_pairs || 0);
  const resolvedPairsCap = Number(backfill.max_resolved_pairs || 0);
  const capCodes = new Set(Object.keys(backfill.blockers || {}));
  const appliedCaps = [
    capCodes.has('HISTORICAL_CANDIDATE_EVENT_CAP_APPLIED') ? 'EVENT CAP APPLIED' : '',
    capCodes.has('HISTORICAL_MARKET_PAIR_CAP_APPLIED') ? 'PAIR CAP APPLIED' : '',
  ].filter(Boolean);
  $('validation-provenance').textContent = backfill.completed_at
    ? `GLOBAL TAG SCOPE · ${globalScope} · SCAN BOUNDS · EVENTS ${candidateEvents} SELECTED / ${candidateEventsFound} FOUND · PAIRS ${pairsReviewed}/${pairsCap || '—'} · RESOLVED ${resolvedPairs}/${resolvedPairsCap || '—'}${appliedCaps.length ? ` · ${appliedCaps.join(' · ')}` : ''}`
    : 'HISTORICAL PROVENANCE · waiting for the first verified run';
  const seriesTickers = backfill.kalshi_series_tickers || [];
  const seriesCounts = backfill.kalshi_series_event_counts || {};
  $('backfill-series').textContent = seriesTickers.length
    ? `KALSHI SERIES SCAN · ${seriesTickers.map((ticker) => `${ticker} ×${Number(seriesCounts[ticker] || 0)}`).join(' · ')}`
    : 'KALSHI SERIES SCAN · no series recorded yet';
  const backfillRuns = data.historical_backfill_runs || [];
  const runsMarkup = backfillRuns.length ? backfillRuns.map((run) => {
    const series = (run.kalshi_series_tickers || []).slice(0, 3).map((ticker) => `${ticker} ×${Number((run.kalshi_series_event_counts || {})[ticker] || 0)}`).join(' · ') || 'no series scanned';
    const scopes = (run.tag_scopes || []).join(' · ') || 'no tag scope';
    const when = run.completed_at ? age(run.completed_at) : 'time unknown';
    return `<div class="backfill-run"><b>${safe(String(run.status || 'UNKNOWN').replaceAll('_', ' '))}</b><span>${safe(when)} · ${Number(run.new_labels || 0)} new label(s) · ${Number(run.resolved_pairs || 0)} resolved / ${Number(run.inconclusive_pairs || 0)} inconclusive</span><small>${safe(series)} · ${safe(scopes)}</small></div>`;
  }).join('') : '<div class="empty">No completed backfill runs recorded yet.</div>';
  $('backfill-runs').innerHTML = `<span class="section-label">RECENT BACKFILL RUNS</span>${runsMarkup}`;
  const gapRadar = data.gap_radar || {};
  const gapSummary = gapRadar.summary || {};
  $('gap-bankroll').textContent = gapSummary.paper_bankroll
    ? `PAPER $2K GAP METER · $${gapSummary.paper_bankroll} · ${Number(gapSummary.distinct_executable_opportunities || 0)} EXECUTABLE GAP(S) SEEN · CANDIDATES ONLY — NOT PROVEN TWINS`
    : 'PAPER $2K GAP METER · no radar scans recorded yet';
  const gapRows = gapRadar.recent || [];
  const gapMarkup = gapRows.length ? gapRows.map((row) => `<div class="backfill-run"><b>${safe(row.executable_gap ? 'GAP' : 'NO GAP')}</b><span>${safe(String(row.best_gap ?? '—'))} · ${safe(String(row.shape || ''))} · ${safe(String(row.verification_status || ''))}</span><small>${safe(String(row.event_subject || ''))}</small></div>`).join('') : '<div class="empty">No gap observations recorded yet.</div>';
  $('gap-recent').innerHTML = `<span class="section-label">RECENT GAP RADAR · PAPER ONLY</span>${gapMarkup}`;
  const trustedRecent = data.trusted_labels_recent || [];
  const approvedRecent = trustedRecent.filter((row) => row.label === 'APPROVED_EQUIVALENT').length;
  $('certified-count').textContent = fmt(trustedRecent.length);
  $('certified-status').textContent = trustedRecent.length
    ? `${fmt(approvedRecent)} certified twin(s) · ${fmt(trustedRecent.length - approvedRecent)} certified rejection(s)`
    : 'Waiting for the first certified pair';
  $('certified-detail').textContent = trustedRecent.length
    ? 'Settlement-verified labels, newest first. Approvals are proven twins; rejections are proven non-twins.'
    : 'Every label requires both venues to publish final settlement evidence first.';
  $('certified-twins').innerHTML = trustedRecent.length ? trustedRecent.map((row) => {
    const labelClass = String(row.label || '').toLowerCase();
    const outcomes = row.outcome_a || row.outcome_b
      ? `settled ${row.outcome_a || '—'} / ${row.outcome_b || '—'}`
      : 'outcomes unpublished';
    const provenance = `${String(row.source_kind || 'UNKNOWN SOURCE').replaceAll('_', ' ')} · ${String(row.relationship_status || 'UNRESOLVED').replaceAll('_', ' ')}`;
    const when = row.created_at ? age(row.created_at) : '—';
    return `<div class="certified-row"><em class="${safe(labelClass)}">${safe(String(row.label || 'UNKNOWN').replaceAll('_', ' '))}</em><div><strong>${safe(row.title_a || row.pair_id)}</strong><span>↔ ${safe(row.title_b || 'counterpart unavailable')}</span><small>${safe(String(row.venue_a || 'venue a'))} ↔ ${safe(String(row.venue_b || 'venue b'))} · ${safe(outcomes)} · ${safe(provenance)}</small></div><span class="certified-when">${safe(when)}</span></div>`;
  }).join('') : '<div class="empty">No settlement-verified labels recorded yet.</div>';
  const feedItems = [
    ...milestoneAlerts.map((alert) => ({
      at: alert.observed_at || '',
      kind: alert.transition_kind === 'DETERMINISTIC_RULE_GATE' ? 'GATE' : 'QUEUE',
      headline: alert.transition_kind === 'DETERMINISTIC_RULE_GATE'
        ? 'DETERMINISTIC RULE GATE CLEARED'
        : `QUEUE → ${String(alert.queue_status || 'TRANSITION').replaceAll('_', ' ')}`,
      detail: String(alert.candidate_id || ''),
    })),
    ...gapRows.map((row) => ({
      at: row.observed_at || '',
      kind: row.executable_gap ? 'GAP' : 'SCAN',
      headline: row.executable_gap
        ? `EXECUTABLE GAP · ${String(row.best_gap ?? '—')}`
        : `RADAR SCAN · ${String(row.best_gap ?? '—')}`,
      detail: `${String(row.event_subject || '')} · ${String(row.shape || '')} · ${String(row.verification_status || '')}`,
    })),
  ].filter((item) => item.at).sort((a, b) => String(b.at).localeCompare(String(a.at))).slice(0, 10);
  $('alert-feed-status').textContent = feedItems.length
    ? `${fmt(feedItems.length)} recent signal(s) · candidates only`
    : 'Waiting for radar and queue activity';
  $('alert-feed').innerHTML = feedItems.length ? feedItems.map((item) => `<div class="alert-item"><b class="alert-kind ${safe(item.kind.toLowerCase())}">${safe(item.kind)}</b><div><span>${safe(item.headline)}</span><small>${safe(item.detail)}</small></div><span class="alert-when">${safe(age(item.at))}</span></div>`).join('') : '<div class="empty">No alerts recorded yet.</div>';
  $('backfill-polymarket').textContent = coverageSummary || `${Number(backfill.polymarket_final_binary_markets || 0)} / ${Number(backfill.polymarket_closed_markets || 0)}`;
  $('backfill-shared').textContent = fmt(backfill.historical_candidate_events ?? backfill.cross_venue_event_candidates ?? 0);
  $('backfill-remaining').textContent = fmt(backfill.labels_remaining ?? labelsRemaining);
  const settlementRankings = data.settlement_candidates || settlement.rankings || [];
  $('settlement-rankings').innerHTML = settlementRankings.length ? settlementRankings.slice(0, 8).map((item, index) => {
    const mismatches = (item.mismatch_codes || []).map((code) => code.replace('_MISMATCH', '').replaceAll('_', ' ')).join(' · ') || 'EXACT RULE MATCH';
    const lifecycle = String(item.lifecycle_status || 'UNKNOWN').replaceAll('_', ' ');
    const settlementTimes = `K ${settlementTime(item.kalshi_resolution_time)} · P ${settlementTime(item.polymarket_resolution_time)}`;
    const readyAt = settlementTime(item.settlement_ready_at);
    const evidenceSummary = (evidence) => {
      if (!evidence || typeof evidence !== 'object') return 'EVIDENCE NOT REFRESHED';
      const label = evidence?.complete ? 'EVIDENCE COMPLETE' : 'EVIDENCE BLOCKED';
      const blockers = (evidence?.blockers || []).slice(0, 2).map((code) => code.replaceAll('_', ' ')).join(' · ');
      const missing = (evidence?.missing_fields || []).slice(0, 2).map((field) => field.replaceAll('_', ' ')).join(' · ');
      const detail = blockers || (missing ? `MISSING ${missing}` : 'SOURCE FIELDS CAPTURED');
      return `${label}${detail ? ` · ${detail}` : ''}`;
    };
    const evidence = `K ${evidenceSummary(item.kalshi_evidence)} · P ${evidenceSummary(item.polymarket_evidence)}`;
    const guaranteeReasons = [
      ...(item.kalshi_guarantee?.reason_codes || []).slice(0, 2).map((code) => `K ${code}`),
      ...(item.polymarket_guarantee?.reason_codes || []).slice(0, 2).map((code) => `P ${code}`),
    ].map((code) => code.replaceAll('_', ' ')).join(' · ');
    return `<div class="settlement-rank"><b>${index + 1}</b><div><strong>${safe(item.kalshi_title)}</strong><span>↔ ${safe(item.polymarket_title)}</span></div><div class="settlement-rank-meta"><em class="${String(item.guarantee_status).toLowerCase()}">${safe(item.guarantee_status)}</em>${guaranteeReasons ? `<small class="settlement-guarantee-reason">${safe(guaranteeReasons)}</small>` : ''}<span>${safe(item.market_type || 'unknown')} · rule distance ${safe(item.rule_distance)}</span><span class="settlement-lifecycle">${safe(lifecycle)}</span><span class="settlement-time">READY BY · ${safe(readyAt)}</span><span class="settlement-time">VENUES · ${safe(settlementTimes)}</span><small>${safe(mismatches)}</small><small class="settlement-evidence">${safe(evidence)}</small></div></div>`;
  }).join('') : '<div class="empty">No shared event rankings found.</div>';
  const election = catalog?.election_discovery || {};
  $('election-status').textContent = sentence(election.status || 'NO_ELECTION_SCAN');
  $('election-kalshi').textContent = election.kalshi_contracts != null ? fmt(election.kalshi_contracts) : '—';
  $('election-polymarket').textContent = election.polymarket_contracts != null ? fmt(election.polymarket_contracts) : '—';
  $('election-shared').textContent = election.shared_events != null ? fmt(election.shared_events) : '—';
  $('election-deterministic').textContent = fmt(Number(election.deterministic_kalshi || 0) + Number(election.deterministic_polymarket || 0));
  $('election-detail').textContent = election.status === 'WAITING_FOR_KALSHI_COUNTERPART'
    ? 'Polymarket contracts passed normalization, but Kalshi has no open counterpart. Atlas will keep checking automatically.'
    : election.shared_events
      ? 'Both venues expose the same event. Exact settlement rules and executable books now decide eligibility.'
      : 'No shared live chamber-control event is available on both venues yet.';
  const electionCandidates = election.polymarket_candidates || [];
  $('election-candidates').innerHTML = electionCandidates.length ? electionCandidates.map((candidate) => {
    const ready = candidate.deterministic_settlement ? 'RULE-COMPLETE' : `BLOCKED: ${(candidate.blockers || []).join(' · ')}`;
    return `<div class="election-candidate"><div><strong>${safe(candidate.title)}</strong><span>${safe(candidate.event_subject)}</span></div><div><b>${safe(candidate.affirmative_outcome || 'unknown outcome')}</b><em class="${candidate.deterministic_settlement ? 'ready' : ''}">${safe(ready)}</em></div></div>`;
  }).join('') : '<div class="empty">No live chamber-control contracts found.</div>';
  $('candidates').innerHTML = proposals.length ? proposals.map((p) => { const mismatches = (p.mismatch_codes || []).map((code) => code.replace('_MISMATCH', '').replaceAll('_', ' ')).join(' · ') || 'LEXICAL REVIEW'; return `<div class="candidate"><div class="candidate-contracts"><strong>${safe(p.kalshi_title)}</strong><small>KALSHI: ${safe(terms(p.kalshi_terms))}</small><span>↔ ${safe(p.polymarket_title)}</span><small>POLYMARKET: ${safe(terms(p.polymarket_terms))}</small></div><div class="candidate-meta"><b>${pct(p.score)}</b><em>${safe(mismatches)}</em><span>${safe(p.review_kind || 'REVIEW REQUIRED')}</span></div></div>`; }).join('') : '<div class="empty">No same-event contract mismatches recorded.</div>';
  const o = data.opportunity;
  if (!o) { $('opportunity').innerHTML = '<div class="empty">No approved executable opportunities.</div>'; return; }
  const demo = Boolean(evidence.demo_opportunity);
  $('opportunity').innerHTML = `<div class="opp-top"><span class="match">${demo ? 'FIXTURE RESEARCH / DEMO EDGE' : 'MATCH FOUND / APPROVED EQUIVALENT'}</span><span class="badge badge--dot ${demo ? 'badge--warn' : 'badge--ok'}">${demo ? 'NOT LIVE' : `PAPER ${o.status}`}</span></div><div class="contracts">Kalshi <span class="arrow">↔</span> Polymarket US</div><div class="legs"><div class="leg"><span class="leg-label">KALSHI / YES EXECUTABLE</span><span class="price">${money(o.leg_a_average_price)}</span></div><div class="leg"><span class="leg-label">POLYMARKET US / NO HEDGE</span><span class="price">${money(o.leg_b_average_price)}</span></div><div class="metrics"><div class="metric"><span>NET LOCKED EDGE</span><strong class="positive">${pct(o.expected_roi)}</strong></div><div class="metric"><span>EXECUTABLE SIZE</span><strong>${money(o.contracts)}</strong></div><div class="metric"><span>NET COST</span><strong>${money(o.net_cost)}</strong></div></div></div>`;
}
async function refresh() {
  try {
    const response = await fetch('/api/overview', {cache:'no-store'});
    render(await response.json());
  } catch {
    $('connection').textContent = 'OFFLINE'; $('connection').classList.add('offline');
    $('updated').textContent = lastGoodUpdate ? `SHOWING DATA FROM ${age(lastGoodUpdate).toUpperCase()}` : 'NO DATA RECEIVED YET';
  }
}
$('refresh').addEventListener('click', refresh); refresh(); setInterval(refresh, 15000);
setInterval(() => {
  if (lastGoodUpdate && !$('connection').classList.contains('offline')) {
    $('updated').textContent = `UPDATED ${age(lastGoodUpdate).toUpperCase()}`;
  }
}, 5000);
