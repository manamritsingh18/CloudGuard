'use strict';
let toastTimer;
function toast(message) {
  $('toast').textContent = message; $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3500);
}
function openDialog(nodes) {
  $('dialog-content').replaceChildren(...nodes);
  if (!$('detail-dialog').open) $('detail-dialog').showModal();
}
function showInstance(name) {
  const target = latestTargets.find((item) => item.target === name);
  const settings = targetConfig(name);
  if (!target || !settings) return;
  const grid = element('div', null, 'detail-grid');
  for (const [label, value] of [['Status', target.status], ['Successful / total', `${target.successful_checks} / ${target.total_checks}`], ['Failed checks', target.failed_checks], ['Observed availability', percent(target.availability_percent)], ['Current latency', ms(target.current_latency_ms)], ['Average success latency', ms(target.average_latency_ms)]]) {
    const cell = element('div'); cell.append(element('small', label), element('strong', String(value))); grid.append(cell);
  }
  const metrics = Object.entries(target.latest_metrics || {}).map(([metric, value]) => `${metricLabel(metric)} ${withUnit(value, metricUnit(metric))}`).join(' · ');
  openDialog([element('h2', settings.name), element('p', `${[settings.provider, settings.service_model].filter(Boolean).join(' · ')}${settings.critical ? ' · critical' : ' · non-critical'}. ${settings.description}`), grid,
    element('p', `Probe: ${settings.url} · ${every(settings.interval_seconds)} · timeout ${number(settings.timeout_seconds, 1)} s`),
    element('p', `Last check: ${date(target.last_check)}${target.last_status_code ? ` · HTTP ${target.last_status_code}` : ''}`), element('p', `Last successful check in window: ${date(target.last_success)}`),
    element('p', `Latest recorded metrics: ${metrics || 'None'}`), element('p', `Latest error: ${target.last_error || 'None'}`)]);
}
function showSlo(id) {
  const slo = latestSlos.find((item) => item.id === id);
  if (!slo) return;
  const [label, className] = SLO_STATUS[slo.status];
  const grid = element('div', null, 'detail-grid');
  for (const [name, value] of [['Attained', percent(slo.attainment_percent, 3)], ['Goal (SLO)', `${upTo(slo.goal_percent, 3)}% over ${windowText(slo.window_hours)}`], ['Error budget left', slo.budget_remaining_percent == null ? '—' : `${number(slo.budget_remaining_percent, 1)}%`], [`Burn rate (last ${upTo(slo.burn_window_hours, 2)} h)`, slo.burn_rate == null ? '—' : `${number(slo.burn_rate, 2)}×`]]) {
    const cell = element('div'); cell.append(element('small', name), element('strong', value)); grid.append(cell);
  }
  const heading = element('p');
  heading.append(element('span', label, `badge ${className}`), document.createTextNode(` ${slo.target_name} · ${sliText(slo)}. ${slo.description || ''}`));
  const nodes = [element('h2', slo.name), heading, grid];
  if (!slo.valid_checks) {
    nodes.push(element('p', 'No valid checks recorded in this window yet, so nothing is calculated. CloudGuard never fills gaps with assumed successes.'));
    openDialog(nodes);
    return;
  }
  const allowed = slo.allowed_bad_fraction * 100;
  const good = {availability: 'healthy checks', latency: `healthy checks within ${number(slo.threshold_ms)} ms`, metric: 'checks within the limit'}[slo.objective];
  const valid = {availability: 'all checks', latency: 'healthy checks', metric: 'checks that reported the metric'}[slo.objective];
  const steps = element('ol', null, 'calculation');
  const step = (text) => steps.append(element('li', text));
  step(`SLI = ${good} ÷ ${valid} = ${number(slo.good_checks)} ÷ ${number(slo.valid_checks)} = ${percent(slo.attainment_percent, 3)}.`);
  step(`Error budget = ${upTo(allowed, 3)}% (100% − ${upTo(slo.goal_percent, 3)}% goal) of ${number(slo.valid_checks)} checks = ${number(slo.budget_checks, 2)} bad checks allowed.`);
  step(`${number(slo.bad_checks)} bad checks so far → ${number(100 - slo.budget_remaining_percent, 1)}% of the budget used, ${number(slo.budget_remaining_percent, 1)}% left. Below 0% the SLO is breached; below 25% it is at risk.`);
  step(slo.burn_valid_checks ? `Burn rate over the last ${upTo(slo.burn_window_hours, 2)} h = ${number(slo.burn_bad_checks)} bad of ${number(slo.burn_valid_checks)} checks (${percent(100 * slo.burn_bad_checks / slo.burn_valid_checks)}) ÷ ${upTo(allowed, 3)}% allowed = ${number(slo.burn_rate, 2)}×. 1× spends exactly the whole budget over ${windowText(slo.window_hours)}; 2× or more is flagged.` : `No checks in the last ${upTo(slo.burn_window_hours, 2)} h, so no burn rate.`);
  if (slo.objective === 'availability') step(`Measured continuously, this budget is ${number(slo.allowed_bad_minutes, 1)} minutes of downtime per ${windowText(slo.window_hours)}. The ${number(slo.bad_checks)} failed checks stand for roughly ${number(slo.estimated_bad_minutes, 1)} minutes (one probe interval each).`);
  if (slo.sla_percent != null) step(`SLA (the promise to users) = ${upTo(slo.sla_percent, 3)}%, looser than the internal ${upTo(slo.goal_percent, 3)}% goal, so the team is warned before the promise is broken. Currently ${slo.sla_met ? 'kept' : 'broken'}.`);
  step(`Samples cover ${duration(slo.observed_hours)} of the ${windowText(slo.window_hours)} window (${percent(slo.coverage_percent, 1)}). Time the monitor was off is not counted either way, so early results can swing a lot.`);
  nodes.push(element('h3', 'How this is calculated'), steps);
  openDialog(nodes);
}
function commandBox(command) {
  const box = element('div', null, 'command-box');
  const copy = element('button', 'Copy'); copy.type = 'button';
  copy.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(command); copy.textContent = 'Copied ✓'; toast('Command copied. Run it in PowerShell in the project folder.'); }
    catch { copy.textContent = 'Select command to copy'; }
  });
  box.append(element('code', command), copy); return box;
}
$('demo-guide').addEventListener('click', () => {
  if (configuration && selectedPlane !== LAB_PLANE) selectPlane(LAB_PLANE);
  const nodes = [element('h2', 'Recovery workflow'), element('p', 'Check service continuity and restore a replica. Run these commands in PowerShell, in the CloudGuard project folder. Docker operations stay under local operator control.')];
  const states = element('div', null, 'guide-status');
  latestTargets.filter((target) => target.plane === LAB_PLANE).forEach((target) => states.append(element('span', `${target.target}: ${target.status}`, `badge ${target.status}`)));
  nodes.push(states);
  for (const [title, description, command] of [
    ['01 / Establish the baseline', 'Both replicas should be healthy. Use “Send request” several times to see their identities.', 'docker compose ps'],
    ['02 / Stop one replica', 'Wait about 5–15 seconds. app-1 becomes unhealthy; the service should remain available through app-2.', 'docker compose stop app1'],
    ['03 / Restore the replica', 'Wait for the next successful probe and the recovery event. Load balancing resumes across both replicas.', 'docker compose start app1']
  ]) {
    const step = element('section', null, 'guide-step'); step.append(element('h3', title), element('p', description), commandBox(command)); nodes.push(step);
  }
  nodes.push(element('p', 'Detection, traffic failover, and container restart are different mechanisms. A deliberately stopped container stays stopped until you start it. Historical failed samples remain in the availability calculation.'));
  openDialog(nodes);
});
$('close-dialog').addEventListener('click', () => $('detail-dialog').close());
$('detail-dialog').addEventListener('click', (event) => { if (event.target === $('detail-dialog') && (event.offsetX < 0 || event.offsetY < 0 || event.offsetX > event.target.clientWidth || event.offsetY > event.target.clientHeight)) event.target.close(); });
$('theme-toggle').addEventListener('click', () => {
  const dark = document.documentElement.dataset.theme !== 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  $('theme-toggle').setAttribute('aria-label', `Switch to ${dark ? 'light' : 'dark'} theme`);
  try { localStorage.setItem('cloudguard-theme', dark ? 'dark' : 'light'); } catch {}
  drawChart();
});
try { const saved = localStorage.getItem('cloudguard-theme'); if (saved === 'light') $('theme-toggle').click(); } catch {}
$('present-toggle').addEventListener('click', () => {
  const enabled = document.body.classList.toggle('presentation');
  $('present-toggle').setAttribute('aria-pressed', String(enabled));
  $('present-toggle').textContent = enabled ? 'Exit focus mode' : 'Focus mode';
  requestAnimationFrame(drawChart);
});
$('pause-toggle').addEventListener('click', () => {
  paused = !paused;
  document.body.classList.toggle('paused', paused);
  $('pause-toggle').setAttribute('aria-pressed', String(paused));
  $('pause-toggle').textContent = paused ? '▷ Resume refresh' : 'Ⅱ Pause refresh';
  if (paused) {
    $('last-update').textContent += ' · AUTO-REFRESH PAUSED';
    toast('Dashboard refresh paused. Monitoring continues recording in SQLite.');
  } else refresh();
});
$('chart-target').addEventListener('change', refresh);
$('event-target').addEventListener('change', () => renderEvents(latestEvents));
document.querySelectorAll('[data-event-filter]').forEach((button) => button.addEventListener('click', () => {
  eventFilter = button.dataset.eventFilter;
  document.querySelectorAll('[data-event-filter]').forEach((item) => { item.classList.toggle('selected', item === button); item.setAttribute('aria-pressed', String(item === button)); });
  renderEvents(latestEvents);
}));
$('send-request').addEventListener('click', async () => {
  const button = $('send-request'); button.disabled = true;
  const start = performance.now();
  try {
    const response = await fetch('/api/app/', {cache:'no-store', signal:AbortSignal.timeout(8000)});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    $('request-result').replaceChildren(element('span', '200 OK', 'console-prompt'), element('strong', data.instance), element('span', `${(performance.now() - start).toFixed(1)} ms · browser round trip`), element('span', data.message, 'console-hint'));
  } catch (error) {
    $('request-result').replaceChildren(element('span', 'REQUEST FAILED', 'instance-error'), element('span', error.message));
  } finally { button.disabled = false; }
});
function chartTooltip(index) {
  const point = chartPoints[index]; if (!point) return;
  $('chart-tooltip').textContent = `${date(point.timestamp)} · ${withUnit(point.value, chartUnit)} · ${point.checks} checks · ${point.failures} failed`;
  $('chart-tooltip').hidden = false;
}
$('latency-chart').addEventListener('pointermove', (event) => {
  if (!chartPoints.length) return;
  const rect = event.currentTarget.getBoundingClientRect();
  const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left - 44) / (rect.width - 54)));
  const timestamp = chartPoints[0].timestamp + fraction * (chartPoints.at(-1).timestamp - chartPoints[0].timestamp);
  let nearest = 0;
  chartPoints.forEach((point, index) => { if (Math.abs(point.timestamp - timestamp) < Math.abs(chartPoints[nearest].timestamp - timestamp)) nearest = index; });
  chartTooltip(nearest);
});
$('latency-chart').addEventListener('pointerleave', () => { $('chart-tooltip').hidden = true; });
let keyboardPoint = 0;
$('latency-chart').addEventListener('focus', () => { keyboardPoint = Math.max(0, chartPoints.length - 1); chartTooltip(keyboardPoint); });
$('latency-chart').addEventListener('keydown', (event) => {
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); keyboardPoint = Math.max(0, Math.min(chartPoints.length - 1, keyboardPoint + (event.key === 'ArrowLeft' ? -1 : 1))); chartTooltip(keyboardPoint); }
});
$('latency-chart').addEventListener('blur', () => { $('chart-tooltip').hidden = true; });
const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
document.addEventListener('pointermove', (event) => {
  if (motionQuery.matches || !window.matchMedia('(pointer:fine)').matches) return;
  const card = event.target.closest('.metric');
  if (card) { const rect = card.getBoundingClientRect(); card.style.setProperty('--px', `${event.clientX - rect.left}px`); card.style.setProperty('--py', `${event.clientY - rect.top}px`); }
});
const sectionObserver = new IntersectionObserver((entries) => {
  entries.forEach((entry) => { if (entry.isIntersecting) document.querySelectorAll('nav a[href^="#"]').forEach((link) => link.classList.toggle('active', link.hash === `#${entry.target.id}`)); });
}, {rootMargin:'-15% 0px -65% 0px'});
['overview','instances','slos','history','incidents'].forEach((id) => sectionObserver.observe($(id)));
if (new URLSearchParams(window.location.search).get('guide') === 'recovery') $('demo-guide').click();
$('audit-trail').addEventListener('click', async () => {
  const content = element('div', 'Loading recorded operator actions…', 'audit-content');
  openDialog([element('h2', 'Operator audit trail'), element('p', 'Actions recorded by the local replica script. Direct Docker commands appear as health transitions only. The actor is local-cli; this is not an authenticated identity log.'), content]);
  try {
    const result = await get('audit?limit=30');
    content.replaceChildren();
    if (!result.actions.length) content.append(element('p', 'No operator actions recorded. Use scripts/replica.ps1 to perform audited actions.'));
    for (const action of result.actions) {
      const row = element('div', null, 'audit-row');
      row.append(element('strong', `${action.action} / ${action.target}`), element('span', action.outcome, `badge ${action.outcome === 'failed' ? 'incident' : 'recovery'}`), element('small', `${date(action.timestamp)} · ${action.actor}`));
      content.append(row);
    }
  } catch (error) { content.textContent = `Audit data unavailable: ${error.message}`; }
});
