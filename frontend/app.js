'use strict';
const $ = (id) => document.getElementById(id);
const number = (value, digits = 0) => value == null ? '—' : Number(value).toLocaleString(undefined, {maximumFractionDigits: digits, minimumFractionDigits: digits});
const upTo = (value, digits) => value == null ? '—' : Number(value).toLocaleString(undefined, {maximumFractionDigits: digits});
const ms = (value) => value == null ? '—' : `${number(value, 1)} ms`;
const percent = (value, digits = 2) => value == null ? '—' : `${number(value, digits)}%`;
const date = (value) => value == null ? 'No successful check in window' : new Date(value * 1000).toLocaleString();
const LAB_PLANE = 'lab';
const SLO_STATUS = {met: ['Met', 'healthy'], at_risk: ['At risk', 'stale'], breached: ['Breached', 'unhealthy'], no_data: ['No data', '']};
let chartPoints = [];
let chartUnit = 'ms';
let busy = false;
let lastReceived = 0;
let paused = false;
let configuration = null;
let selectedPlane = null;
let latestTargets = [];
let latestPlanes = [];
let latestEvents = [];
let latestSlos = [];
let eventFilter = 'all';

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function metricUnit(name) {
  if (name.endsWith('_ms')) return 'ms';
  if (name.endsWith('_s')) return 's';
  return name.includes('days') ? 'days' : '';
}
const metricLabel = (name) => name.replace(/_(ms|s)$/, '').replaceAll('_', ' ');
function withUnit(value, unit) {
  if (value == null) return '—';
  if (unit === 'ms') return ms(value);
  if (unit === 's') return value >= 86400 ? `${number(value / 86400, 1)} d` : value >= 3600 ? `${number(value / 3600, 1)} h` : `${number(value)} s`;
  if (unit === 'days') return `${number(value, 1)} days`;
  return number(value, Number.isInteger(value) ? 0 : 2);
}
const duration = (hours) => hours < 1 ? `${number(hours * 60)} min` : hours < 48 ? `${number(hours, 1)} h` : `${number(hours / 24, 1)} d`;
const every = (seconds) => seconds >= 60 ? `every ${number(seconds / 60, 1)} min` : `every ${number(seconds)} s`;
const windowText = (hours) => hours % 24 === 0 ? `${hours / 24} d` : `${hours} h`;
const targetConfig = (id) => configuration?.targets.find((target) => target.id === id);
const targetName = (id) => targetConfig(id)?.name ?? id;
const currentPlane = () => latestPlanes.find((plane) => plane.id === selectedPlane) ?? configuration.planes.find((plane) => plane.id === selectedPlane);

function setupConfiguration() {
  const saved = (() => { try { return localStorage.getItem('cloudguard-plane'); } catch { return null; } })();
  const params = new URLSearchParams(window.location.search);
  const requested = params.get('guide') === 'recovery' ? LAB_PLANE : params.get('plane') || saved;
  selectedPlane = configuration.planes.some((plane) => plane.id === requested) ? requested : configuration.planes[0].id;
  $('plane-switch').replaceChildren(...configuration.planes.map((plane) => {
    const button = element('button', null);
    button.type = 'button';
    button.dataset.plane = plane.id;
    button.append(element('i', null, 'plane-dot'), element('span', plane.name));
    button.addEventListener('click', () => selectPlane(plane.id));
    return button;
  }));
  $('chart-target').replaceChildren(...configuration.planes.map((plane) => {
    const group = element('optgroup');
    group.label = plane.name;
    for (const target of configuration.targets.filter((item) => item.plane === plane.id)) {
      group.append(new Option(`${target.name} · response time`, target.id));
      // Certificate days are on the cards and in the TLS objective; one chart per probe would be noise.
      target.metrics.filter((metric) => metric !== 'tls_days_left').forEach((metric) => group.append(new Option(`${target.name} · ${metricLabel(metric)}`, `${target.id}|${metric}`)));
    }
    return group;
  }));
  applyPlane();
}

function applyPlane() {
  const plane = configuration.planes.find((item) => item.id === selectedPlane);
  document.querySelectorAll('#plane-switch button').forEach((button) => {
    button.classList.toggle('selected', button.dataset.plane === selectedPlane);
    button.setAttribute('aria-pressed', String(button.dataset.plane === selectedPlane));
  });
  $('chart-target').value = plane.headline;
  const lab = selectedPlane === LAB_PLANE;
  $('request-panel').hidden = !lab;
  $('lab-flow').hidden = !lab;
  $('provider-map').hidden = lab;
  $('topology-title').textContent = lab ? 'Traffic flow' : 'Cloud providers';
  $('topology-description').textContent = lab ? 'One entry point. Two independent replicas.' : 'Where each monitored service runs';
  $('services-title').textContent = lab ? 'Application instances' : 'Monitored services';
  $('services-description').textContent = plane.description;
  const components = configuration.targets.filter((target) => target.plane === selectedPlane);
  $('event-target').replaceChildren(new Option('All components', 'all'), ...components.map((target) => new Option(target.name, target.id)));
}

function selectPlane(id) {
  selectedPlane = id;
  try { localStorage.setItem('cloudguard-plane', id); } catch {}
  applyPlane();
  refresh();
}

function renderInstances() {
  const members = latestTargets.filter((target) => target.plane === selectedPlane);
  $('service-count').textContent = `${members.filter((target) => target.status === 'healthy').length} of ${members.length} healthy`;
  const cards = members.map((target) => {
    const card = element('article', null, 'instance-card');
    const header = element('div', null, 'instance-header');
    header.append(element('span', selectedPlane === LAB_PLANE ? '▦' : '◈', 'instance-icon'));
    const identity = element('div');
    identity.append(element('strong', target.name), element('small', [target.provider, target.service_model, target.critical ? null : 'non-critical'].filter(Boolean).join(' · ')));
    header.append(identity, element('span', target.status, `badge ${target.status}`));
    const stats = element('dl', null, 'instance-stats');
    for (const [label, value] of [['Response', ms(target.current_latency_ms)], ['Average', ms(target.average_latency_ms)], ['Availability', percent(target.availability_percent)]]) {
      const stat = element('div');
      stat.append(element('dt', label), element('dd', value));
      stats.append(stat);
    }
    const chips = element('div', null, 'metric-chips');
    Object.entries(target.latest_metrics || {}).sort(([a], [b]) => (a === 'tls_days_left') - (b === 'tls_days_left')).forEach(([name, value]) => {
      const chip = element('span', null, name === 'tls_days_left' && value < 21 ? 'chip warn' : 'chip');
      chip.append(element('small', name === 'tls_days_left' ? 'TLS cert' : metricLabel(name)), document.createTextNode(` ${withUnit(value, metricUnit(name))}`));
      chips.append(chip);
    });
    const foot = element('div', `Last success: ${date(target.last_success)} · ${number(target.total_checks)} checks · ${every(target.interval_seconds)}`, 'instance-foot');
    if (target.status !== 'healthy' && target.last_error) foot.append(element('span', target.last_error, 'instance-error'));
    const inspect = element('button', 'Inspect service ↗', 'inspect-button');
    inspect.type = 'button';
    inspect.addEventListener('click', () => showInstance(target.target));
    card.append(header, stats);
    if (chips.childElementCount) card.append(chips);
    card.append(foot, inspect);
    const topologyDot = $(`topology-${target.target}`);
    if (topologyDot) topologyDot.className = target.status;
    return card;
  });
  $('instance-cards').replaceChildren(...(cards.length ? cards : [element('p', 'No targets configured for this environment.', 'empty')]));
}

function renderProviders() {
  const members = latestTargets.filter((target) => target.plane === selectedPlane);
  const providers = [...new Set(members.map((target) => target.provider || 'Other'))];
  $('provider-map').replaceChildren(...providers.map((provider) => {
    const node = element('div', null, 'provider-node');
    const list = element('ul');
    members.filter((target) => (target.provider || 'Other') === provider).forEach((target) => {
      const item = element('li');
      item.append(element('i', null, target.status), document.createTextNode(target.name));
      list.append(item);
    });
    node.append(element('strong', provider), list);
    return node;
  }));
}

function sliText(slo) {
  if (slo.objective === 'availability') return 'Share of checks that were healthy';
  if (slo.objective === 'latency') return `Healthy checks answered within ${number(slo.threshold_ms)} ms`;
  const unit = metricUnit(slo.metric);
  const limit = (value) => `${number(value)}${unit ? ` ${unit}` : ''}`;
  const bounds = [slo.min != null ? `≥ ${limit(slo.min)}` : null, slo.max != null ? `≤ ${limit(slo.max)}` : null].filter(Boolean).join(' and ');
  return `${slo.metric === 'tls_days_left' ? 'Certificate validity' : metricLabel(slo.metric)} ${bounds}`;
}

function renderSlos() {
  const slos = latestSlos.filter((slo) => slo.plane === selectedPlane);
  $('slo-count').textContent = slos.length ? `${slos.filter((slo) => slo.status === 'met').length} of ${slos.length} met` : 'None';
  const rows = slos.map((slo) => {
    const row = element('tr', null, 'slo-row');
    row.tabIndex = 0;
    row.setAttribute('aria-label', `${slo.name}: ${SLO_STATUS[slo.status][0]}. Show calculation.`);
    row.addEventListener('click', () => showSlo(slo.id));
    row.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); showSlo(slo.id); } });
    const objective = element('td');
    objective.append(element('strong', slo.name), element('small', sliText(slo)));
    const goal = element('td');
    goal.append(element('strong', `${upTo(slo.goal_percent, 3)}% · ${windowText(slo.window_hours)}`),
      element('small', `${slo.sla_percent != null ? `SLA ${upTo(slo.sla_percent, 3)}% · ` : ''}${duration(slo.observed_hours)} of ${windowText(slo.window_hours)} observed`));
    const attained = element('td');
    attained.append(element('strong', percent(slo.attainment_percent, 3)), element('small', `${number(slo.valid_checks)} checks · ${number(slo.bad_checks)} bad`));
    const budget = element('td');
    const bar = element('span', null, 'budget-bar');
    const fill = element('i', null, SLO_STATUS[slo.status][1]);
    fill.style.width = `${Math.max(0, Math.min(100, slo.budget_remaining_percent ?? 0))}%`;
    bar.append(fill);
    const remaining = slo.budget_remaining_percent;
    budget.append(bar, element('small', remaining == null ? '—' : remaining < 0 ? `Exhausted (${number(remaining)}%)` : `${number(remaining, 1)}% left`));
    const burn = element('td', slo.burn_rate == null ? '—' : `${number(slo.burn_rate, 2)}×`);
    const status = element('td');
    status.append(element('span', SLO_STATUS[slo.status][0], `badge ${SLO_STATUS[slo.status][1]}`));
    row.append(objective, element('td', slo.target_name), goal, attained, budget, burn, status);
    return row;
  });
  if (!rows.length) {
    const row = element('tr');
    const cell = element('td', 'No objectives for this environment. Add them under slos: in monitor/targets.yaml.', 'empty');
    cell.colSpan = 7;
    row.append(cell);
    rows.push(row);
  }
  $('slo-rows').replaceChildren(...rows);
}

function renderEvents(events) {
  const planeEvents = events.filter((event) => targetConfig(event.target)?.plane === selectedPlane);
  const filtered = planeEvents.filter((event) => (eventFilter === 'all' || event.kind === eventFilter) && ($('event-target').value === 'all' || event.target === $('event-target').value));
  $('event-count').textContent = `${filtered.length} of ${planeEvents.length} recent events`;
  const rows = filtered.slice(0, 30).map((event) => {
    const row = element('tr');
    const type = element('td');
    type.append(element('span', event.kind === 'recovery' ? '↗ Recovery' : '! Incident', `badge ${event.kind}`));
    const component = element('td', targetName(event.target));
    component.title = event.target;
    row.append(type, component, element('td', event.message), element('td', date(event.timestamp)));
    return row;
  });
  if (!rows.length) {
    const row = element('tr');
    const cell = element('td', planeEvents.length ? 'No events match these filters.' : 'No health transitions recorded. Incidents appear when a probe fails; recoveries appear when it succeeds again.', 'empty');
    cell.colSpan = 4;
    row.append(cell);
    rows.push(row);
  }
  $('event-rows').replaceChildren(...rows);
}

function drawChart() {
  const canvas = $('latency-chart');
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * ratio);
  canvas.height = Math.round(rect.height * ratio);
  const ctx = canvas.getContext('2d');
  ctx.scale(ratio, ratio);
  const left = 44, right = rect.width - 10, top = 14, bottom = rect.height - 28;
  const values = chartPoints.filter((point) => point.value != null);
  $('chart-empty').hidden = values.length > 0;
  if (!chartPoints.length) return;
  const max = Math.max(10, ...values.map((point) => point.value)) * 1.2;
  ctx.font = '9px Segoe UI';
  ctx.fillStyle = '#8d9ea8';
  for (let i = 0; i <= 3; i++) {
    const y = bottom - (bottom - top) * i / 3;
    ctx.strokeStyle = document.documentElement.dataset.theme === 'dark' ? '#26343e' : '#edf1f4';
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
    ctx.fillText(number(max * i / 3, max >= 100 ? 0 : 1), 1, y + 3);
  }
  const first = chartPoints[0].timestamp, last = chartPoints.at(-1).timestamp;
  const x = (point) => first === last ? (left + right) / 2 : left + (point.timestamp - first) / (last - first) * (right - left);
  const y = (point) => bottom - point.value / max * (bottom - top);
  ctx.strokeStyle = '#1ca684'; ctx.lineWidth = 2;
  ctx.beginPath();
  let connected = false;
  for (const point of chartPoints) {
    if (point.value == null) { connected = false; continue; }
    if (connected) ctx.lineTo(x(point), y(point)); else ctx.moveTo(x(point), y(point));
    connected = true;
  }
  ctx.stroke();
  for (const point of chartPoints) {
    if (point.value != null) {
      ctx.fillStyle = '#1ca684'; ctx.beginPath(); ctx.arc(x(point), y(point), 2.5, 0, Math.PI * 2); ctx.fill();
    }
    if (point.failures) {
      ctx.fillStyle = '#cd5364'; ctx.fillRect(x(point) - 2, bottom - 4, 4, 4);
    }
  }
  ctx.fillStyle = '#8d9ea8';
  const timeLabel = (timestamp) => new Date(timestamp * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
  ctx.textAlign = 'left'; ctx.fillText(timeLabel(first), left, bottom + 19);
  ctx.textAlign = 'right'; ctx.fillText(timeLabel(last), right, bottom + 19);
  canvas.setAttribute('aria-label', `${chartPoints.length} buckets of ${$('chart-description').textContent}. ${chartPoints.reduce((sum, p) => sum + p.failures, 0)} failed probes. Red markers indicate failures.`);
}

function markUnavailable(message) {
  document.body.classList.add('data-stale');
  $('status-banner').className = 'status-banner error';
  $('overall-title').textContent = 'Monitoring data unavailable';
  $('overall-detail').textContent = message;
  $('status-symbol').textContent = '!';
  $('live-label').textContent = 'DISCONNECTED';
  $('last-update').textContent = lastReceived ? `Last received ${new Date(lastReceived).toLocaleTimeString()} · displayed data is stale` : 'Waiting for first connection';
}

async function get(path) {
  const response = await fetch(`/api/monitor/${path}`, {cache:'no-store', signal:AbortSignal.timeout(8000)});
  if (!response.ok) throw new Error(`Monitoring returned HTTP ${response.status}`);
  return response.json();
}

function renderOverview(summary) {
  const plane = currentPlane();
  const members = summary.targets.filter((target) => target.plane === plane.id);
  const names = members.filter((target) => target.status !== 'healthy').map((target) => target.name).join(', ');
  const descriptions = {
    healthy: [`${plane.name}: all checks passing`, `${members.length} independent probes are responding as expected.`],
    degraded: [`${plane.name}: degraded`, `Non-critical check failing: ${names}. Critical services are responding.`],
    outage: [`${plane.name}: critical service failing`, `Failing: ${names}. Inspect the service cards and the incident log.`],
    unknown: [`${plane.name}: waiting for fresh checks`, `No recent sample from: ${names || 'any target'}. Unobserved time is not counted as available.`]
  };
  const status = plane.status;
  document.body.classList.toggle('data-stale', status === 'unknown');
  $('status-banner').className = `status-banner ${status}`;
  $('overall-title').textContent = descriptions[status][0];
  $('overall-detail').textContent = descriptions[status][1];
  $('status-symbol').textContent = status === 'healthy' ? '✓' : '!';
  $('live-label').textContent = status === 'unknown' ? 'STALE / PENDING' : 'LIVE MONITORING';
  document.querySelectorAll('#plane-switch button').forEach((button) => {
    button.querySelector('.plane-dot').className = `plane-dot ${summary.planes.find((item) => item.id === button.dataset.plane)?.status ?? ''}`;
  });
  const headline = members.find((target) => target.target === plane.headline);
  $('availability').textContent = percent(headline.availability_percent);
  $('availability-note').textContent = headline.total_checks ? `${headline.name} · ${date(headline.window_start)} → ${date(headline.window_end)}` : `${headline.name} · no samples in selected window`;
  $('current-latency').textContent = ms(headline.current_latency_ms);
  $('average-latency').textContent = `${headline.name} average: ${ms(headline.average_latency_ms)} · successful probes`;
  $('successes').textContent = number(headline.successful_checks);
  $('total-checks').textContent = `${number(headline.total_checks)} total ${headline.name} checks`;
  $('failures').textContent = number(headline.failed_checks);
  $('failures-note').textContent = `${headline.name} failures in selected window`;
  const fastest = Math.min(...members.map((target) => target.interval_seconds));
  $('last-update').textContent = `Updated ${new Date(summary.generated_at * 1000).toLocaleTimeString()} · probes ${every(fastest)} or slower · refresh every 5s`;
}

async function refresh() {
  if (busy) return;
  busy = true; $('refresh').disabled = true;
  try {
    if (!configuration) {
      configuration = await get('targets');
      setupConfiguration();
    }
    const hours = $('window').value;
    const [chartTarget, chartMetric] = $('chart-target').value.split('|');
    const historyPath = `history?hours=${hours}&target=${encodeURIComponent(chartTarget)}${chartMetric ? `&metric=${encodeURIComponent(chartMetric)}` : ''}`;
    const [summary, history, incidents, slos] = await Promise.all([get(`summary?hours=${hours}`), get(historyPath), get('incidents?limit=100'), get('slos')]);
    latestTargets = summary.targets;
    latestPlanes = summary.planes;
    latestEvents = incidents.events;
    latestSlos = slos.slos;
    lastReceived = Date.now();
    renderOverview(summary);
    renderInstances();
    renderProviders();
    renderSlos();
    renderEvents(latestEvents);
    if (paused) $('last-update').textContent += ' · AUTO-REFRESH PAUSED';
    $('chart-title').textContent = chartMetric ? 'Recorded metric' : 'Response time';
    $('chart-description').textContent = `${targetName(chartTarget)} · ${chartMetric ? `${metricLabel(chartMetric)} reported by the service` : 'successful probe averages'} · hover or focus to inspect`;
    chartPoints = history.points;
    chartUnit = history.unit;
    $('chart-range').textContent = chartPoints.length ? `${date(chartPoints[0].timestamp)} → ${date(chartPoints.at(-1).last_timestamp)}` : 'No measurement window yet';
    $('chart-samples').textContent = `${number(chartPoints.reduce((sum, point) => sum + point.checks, 0))} probes · ${chartUnit || 'value'} · red = failure`;
    drawChart();
  } catch (error) {
    markUnavailable(`${error.message}. Reconnecting automatically.`);
  } finally {
    busy = false; $('refresh').disabled = false;
  }
}
$('refresh').addEventListener('click', refresh);
$('window').addEventListener('change', refresh);
window.addEventListener('resize', drawChart);
refresh();
setInterval(() => { if (!paused) refresh(); }, 5000);
