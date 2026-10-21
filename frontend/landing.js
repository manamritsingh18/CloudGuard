'use strict';
const componentInfo = {
  browser: ['01 / BROWSER', 'One URL to the whole system.', 'The browser opens the landing page and dashboard through localhost:8080. Requests to the application and monitoring APIs go through the same NGINX entry point.'],
  nginx: ['02 / NGINX', 'A traffic director with a second route.', 'NGINX balances requests between app-1 and app-2. For eligible connection errors and gateway failures, it can retry the other upstream. It also serves this page and the dashboard.'],
  replicas: ['03 / APPLICATION REPLICAS', 'Same application. Independent containers.', 'Two FastAPI processes run in separate containers and return their own instance identity. Stopping one does not stop the other. The restart policy handles unexpected process exits; a deliberate stop requires an explicit start.'],
  monitor: ['04 / MONITORING + STORAGE', 'Measure the service. Keep the evidence.', 'One worker probes each replica and the NGINX application route every five seconds by default. SQLite persists latency samples and health transitions. Availability equals successful recorded probes divided by all recorded probes.']
};
document.querySelectorAll('[data-node]').forEach((button) => button.addEventListener('click', () => {
  document.querySelectorAll('[data-node]').forEach((item) => {item.classList.toggle('selected', item === button); item.setAttribute('aria-pressed', String(item === button));});
  const [label, title, description] = componentInfo[button.dataset.node];
  document.getElementById('node-label').textContent = label;
  document.getElementById('node-title').textContent = title;
  document.getElementById('node-description').textContent = description;
}));
const audiences = {
  overview: ['Every component, connected.', 'Trace a request, inspect each instance, and compare measured latency. Independent probes distinguish replica health from gateway availability.'],
  operations: ['Investigate. Restore. Verify.', 'Review the failing component, check whether the gateway is still serving requests, and use the recovery guide to restore the replica. Confirm a successful probe and recovery event before closing the incident.']
};
document.querySelectorAll('[data-audience]').forEach((button) => button.addEventListener('click', () => {
  document.querySelectorAll('[data-audience]').forEach((item) => {item.classList.toggle('selected', item === button); item.setAttribute('aria-pressed', String(item === button));});
  const [title, description] = audiences[button.dataset.audience];
  document.getElementById('audience-title').textContent = title;
  document.getElementById('audience-description').textContent = description;
}));
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
document.addEventListener('pointermove', (event) => {
  if (reducedMotion.matches || !window.matchMedia('(pointer:fine)').matches) return;
  const card = event.target.closest('.spotlight');
  if (card) {
    const rect = card.getBoundingClientRect();
    card.style.setProperty('--px', `${event.clientX - rect.left}px`);
    card.style.setProperty('--py', `${event.clientY - rect.top}px`);
  }
  if (event.target.closest('.hero')) {
    const hero = document.getElementById('hero').getBoundingClientRect();
    const art = document.querySelector('.core-emblem');
    art.style.setProperty('--art-x', `${((event.clientX - hero.left) / hero.width - .5) * 12}px`);
    art.style.setProperty('--art-y', `${((event.clientY - hero.top) / hero.height - .5) * 12}px`);
  }
});
let liveBusy = false;
async function updateLive() {
  if (liveBusy) return;
  liveBusy = true;
  try {
    const response = await fetch('/api/monitor/summary', {cache:'no-store', signal:AbortSignal.timeout(8000)});
    if (!response.ok) throw new Error('Monitor unavailable');
    const summary = await response.json();
    const titles = {healthy:'All systems operational', degraded:'Service available · replica degraded', outage:'Application gateway unavailable', unknown:'Waiting for fresh health samples'};
    document.getElementById('landing-status').textContent = `${titles[summary.overall_status]} · checked ${new Date(summary.generated_at * 1000).toLocaleTimeString()}`;
    document.getElementById('landing-status-dot').style.background = summary.overall_status === 'healthy' ? '#a1e8c4' : '#dfb27b';
    summary.targets.filter((target) => target.target !== 'gateway').forEach((target) => { document.getElementById(`hero-${target.target}`).className = `art-dot ${target.status}`; });
  } catch {
    document.getElementById('landing-status').textContent = 'Monitoring unavailable · reconnecting automatically';
    document.getElementById('landing-status-dot').style.background = '#a08b85';
    document.querySelectorAll('.art-dot').forEach((dot) => { dot.className = 'art-dot'; });
  } finally { liveBusy = false; }
}
updateLive(); setInterval(updateLive, 10000);
