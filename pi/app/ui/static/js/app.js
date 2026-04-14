// Pillar Controller — Frontend Application

const API = '';
let ws = null;
let state = {};

// --- Auth ---

function getToken() {
  return localStorage.getItem('pillar_token') || '';
}

function setToken(token) {
  localStorage.setItem('pillar_token', token);
}

function authHeaders() {
  const token = getToken();
  const headers = { 'Content-Type': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

// --- WebSocket ---

let wsRetryTimer = null;

function connectWS() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
  if (wsRetryTimer) { clearTimeout(wsRetryTimer); wsRetryTimer = null; }

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onopen = () => {
    document.getElementById('connection-dot').className = 'dot connected';
  };

  ws.onclose = () => {
    document.getElementById('connection-dot').className = 'dot disconnected';
    ws = null;
    wsRetryTimer = setTimeout(connectWS, 2000);
  };

  ws.onerror = () => { ws.close(); };

  ws.onmessage = (evt) => {
    try { updateState(JSON.parse(evt.data)); } catch (e) {}
  };
}

function updateState(data) {
  state = { ...state, ...data };

  if (data.actual_fps !== undefined) {
    document.getElementById('fps-display').textContent = `${data.actual_fps} FPS`;
  }

  if (data.current_scene) {
    document.getElementById('current-scene-name').textContent = data.current_scene.replace(/_/g, ' ');
  }

  if (data.blackout !== undefined) {
    document.getElementById('blackout-on-btn').classList.toggle('active', data.blackout);
  }

  // Brightness from WebSocket
  if (data.brightness) {
    const b = data.brightness;
    if (b.manual_cap !== undefined) {
      const slider = document.getElementById('brightness-slider');
      slider.value = Math.round(b.manual_cap * 100);
      document.getElementById('brightness-value').textContent = `${slider.value}%`;
    }
    if (b.auto_enabled !== undefined) {
      document.getElementById('brightness-auto-toggle').checked = b.auto_enabled;
    }
    if (b.solar_phase) {
      document.getElementById('brightness-phase').textContent = b.solar_phase;
    }
    if (b.effective_brightness !== undefined) {
      document.getElementById('brightness-effective').textContent =
        `Effective: ${Math.round(b.effective_brightness * 100)}%`;
    }
  }
}

// --- API helpers ---

async function api(method, path, body) {
  const opts = { method, headers: authHeaders() };
  if (body) opts.body = JSON.stringify(body);
  try {
    const res = await fetch(`${API}${path}`, opts);
    if (res.status === 401) {
      showAuthBanner();
      return null;
    }
    if (res.status === 204 || res.headers.get('content-length') === '0') {
      return { status: 'ok' };
    }
    const ct = res.headers.get('content-type') || '';
    if (ct.includes('application/json')) {
      return await res.json();
    }
    // Non-JSON success — return status wrapper
    return res.ok ? { status: 'ok' } : null;
  } catch (e) {
    console.error(`API error: ${method} ${path}`, e);
    return null;
  }
}

function showAuthBanner() {
  document.getElementById('auth-banner').classList.remove('hidden');
}

// --- Tab navigation ---

function activateTab(tab) {
  const tabs = Array.from(document.querySelectorAll('.tab'));
  tabs.forEach(t => {
    t.classList.remove('active');
    t.setAttribute('aria-selected', 'false');
    t.setAttribute('tabindex', '-1');
  });
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  tab.classList.add('active');
  tab.setAttribute('aria-selected', 'true');
  tab.setAttribute('tabindex', '0');
  tab.focus();
  document.getElementById(`panel-${tab.dataset.tab}`).classList.add('active');

  if (tab.dataset.tab === 'effects') loadEffects();
  if (tab.dataset.tab === 'media') loadMedia();
  if (tab.dataset.tab === 'audio') loadAudioDevices();
  if (tab.dataset.tab === 'diag') loadStats();
  if (tab.dataset.tab === 'sim') loadSimEffects();
  if (tab.dataset.tab === 'system') loadSystemStatus();
}

function initTabs() {
  const tabs = Array.from(document.querySelectorAll('.tab'));
  tabs.forEach(tab => {
    tab.addEventListener('click', () => activateTab(tab));
  });

  // Keyboard navigation: Arrow Left/Right, Home, End
  document.getElementById('tabs').addEventListener('keydown', (e) => {
    const current = document.querySelector('.tab[aria-selected="true"]');
    if (!current) return;
    const idx = tabs.indexOf(current);
    let next = -1;

    if (e.key === 'ArrowRight') next = (idx + 1) % tabs.length;
    else if (e.key === 'ArrowLeft') next = (idx - 1 + tabs.length) % tabs.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = tabs.length - 1;

    if (next >= 0) {
      e.preventDefault();
      activateTab(tabs[next]);
    }
  });
}

// --- Effects ---

async function loadEffects() {
  const data = await api('GET', '/api/scenes/list');
  if (!data) return;

  const genList = document.getElementById('effect-list');
  const audioList = document.getElementById('audio-effect-list');
  genList.innerHTML = '';
  audioList.innerHTML = '';

  for (const [name, info] of Object.entries(data.effects)) {
    if (name.startsWith('diag_')) continue;
    const btn = document.createElement('button');
    btn.textContent = name.replace(/_/g, ' ');
    if (name === data.current) btn.classList.add('active-scene');
    btn.addEventListener('click', () => activateEffect(name));
    if (info.type === 'audio') {
      audioList.appendChild(btn);
    } else {
      genList.appendChild(btn);
    }
  }
}

async function activateEffect(name) {
  await api('POST', '/api/scenes/activate', { effect: name, params: {} });
  loadEffects();
}

// --- Presets ---

async function loadPresets() {
  const data = await api('GET', '/api/scenes/presets');
  if (!data) return;

  const grid = document.getElementById('preset-grid');
  grid.innerHTML = '';

  for (const [name] of Object.entries(data)) {
    const btn = document.createElement('button');
    btn.textContent = name;
    btn.addEventListener('click', () => api('POST', `/api/scenes/presets/load/${encodeURIComponent(name)}`));
    grid.appendChild(btn);
  }
}

// --- Media ---

async function loadMedia() {
  const data = await api('GET', '/api/media/list');
  if (!data) return;

  const lib = document.getElementById('media-library');
  lib.innerHTML = '';

  for (const item of data.items) {
    const btn = document.createElement('button');
    btn.textContent = `${item.name}\n${item.type} | ${item.frame_count}f`;
    btn.addEventListener('click', () => api('POST', `/api/media/play/${item.id}?loop=true&speed=1.0`));
    lib.appendChild(btn);
  }
}

function initUpload() {
  const btn = document.getElementById('upload-btn');
  const input = document.getElementById('media-upload');

  btn.addEventListener('click', async () => {
    const file = input.files[0];
    if (!file) return;

    const progress = document.getElementById('upload-progress');
    progress.classList.remove('hidden');

    const formData = new FormData();
    formData.append('file', file);

    try {
      const token = getToken();
      const headers = {};
      if (token) headers['Authorization'] = `Bearer ${token}`;

      const res = await fetch('/api/media/upload', {
        method: 'POST',
        body: formData,
        headers,
      });

      if (res.status === 401) {
        showAuthBanner();
      } else if (res.status === 413) {
        alert('File too large');
      } else {
        const data = await res.json();
        if (data.status === 'ok') {
          input.value = '';
          loadMedia();
        }
      }
    } catch (e) {
      console.error('Upload failed', e);
    }

    progress.classList.add('hidden');
  });
}

// --- Audio ---

async function loadAudioDevices() {
  const data = await api('GET', '/api/audio/devices');
  if (!data) return;

  const select = document.getElementById('audio-device-select');
  select.innerHTML = '<option value="">Default</option>';
  for (const dev of data.devices) {
    const opt = document.createElement('option');
    opt.value = dev.index;
    opt.textContent = dev.name;
    select.appendChild(opt);
  }
}

function initAudio() {
  document.getElementById('audio-device-select').addEventListener('change', (e) => {
    const idx = e.target.value ? parseInt(e.target.value) : null;
    api('POST', '/api/audio/config', { device_index: idx, sensitivity: 1.0, gain: 1.0 });
  });

  document.getElementById('audio-start-btn').addEventListener('click', () => {
    api('POST', '/api/audio/start');
  });

  document.getElementById('audio-stop-btn').addEventListener('click', () => {
    api('POST', '/api/audio/stop');
  });

  document.getElementById('audio-sensitivity').addEventListener('change', (e) => {
    api('POST', '/api/audio/config', { sensitivity: e.target.value / 100 });
  });

  document.getElementById('audio-gain').addEventListener('change', (e) => {
    api('POST', '/api/audio/config', { gain: e.target.value / 100 });
  });
}

// --- Diagnostics ---

function initDiagnostics() {
  document.querySelectorAll('.test-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      api('POST', '/api/diagnostics/test-pattern', { pattern: btn.dataset.test });
    });
  });

  document.getElementById('diag-clear-btn').addEventListener('click', () => {
    api('POST', '/api/diagnostics/clear');
  });
}

async function loadStats() {
  const data = await api('GET', '/api/diagnostics/stats');
  if (!data) return;
  document.getElementById('stats-output').textContent = JSON.stringify(data, null, 2);
}

// --- System ---

async function loadSystemStatus() {
  const data = await api('GET', '/api/system/status');
  if (!data) return;

  document.getElementById('sys-transport').textContent =
    data.transport?.connected ? `Connected (${data.transport.port})` : 'Disconnected';
  document.getElementById('sys-firmware').textContent =
    data.transport?.caps?.firmware_version || '--';
  document.getElementById('sys-frames').textContent =
    data.render?.frames_sent?.toLocaleString() || '0';
}

function initSystem() {
  document.getElementById('fps-select').addEventListener('change', (e) => {
    api('POST', '/api/display/fps', { value: parseInt(e.target.value) });
  });

  document.getElementById('restart-app-btn').addEventListener('click', () => {
    if (confirm('Restart the pillar application?')) {
      api('POST', '/api/system/restart-app');
    }
  });

  document.getElementById('reboot-btn').addEventListener('click', () => {
    if (confirm('Reboot the Raspberry Pi?')) {
      api('POST', '/api/system/reboot');
    }
  });

  // System sub-navigation
  document.querySelectorAll('.subnav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.subnav-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.system-section').forEach(s => {
        s.classList.add('hidden');
        s.classList.remove('active');
      });
      btn.classList.add('active');
      const section = document.getElementById(btn.dataset.section);
      if (section) {
        section.classList.remove('hidden');
        section.classList.add('active');
      }
      if (btn.dataset.section === 'system-setup') loadSetupStatus();
    });
  });

  initSetup();
}

// --- Setup ---

let setupSessionId = null;

async function loadSetupStatus() {
  const data = await api('GET', '/api/setup/session/status');
  if (!data) return;

  if (data.active) {
    setupSessionId = data.session_id;
    showSetupActive();
    await loadStripInventory();
  } else {
    setupSessionId = null;
    showSetupInactive();
  }
}

function showSetupActive() {
  document.getElementById('setup-status-msg').textContent = `Session active: ${setupSessionId}`;
  document.getElementById('setup-start-btn').classList.add('hidden');
  document.getElementById('setup-cancel-btn').classList.remove('hidden');
  document.getElementById('setup-commit-btn').classList.remove('hidden');
  document.getElementById('setup-strip-inventory').classList.remove('hidden');
}

function showSetupInactive() {
  document.getElementById('setup-status-msg').textContent = 'No active session';
  document.getElementById('setup-start-btn').classList.remove('hidden');
  document.getElementById('setup-cancel-btn').classList.add('hidden');
  document.getElementById('setup-commit-btn').classList.add('hidden');
  document.getElementById('setup-strip-inventory').classList.add('hidden');
}

async function loadStripInventory() {
  const data = await api('GET', '/api/setup/installation');
  if (!data || !data.strips) return;

  const tbody = document.getElementById('strip-table-body');
  tbody.innerHTML = '';

  for (const strip of data.strips) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${strip.id}</td>
      <td>${strip.label}</td>
      <td>${strip.enabled ? 'Yes' : 'No'}</td>
      <td>${strip.installed_led_count}</td>
      <td>${strip.color_order}</td>
      <td>${strip.direction === 'bottom_to_top' ? '↑' : '↓'}</td>
      <td>${strip.output_channel}</td>
      <td>${strip.output_slot}</td>
    `;
    tbody.appendChild(tr);
  }
}

function initSetup() {
  document.getElementById('setup-start-btn').addEventListener('click', async () => {
    const data = await api('POST', '/api/setup/session/start');
    if (data && data.session_id) {
      setupSessionId = data.session_id;
      showSetupActive();
      await loadStripInventory();
    }
  });

  document.getElementById('setup-cancel-btn').addEventListener('click', async () => {
    await api('POST', '/api/setup/session/cancel');
    setupSessionId = null;
    showSetupInactive();
  });

  document.getElementById('setup-commit-btn').addEventListener('click', async () => {
    if (!setupSessionId) return;
    const data = await api('POST', '/api/setup/session/commit', { session_id: setupSessionId });
    if (data && data.status === 'committed') {
      setupSessionId = null;
      showSetupInactive();
    }
  });
}

// --- Brightness ---

function initBrightness() {
  const slider = document.getElementById('brightness-slider');
  const display = document.getElementById('brightness-value');

  let debounce = null;
  slider.addEventListener('input', () => {
    display.textContent = `${slider.value}%`;
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      api('POST', '/api/brightness/config', { manual_cap: slider.value / 100 });
    }, 100);
  });

  document.getElementById('brightness-auto-toggle').addEventListener('change', (e) => {
    api('POST', '/api/brightness/config', { auto_enabled: e.target.checked });
  });
}

// --- Blackout (explicit on/off) ---

function initBlackout() {
  document.getElementById('blackout-on-btn').addEventListener('click', () => {
    api('POST', '/api/display/blackout', { enabled: true });
  });

  document.getElementById('blackout-off-btn').addEventListener('click', () => {
    api('POST', '/api/display/blackout', { enabled: false });
  });
}

// --- Auth ---

function initAuth() {
  // Restore token if saved
  const saved = getToken();
  if (!saved) {
    showAuthBanner();
  }

  document.getElementById('auth-save-btn').addEventListener('click', () => {
    const token = document.getElementById('auth-token-input').value.trim();
    if (token) {
      setToken(token);
      document.getElementById('auth-banner').classList.add('hidden');
    }
  });

  document.getElementById('system-token-save').addEventListener('click', () => {
    const token = document.getElementById('system-token-input').value.trim();
    if (token) {
      setToken(token);
      alert('Token updated');
    }
  });
}

// --- Simulator ---

let previewWs = null;

function initSim() {
  const effectSelect = document.getElementById('sim-effect-select');

  document.getElementById('sim-start-btn').addEventListener('click', async () => {
    const effect = effectSelect.value;
    if (!effect) return;
    const data = await api('POST', '/api/preview/start', { effect, params: {}, fps: 30 });
    if (data && data.active) {
      document.getElementById('sim-status').textContent = `Previewing: ${effect}`;
      connectPreviewWs();
    }
  });

  document.getElementById('sim-stop-btn').addEventListener('click', async () => {
    await api('POST', '/api/preview/stop');
    document.getElementById('sim-status').textContent = 'Idle';
    if (previewWs) { previewWs.close(); previewWs = null; }
  });
}

async function loadSimEffects() {
  const data = await api('GET', '/api/scenes/list');
  if (!data) return;
  const select = document.getElementById('sim-effect-select');
  select.innerHTML = '';
  for (const [name, info] of Object.entries(data.effects)) {
    if (name.startsWith('diag_')) continue;
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name.replace(/_/g, ' ');
    select.appendChild(opt);
  }
}

let previewWsRetry = null;

function connectPreviewWs() {
  if (previewWs && (previewWs.readyState === WebSocket.CONNECTING || previewWs.readyState === WebSocket.OPEN)) return;
  if (previewWsRetry) { clearTimeout(previewWsRetry); previewWsRetry = null; }

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  previewWs = new WebSocket(`${proto}//${location.host}/api/preview/ws`);
  previewWs.binaryType = 'arraybuffer';

  previewWs.onopen = () => {
    document.getElementById('sim-status').textContent = 'Connected';
  };

  previewWs.onmessage = (evt) => {
    if (evt.data instanceof ArrayBuffer && evt.data.byteLength > 10) {
      renderPreviewFrame(evt.data);
    }
  };

  previewWs.onerror = () => { previewWs.close(); };

  previewWs.onclose = () => {
    previewWs = null;
    document.getElementById('sim-status').textContent = 'Disconnected';
    // Auto-retry if preview was active
    previewWsRetry = setTimeout(async () => {
      const status = await api('GET', '/api/preview/status');
      if (status && status.active) connectPreviewWs();
    }, 3000);
  };
}

function renderPreviewFrame(buffer) {
  const view = new DataView(buffer);
  const type = view.getUint8(0);
  if (type !== 0x01) return;

  const width = view.getUint16(5, true);
  const height = view.getUint16(7, true);
  const headerSize = 10;
  const pixels = new Uint8Array(buffer, headerSize);

  const canvas = document.getElementById('sim-canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  const imgData = ctx.createImageData(width, height);

  // Transpose: our frame is (width, height, 3) but canvas expects (height, width, 4)
  for (let x = 0; x < width; x++) {
    for (let y = 0; y < height; y++) {
      const srcIdx = (x * height + y) * 3;
      const dstIdx = (y * width + x) * 4;
      imgData.data[dstIdx] = pixels[srcIdx];
      imgData.data[dstIdx + 1] = pixels[srcIdx + 1];
      imgData.data[dstIdx + 2] = pixels[srcIdx + 2];
      imgData.data[dstIdx + 3] = 255;
    }
  }
  ctx.putImageData(imgData, 0, 0);
}

// --- Tooltips (mobile long-press) ---

function initTooltips() {
  let tooltipTimer = null;
  let activeTooltip = null;

  function showTooltip(el) {
    hideTooltip();
    const text = el.dataset.tooltip;
    if (!text) return;
    const popup = document.createElement('div');
    popup.className = 'tooltip-popup';
    popup.textContent = text;
    el.appendChild(popup);
    activeTooltip = popup;
  }

  function hideTooltip() {
    if (activeTooltip) {
      activeTooltip.remove();
      activeTooltip = null;
    }
    clearTimeout(tooltipTimer);
  }

  document.addEventListener('touchstart', (e) => {
    const el = e.target.closest('[data-tooltip]');
    if (!el) return;
    tooltipTimer = setTimeout(() => {
      e.preventDefault();
      showTooltip(el);
    }, 600);
  }, { passive: false });

  document.addEventListener('touchend', hideTooltip);
  document.addEventListener('touchcancel', hideTooltip);
}

// --- Init ---

document.addEventListener('DOMContentLoaded', () => {
  initAuth();
  initTabs();
  initBrightness();
  initBlackout();
  initUpload();
  initAudio();
  initDiagnostics();
  initSystem();
  initSim();
  initTooltips();
  connectWS();
  loadPresets();
});
