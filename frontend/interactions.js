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
  if (!target) return;
  const grid = element('div', null, 'detail-grid');
  for (const [label, value] of [['Status', target.status], ['Successful / total', `${target.successful_checks} / ${target.total_checks}`], ['Failed checks', target.failed_checks], ['Observed availability', percent(target.availability_percent)], ['Current latency', ms(target.current_latency_ms)], ['Average success latency', ms(target.average_latency_ms)]]) {
    const cell = element('div'); cell.append(element('small', label), element('strong', String(value))); grid.append(cell);
  }
  openDialog([element('h2', name), element('p', 'Independent FastAPI replica on the private Compose network. Measurements below are from the selected window.'), grid,
    element('p', `Last check: ${date(target.last_check)}`), element('p', `Last successful check in window: ${date(target.last_success)}`),
    element('p', `Latest error: ${target.last_error || 'None'}`)]);
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
  const nodes = [element('h2', 'Recovery workflow'), element('p', 'Check service continuity and restore a replica. Run these commands in PowerShell, in the CloudGuard project folder. Docker operations stay under local operator control.')];
  const states = element('div', null, 'guide-status');
  latestTargets.forEach((target) => states.append(element('span', `${target.target}: ${target.status}`, `badge ${target.status}`)));
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
  $('chart-tooltip').textContent = `${date(point.timestamp)} · ${ms(point.latency_ms)} · ${point.checks} checks · ${point.failures} failed`;
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
['overview','instances','history','incidents'].forEach((id) => sectionObserver.observe($(id)));
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
