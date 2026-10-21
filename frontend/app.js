'use strict';
const $ = (id) => document.getElementById(id);
const number = (value, digits = 0) => value == null ? '—' : Number(value).toLocaleString(undefined, {maximumFractionDigits: digits, minimumFractionDigits: digits});
const ms = (value) => value == null ? '—' : `${number(value, 1)} ms`;
const percent = (value) => value == null ? '—' : `${number(value, 2)}%`;
const date = (value) => value == null ? 'No successful check in window' : new Date(value * 1000).toLocaleString();
let chartPoints = [];
let busy = false;
let lastReceived = 0;
let paused = false;
let latestTargets = [];
let latestEvents = [];
let eventFilter = 'all';

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function renderInstances(targets) {
  const cards = targets.filter((target) => target.target !== 'gateway').map((target) => {
    const card = element('article', null, 'instance-card');
    const header = element('div', null, 'instance-header');
    header.append(element('span', '▦', 'instance-icon'));
    const identity = element('div');
    identity.append(element('strong', target.target), element('small', 'FastAPI · Docker container'));
    header.append(identity, element('span', target.status, `badge ${target.status}`));
    const stats = element('dl', null, 'instance-stats');
    for (const [label, value] of [['Response', ms(target.current_latency_ms)], ['Average', ms(target.average_latency_ms)], ['Availability', percent(target.availability_percent)]]) {
      const stat = element('div');
      stat.append(element('dt', label), element('dd', value));
      stats.append(stat);
    }
    const foot = element('div', `Last success: ${date(target.last_success)} · ${number(target.total_checks)} checks`, 'instance-foot');
    if (target.last_error) foot.append(element('span', target.last_error, 'instance-error'));
    const inspect = element('button', 'Inspect instance ↗', 'inspect-button');
    inspect.type = 'button';
    inspect.addEventListener('click', () => showInstance(target.target));
    card.append(header, stats, foot, inspect);
    $(`topology-${target.target}`).className = target.status;
    return card;
  });
  $('instance-cards').replaceChildren(...cards);
}

function renderEvents(events) {
  const filtered = events.filter((event) => (eventFilter === 'all' || event.kind === eventFilter) && ($('event-target').value === 'all' || event.target === $('event-target').value));
  $('event-count').textContent = `${filtered.length} of ${events.length} recent events`;
  const rows = filtered.map((event) => {
    const row = element('tr');
    const type = element('td');
    type.append(element('span', event.kind === 'recovery' ? '↗ Recovery' : '! Incident', `badge ${event.kind}`));
    row.append(type, element('td', event.target), element('td', event.message), element('td', date(event.timestamp)));
    return row;
  });
  if (!rows.length) {
    const row = element('tr');
    const cell = element('td', events.length ? 'No events match these filters.' : 'No health transitions recorded. Incidents appear when a probe fails; recoveries appear when it succeeds again.', 'empty');
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
  const values = chartPoints.filter((point) => point.latency_ms != null);
  $('chart-empty').hidden = values.length > 0;
  if (!chartPoints.length) return;
  const max = Math.max(10, ...values.map((point) => point.latency_ms)) * 1.2;
  ctx.font = '9px Segoe UI';
  ctx.fillStyle = '#8d9ea8';
  for (let i = 0; i <= 3; i++) {
    const y = bottom - (bottom - top) * i / 3;
    ctx.strokeStyle = document.documentElement.dataset.theme === 'dark' ? '#26343e' : '#edf1f4';
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
    ctx.fillText(number(max * i / 3, 1), 1, y + 3);
  }
  const first = chartPoints[0].timestamp, last = chartPoints.at(-1).timestamp;
  const x = (point) => first === last ? (left + right) / 2 : left + (point.timestamp - first) / (last - first) * (right - left);
  const y = (point) => bottom - point.latency_ms / max * (bottom - top);
  ctx.strokeStyle = '#1ca684'; ctx.lineWidth = 2;
  ctx.beginPath();
  let connected = false;
  for (const point of chartPoints) {
    if (point.latency_ms == null) { connected = false; continue; }
    if (connected) ctx.lineTo(x(point), y(point)); else ctx.moveTo(x(point), y(point));
    connected = true;
  }
  ctx.stroke();
  for (const point of chartPoints) {
    if (point.latency_ms != null) {
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
  canvas.setAttribute('aria-label', `${chartPoints.length} latency buckets. ${chartPoints.reduce((sum, p) => sum + p.failures, 0)} failed probes. Red markers indicate failures.`);
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

async function refresh() {
  if (busy) return;
  busy = true; $('refresh').disabled = true;
  try {
    const hours = $('window').value;
    const chartTarget = $('chart-target').value;
    const [summary, history, incidents] = await Promise.all([get(`summary?hours=${hours}`), get(`history?hours=${hours}&target=${chartTarget}`), get('incidents')]);
    latestTargets = summary.targets;
    latestEvents = incidents.events;
    const gateway = summary.targets.find((target) => target.target === 'gateway');
    const descriptions = {
      healthy: ['All systems operational', 'Both replicas and the load-balanced application are responding.'],
      degraded: ['Service available · replica degraded', 'NGINX gateway is responding. An independent replica health check is failing.'],
      outage: ['Application gateway unavailable', 'The gateway probe failed. Inspect container status and the incident log.'],
      unknown: ['Waiting for fresh health checks', 'One or more targets have no recent sample. Unobserved time is not counted as available.']
    };
    const status = summary.overall_status;
    document.body.classList.toggle('data-stale', status === 'unknown');
    $('status-banner').className = `status-banner ${status}`;
    $('overall-title').textContent = descriptions[status][0];
    $('overall-detail').textContent = descriptions[status][1];
    $('status-symbol').textContent = status === 'healthy' ? '✓' : '!';
    $('live-label').textContent = status === 'unknown' ? 'STALE / PENDING' : 'LIVE MONITORING';
    $('availability').textContent = percent(gateway.availability_percent);
    $('availability-note').textContent = gateway.total_checks ? `${date(gateway.window_start)} → ${date(gateway.window_end)}` : 'No samples in selected window';
    $('current-latency').textContent = ms(gateway.current_latency_ms);
    $('average-latency').textContent = `Average: ${ms(gateway.average_latency_ms)} · successful probes`;
    $('successes').textContent = number(gateway.successful_checks);
    $('total-checks').textContent = `${number(gateway.total_checks)} total gateway checks`;
    $('failures').textContent = number(gateway.failed_checks);
    $('last-update').textContent = `Updated ${new Date(summary.generated_at * 1000).toLocaleTimeString()} · probes every ${summary.check_interval_seconds}s · refresh every 5s`;
    lastReceived = Date.now();
    renderInstances(summary.targets);
    renderEvents(incidents.events);
    if (paused) $('last-update').textContent += ' · AUTO-REFRESH PAUSED';
    $('chart-description').textContent = `${chartTarget} · successful probe averages · hover or focus to inspect`;
    chartPoints = history.points;
    $('chart-range').textContent = chartPoints.length ? `${date(chartPoints[0].timestamp)} → ${date(chartPoints.at(-1).last_timestamp)}` : 'No measurement window yet';
    $('chart-samples').textContent = `${number(chartPoints.reduce((sum, point) => sum + point.checks, 0))} probes · red = failure`;
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
