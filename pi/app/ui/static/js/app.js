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

  // Audio spectrum + beat
  if (data.audio_spectrum) {
    spectrumTarget = data.audio_spectrum;
  }
  const beatEl = document.getElementById('beat-indicator');
  if (beatEl) {
    beatEl.classList.toggle('active', !!data.audio_beat);
  }
}

// --- API helpers ---

async function api(method, path, body) {
  const opts = { method, headers: authHeaders() };
  if (body) opts.body = JSON.stringify(body);
  try {
    const res = await fetch(`${API}${path}`, opts);
    if (res.status === 401) {
      console.warn('Auth required — set token in System > Admin');
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

// --- Tab navigation ---

let activePreviewCanvas = 'sim-canvas';
let activeEffectName = null;

function activateTab(tab) {
  // Detect previous tab
  const prevTab = document.querySelector('.tab.active');
  const prevTabName = prevTab ? prevTab.dataset.tab : null;

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

  // Stop effects preview when leaving effects tab
  if (prevTabName === 'effects' && tab.dataset.tab !== 'effects') {
    stopEffectsPreview();
  }

  if (tab.dataset.tab === 'effects') {
    activePreviewCanvas = 'effects-sim-canvas';
    loadEffects();
  } else if (tab.dataset.tab === 'sim') {
    activePreviewCanvas = 'sim-canvas';
    loadSimEffects();
  } else {
    activePreviewCanvas = 'sim-canvas';
  }

  if (tab.dataset.tab === 'media') loadMedia();
  if (tab.dataset.tab === 'audio') { loadAudioDevices(); loadAudioConfig(); }
  if (tab.dataset.tab === 'system') loadSystemStatus();

  // Stop setup live preview when leaving System tab
  if (tab.dataset.tab !== 'system') stopSetupLivePreview();
}

async function stopEffectsPreview() {
  await api('POST', '/api/preview/stop');
  if (previewWs) { previewWs.close(); previewWs = null; }
}

async function startEffectsPreview(effectName, params) {
  const data = await api('POST', '/api/preview/start', { effect: effectName, params: params || {}, fps: 30 });
  if (data && data.active) {
    connectPreviewWs();
  }
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

let effectsCatalog = null;
let currentEffectParams = {};
let currentFilterCategory = 'All';

const CATEGORY_MAP = {
  imported_sound: 'Sound Reactive',
  sound: 'Sound Reactive',
  audio: 'Sound Reactive',
  classic: 'Classic',
  imported_classic: 'Classic',
  ambient: 'Ambient',
  imported_ambient: 'Ambient',
  generative: 'Built-in',
  special: 'Special',
};

const DEFAULT_PALETTES = ['Rainbow', 'Ocean', 'Sunset', 'Forest', 'Lava', 'Ice', 'Neon', 'Cyberpunk', 'Pastel', 'Vapor'];

function effectCategory(group) {
  return CATEGORY_MAP[group] || 'Other';
}

async function loadEffects() {
  const data = await api('GET', '/api/effects/catalog');
  if (!data) return;
  effectsCatalog = data.effects;

  // Restore saved params from state.json
  if (data.current_params && Object.keys(data.current_params).length > 0) {
    currentEffectParams = { ...data.current_params };
  }

  // Build categorized list
  const categorized = [];
  for (const [name, info] of Object.entries(data.effects)) {
    if (name.startsWith('diag_')) continue;
    const cat = effectCategory(info.group || 'other');
    categorized.push({ name, category: cat, ...info });
  }

  // Count per category
  const counts = {};
  for (const eff of categorized) {
    counts[eff.category] = (counts[eff.category] || 0) + 1;
  }

  // Render filter bar
  const filterBar = document.getElementById('effects-filter-bar');
  filterBar.innerHTML = '';

  const categoryOrder = ['All', 'Classic', 'Ambient', 'Sound Reactive', 'Built-in', 'Special', 'Other'];
  for (const cat of categoryOrder) {
    if (cat !== 'All' && !counts[cat]) continue;
    const btn = document.createElement('button');
    btn.className = 'filter-btn' + (cat === currentFilterCategory ? ' filter-active' : '');
    btn.textContent = cat === 'All' ? `All (${categorized.length})` : `${cat} (${counts[cat]})`;
    btn.addEventListener('click', () => {
      currentFilterCategory = cat;
      // Update active state on filter buttons
      filterBar.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('filter-active'));
      btn.classList.add('filter-active');
      // Filter grid
      applyEffectsFilter();
    });
    filterBar.appendChild(btn);
  }

  // Render effects grid
  const grid = document.getElementById('effects-grid');
  grid.innerHTML = '';

  for (const eff of categorized) {
    const btn = document.createElement('button');
    btn.textContent = eff.label || eff.name.replace(/_/g, ' ');
    btn.dataset.category = eff.category;
    if (eff.name === data.current) btn.classList.add('active-scene');
    btn.addEventListener('click', () => activateEffect(eff.name));
    grid.appendChild(btn);
  }

  applyEffectsFilter();

  // Show controls for active effect and start preview
  if (data.current && data.effects[data.current]) {
    activeEffectName = data.current;
    showEffectControls(data.current, data.effects[data.current]);
    startEffectsPreview(data.current, currentEffectParams);
  }
}

function applyEffectsFilter() {
  const grid = document.getElementById('effects-grid');
  grid.querySelectorAll('button').forEach(btn => {
    if (currentFilterCategory === 'All' || btn.dataset.category === currentFilterCategory) {
      btn.style.display = '';
    } else {
      btn.style.display = 'none';
    }
  });
}

function showEffectControls(name, meta) {
  const wrap = document.getElementById('active-effect-controls');
  document.getElementById('active-effect-name').textContent = meta.label || name;
  const paramsDiv = document.getElementById('effect-params');
  paramsDiv.innerHTML = '';

  // Build params list, ensuring speed is always present
  const params = meta.params ? [...meta.params] : [];
  const hasSpeed = params.some(p => p.name === 'speed');
  if (!hasSpeed && name !== 'animation_switcher') {
    params.unshift({
      name: 'speed',
      label: 'Speed',
      min: 0.1,
      max: 5.0,
      step: 0.1,
      default: 1.0,
    });
  }

  // Render param sliders
  for (const p of params) {
    const row = document.createElement('div');
    row.className = 'param-row';
    const val = currentEffectParams[p.name] ?? p.default;
    row.innerHTML = `
      <label>${p.label}</label>
      <input type="range" min="${p.min}" max="${p.max}" step="${p.step}" value="${val}"
             data-param="${p.name}" class="param-slider">
      <span class="param-value">${Number.isInteger(p.step) ? val : parseFloat(val).toFixed(1)}</span>
    `;
    const slider = row.querySelector('input');
    const display = row.querySelector('.param-value');
    let debounce = null;
    slider.addEventListener('input', () => {
      const v = parseFloat(slider.value);
      display.textContent = Number.isInteger(p.step) ? v : v.toFixed(1);
      currentEffectParams[p.name] = v;
      clearTimeout(debounce);
      debounce = setTimeout(() => {
        api('POST', '/api/scenes/activate', { effect: name, params: currentEffectParams });
        // Update preview with new params
        if (activeEffectName === name) {
          startEffectsPreview(name, currentEffectParams);
        }
      }, 100);
    });
    paramsDiv.appendChild(row);
  }

  // Palette selector — always shown
  const palWrap = document.getElementById('effect-palette-wrap');
  palWrap.classList.remove('hidden');
  const select = document.getElementById('effect-palette-select');
  select.innerHTML = '';
  const paletteList = (meta.palettes && meta.palettes.length > 0) ? meta.palettes : DEFAULT_PALETTES;
  paletteList.forEach((palName, idx) => {
    const opt = document.createElement('option');
    opt.value = idx;
    opt.textContent = palName;
    if (currentEffectParams.palette === idx) opt.selected = true;
    select.appendChild(opt);
  });
  select.onchange = () => {
    currentEffectParams.palette = parseInt(select.value);
    api('POST', '/api/scenes/activate', { effect: name, params: currentEffectParams });
    if (activeEffectName === name) {
      startEffectsPreview(name, currentEffectParams);
    }
  };

  // Switcher-specific UI
  const switcherWrap = document.getElementById('switcher-controls');
  if (switcherWrap) {
    if (name === 'animation_switcher') {
      const saved = currentEffectParams.playlist;
      switcherSelectedEffects = new Set(Array.isArray(saved) ? saved : []);
      switcherWrap.classList.remove('hidden');
      renderSwitcherControls();
      startSwitcherStatusPolling();
    } else {
      switcherWrap.classList.add('hidden');
      stopSwitcherStatusPolling();
    }
  }

  wrap.classList.remove('hidden');
}

function classifyEffectForSwitcher(name, meta) {
  if (name === 'animation_switcher') return null;
  if (name.startsWith('diag_')) return null;
  if (meta.group === 'diagnostic') return null;
  if (meta.group === 'sound' || meta.group === 'audio') return 'sr';
  return 'other';
}

function renderSwitcherControls() {
  const wrap = document.getElementById('switcher-controls');
  if (!wrap || !effectsCatalog) return;

  const srEntries = [];
  const otherEntries = [];
  for (const [name, meta] of Object.entries(effectsCatalog)) {
    const section = classifyEffectForSwitcher(name, meta);
    if (section === 'sr') srEntries.push([name, meta]);
    else if (section === 'other') otherEntries.push([name, meta]);
  }
  const byName = (a, b) => compareByLabel(a[0], b[0]);
  srEntries.sort(byName);
  otherEntries.sort(byName);

  const build = (container, entries) => {
    container.innerHTML = '';
    for (const [name, meta] of entries) {
      const row = document.createElement('label');
      row.className = 'switcher-check-row';
      row.dataset.name = name;
      const checked = switcherSelectedEffects.has(name);
      if (checked) row.classList.add('checked');
      row.innerHTML = `
        <input type="checkbox" ${checked ? 'checked' : ''} data-name="${name}">
        <span>${meta.label || name}</span>
      `;
      container.appendChild(row);
    }
  };

  build(document.getElementById('switcher-sr-list'), srEntries);
  build(document.getElementById('switcher-other-list'), otherEntries);

  wrap.querySelectorAll('.switcher-check-row input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      const name = cb.dataset.name;
      if (cb.checked) switcherSelectedEffects.add(name);
      else switcherSelectedEffects.delete(name);
      cb.closest('.switcher-check-row').classList.toggle('checked', cb.checked);
      scheduleSwitcherSave();
    });
  });

  wrap.querySelectorAll('.switcher-select-all').forEach(btn => {
    btn.addEventListener('click', () => {
      const section = btn.dataset.section;
      const entries = section === 'sr' ? srEntries : otherEntries;
      entries.forEach(([name]) => switcherSelectedEffects.add(name));
      renderSwitcherControls();
      scheduleSwitcherSave();
    });
  });
  wrap.querySelectorAll('.switcher-clear').forEach(btn => {
    btn.addEventListener('click', () => {
      const section = btn.dataset.section;
      const entries = section === 'sr' ? srEntries : otherEntries;
      entries.forEach(([name]) => switcherSelectedEffects.delete(name));
      renderSwitcherControls();
      scheduleSwitcherSave();
    });
  });
}

function scheduleSwitcherSave() {
  clearTimeout(switcherSaveDebounce);
  switcherSaveDebounce = setTimeout(() => {
    if (activeEffectName !== 'animation_switcher') return;
    const playlist = Array.from(switcherSelectedEffects).sort(compareByLabel);
    const params = { ...currentEffectParams, playlist };
    currentEffectParams = params;
    api('POST', '/api/scenes/activate', { effect: 'animation_switcher', params });
  }, 300);
}

async function pollSwitcherStatus() {
  if (activeEffectName !== 'animation_switcher') return;
  const status = await api('GET', '/api/scenes/switcher/status');
  if (!status || !status.active) return;
  const el = document.getElementById('switcher-status');
  if (!el) return;
  const current = status.current;
  const currentLabel = (effectsCatalog && effectsCatalog[current])
    ? effectsCatalog[current].label : current;
  const remaining = Math.round(status.time_remaining || 0);
  el.textContent = `Now playing: ${currentLabel || '(none)'} — switching in ${remaining}s`;
}

function startSwitcherStatusPolling() {
  stopSwitcherStatusPolling();
  pollSwitcherStatus();
  switcherStatusInterval = setInterval(pollSwitcherStatus, 2000);
}

function stopSwitcherStatusPolling() {
  if (switcherStatusInterval) {
    clearInterval(switcherStatusInterval);
    switcherStatusInterval = null;
  }
}

async function activateEffect(name) {
  activeEffectName = name;
  // Activate without sending params — backend uses effect defaults
  const result = await api('POST', '/api/scenes/activate', { effect: name });
  // Use resolved params from response (no second request, no race condition)
  if (activeEffectName !== name) return;  // stale click guard
  currentEffectParams = (result && result.params) ? { ...result.params } : {};
  // Re-render to update active highlight and controls without full refetch
  if (effectsCatalog && effectsCatalog[name]) {
    // Update highlights in effects grid
    const grid = document.getElementById('effects-grid');
    if (grid) {
      grid.querySelectorAll('button').forEach(b => b.classList.remove('active-scene'));
      grid.querySelectorAll('button').forEach(b => {
        const label = effectsCatalog[name]?.label || name.replace(/_/g, ' ');
        if (b.textContent === label) b.classList.add('active-scene');
      });
    }
    showEffectControls(name, effectsCatalog[name]);
    // Start live preview on effects tab
    const activeTab = document.querySelector('.tab.active');
    if (activeTab && activeTab.dataset.tab === 'effects') {
      startEffectsPreview(name, currentEffectParams);
    }
  }
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
        console.warn('Auth required — set token in System > Admin');
      } else if (res.status === 413) {
        alert('File too large');
      } else if (res.ok) {
        const ct = res.headers.get('content-type') || '';
        const data = ct.includes('application/json') ? await res.json() : { status: 'ok' };
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

// --- Switcher state ---

let switcherStatusInterval = null;
let switcherSelectedEffects = new Set();
let switcherSaveDebounce = null;

// Deterministic collator for switcher playlist ordering
const SWITCHER_COLLATOR = new Intl.Collator(undefined, { sensitivity: 'base' });

function compareByLabel(aName, bName) {
  const la = (effectsCatalog && effectsCatalog[aName] ? effectsCatalog[aName].label : aName) || aName;
  const lb = (effectsCatalog && effectsCatalog[bName] ? effectsCatalog[bName].label : bName) || bName;
  const cmp = SWITCHER_COLLATOR.compare(la, lb);
  if (cmp !== 0) return cmp;
  return aName.localeCompare(bName);
}

// --- Audio ---

let spectrumTarget = new Array(16).fill(0);
let spectrumCurrent = new Array(16).fill(0);
let spectrumAnimId = null;

const BAND_COLORS = [
  '#c0392b','#c0392b','#c0392b','#d35400',
  '#e67e22','#f1c40f','#2ecc71','#2ecc71','#27ae60','#2ecc71',
  '#3498db','#2980b9','#8e44ad','#9b59b6','#8e44ad','#9b59b6',
];

function renderSpectrum() {
  const canvas = document.getElementById('spectrum-canvas');
  if (!canvas) return;

  // Skip rendering when canvas is hidden (display:none → zero rect)
  const rect = canvas.getBoundingClientRect();
  if (rect.width < 10) {
    spectrumAnimId = requestAnimationFrame(renderSpectrum);
    return;
  }

  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);

  const w = rect.width;
  const h = rect.height;
  const barCount = 16;
  const gap = 3;
  const barWidth = (w - gap * (barCount + 1)) / barCount;

  ctx.clearRect(0, 0, w, h);

  // Band region labels
  ctx.font = '10px system-ui, sans-serif';
  ctx.fillStyle = '#c0392b88';
  ctx.fillText('BASS', gap, 12);
  ctx.fillStyle = '#2ecc7188';
  ctx.fillText('MID', gap + (barWidth + gap) * 4, 12);
  ctx.fillStyle = '#9b59b688';
  ctx.fillText('TREBLE', gap + (barWidth + gap) * 10, 12);

  for (let i = 0; i < barCount; i++) {
    spectrumCurrent[i] += (spectrumTarget[i] - spectrumCurrent[i]) * 0.3;
  }

  for (let i = 0; i < barCount; i++) {
    const x = gap + i * (barWidth + gap);
    const barH = Math.max(1, spectrumCurrent[i] * (h - 20));
    const y = h - barH;

    const grad = ctx.createLinearGradient(x, h, x, y);
    grad.addColorStop(0, BAND_COLORS[i] + '44');
    grad.addColorStop(1, BAND_COLORS[i]);
    ctx.fillStyle = grad;

    const r = Math.min(3, barWidth / 2);
    ctx.beginPath();
    ctx.moveTo(x, h);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.lineTo(x + barWidth - r, y);
    ctx.quadraticCurveTo(x + barWidth, y, x + barWidth, y + r);
    ctx.lineTo(x + barWidth, h);
    ctx.fill();
  }

  spectrumAnimId = requestAnimationFrame(renderSpectrum);
}

function startSpectrum() {
  if (!spectrumAnimId) renderSpectrum();
}

function stopSpectrum() {
  if (spectrumAnimId) {
    cancelAnimationFrame(spectrumAnimId);
    spectrumAnimId = null;
  }
}

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

async function loadAudioConfig() {
  const data = await api('GET', '/api/audio/config');
  if (!data) return;
  const setSlider = (id, val) => {
    const el = document.getElementById(id);
    if (el && val != null) {
      el.value = Math.round(val * 100);
      const valEl = document.getElementById(id + '-value');
      if (valEl) valEl.textContent = Math.round(val * 100) + '%';
    }
  };
  setSlider('audio-gain', data.gain);
  setSlider('sens-bass', data.bass_sensitivity);
  setSlider('sens-mid', data.mid_sensitivity);
  setSlider('sens-treble', data.treble_sensitivity);
}

function initAudio() {
  document.getElementById('audio-device-select').addEventListener('change', (e) => {
    const idx = e.target.value ? parseInt(e.target.value) : null;
    api('POST', '/api/audio/config', { device_index: idx });
  });

  document.getElementById('audio-start-btn').addEventListener('click', () => {
    api('POST', '/api/audio/start');
    startSpectrum();
  });

  document.getElementById('audio-stop-btn').addEventListener('click', () => {
    api('POST', '/api/audio/stop');
    stopSpectrum();
  });

  document.getElementById('audio-gain').addEventListener('input', (e) => {
    const val = e.target.value / 100;
    document.getElementById('audio-gain-value').textContent = Math.round(val * 100) + '%';
    api('POST', '/api/audio/config', { gain: val });
  });

  const bandSliders = [
    { id: 'sens-bass', param: 'bass_sensitivity' },
    { id: 'sens-mid', param: 'mid_sensitivity' },
    { id: 'sens-treble', param: 'treble_sensitivity' },
  ];
  for (const { id, param } of bandSliders) {
    let debounce = null;
    document.getElementById(id).addEventListener('input', (e) => {
      const val = e.target.value / 100;
      document.getElementById(id + '-value').textContent = Math.round(val * 100) + '%';
      clearTimeout(debounce);
      debounce = setTimeout(() => {
        api('POST', '/api/audio/config', { [param]: val });
      }, 100);
    });
  }

  startSpectrum();
}

// --- Diagnostics ---

function initDiagnostics() {
  let activeTest = null;

  document.querySelectorAll('.test-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const pattern = btn.dataset.test;
      if (activeTest === pattern) {
        // Deactivate: click the already-active button
        await api('POST', '/api/diagnostics/clear');
        btn.classList.remove('active');
        activeTest = null;
      } else {
        // Activate new pattern (clears previous if any)
        await api('POST', '/api/diagnostics/test-pattern', { pattern });
        document.querySelectorAll('.test-btn.active').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        activeTest = pattern;
      }
    });
  });

  document.getElementById('diag-clear-btn').addEventListener('click', async () => {
    await api('POST', '/api/diagnostics/clear');
    document.querySelectorAll('.test-btn.active').forEach(b => b.classList.remove('active'));
    activeTest = null;
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
      if (btn.dataset.section === 'system-setup') { loadPixelMap(); loadTeensyStatus(); loadStats(); startSetupLivePreview(); }
      else stopSetupLivePreview();
    });
  });

  initSetup();
}

// --- Pixel Map Setup ---

const COLOR_ORDERS = ['BGR','RGB','GRB','GBR','BRG','RBG'];

let _pixelMapData = null;

function segmentColor(index) {
  const hue = (index * 137.508) % 360;
  return `hsl(${hue}, 75%, 55%)`;
}

function showPmStatus(msg, isError = false) {
  const el = document.getElementById('pm-status');
  if (!el) return;
  el.textContent = msg;
  el.className = isError ? 'status-msg error' : 'status-msg';
  setTimeout(() => { el.textContent = ''; }, 4000);
}

async function loadPixelMap() {
  const data = await api('GET', '/api/pixel-map/');
  if (!data) return;
  _pixelMapData = data;

  const originSelect = document.getElementById('pm-origin-select');
  if (originSelect && data.origin) {
    originSelect.value = data.origin;
  }

  renderGridSVG(data);
  renderSegmentTable(data);
  renderSegmentCards(data);
  updateSummary(data);
}

function renderGridSVG(data) {
  const svg = document.getElementById('pm-grid-svg');
  if (!svg) return;

  const segments = data.segments || [];
  const gridW = data.grid ? data.grid.width : 0;
  const gridH = data.grid ? data.grid.height : 0;

  if (gridW === 0 || gridH === 0 || segments.length === 0) {
    svg.innerHTML = '<text x="20" y="30" fill="#666" font-size="14">No grid data</text>';
    svg.setAttribute('viewBox', '0 0 200 50');
    return;
  }

  const isBottomLeft = (data.origin || 'bottom-left') === 'bottom-left';

  // SVG layout: fixed compact size, scale content to fit
  const pad = { left: 30, right: 10, top: 10, bottom: 25 };
  const maxSvgW = 400;
  const maxSvgH = 180;
  const availW = maxSvgW - pad.left - pad.right;
  const availH = maxSvgH - pad.top - pad.bottom;
  const cellW = Math.max(4, Math.min(20, availW / gridW));
  const cellH = Math.max(0.5, Math.min(3, availH / gridH));
  const svgW = pad.left + gridW * cellW + pad.right;
  const svgH = pad.top + gridH * cellH + pad.bottom;

  svg.setAttribute('viewBox', `0 0 ${svgW} ${svgH}`);

  const parts = [];

  // Background grid dots
  for (let gx = 0; gx < gridW; gx++) {
    for (let gy = 0; gy < gridH; gy += Math.max(1, Math.floor(gridH / 40))) {
      const px = pad.left + gx * cellW + cellW / 2;
      const drawY = isBottomLeft ? (gridH - 1 - gy) : gy;
      const py = pad.top + drawY * cellH + cellH / 2;
      parts.push(`<circle cx="${px}" cy="${py}" r="0.8" fill="#333" />`);
    }
  }

  // X axis labels (every column)
  for (let gx = 0; gx < gridW; gx++) {
    const px = pad.left + gx * cellW + cellW / 2;
    parts.push(`<text x="${px}" y="${svgH - 5}" fill="#666" font-size="8" text-anchor="middle">${gx}</text>`);
  }

  // Y axis labels (sparse)
  const yLabelStep = Math.max(1, Math.floor(gridH / 8));
  for (let gy = 0; gy <= gridH; gy += yLabelStep) {
    const drawY = isBottomLeft ? (gridH - 1 - gy) : gy;
    const py = pad.top + drawY * cellH + cellH / 2;
    parts.push(`<text x="${pad.left - 4}" y="${py + 3}" fill="#666" font-size="7" text-anchor="end">${gy}</text>`);
  }

  // Group segments by output for daisy-chain lines
  const byOutput = {};
  segments.forEach((seg, idx) => {
    const out = seg.output;
    if (!byOutput[out]) byOutput[out] = [];
    byOutput[out].push({ seg, idx });
  });

  // Draw daisy-chain dashed lines between consecutive segments on same output
  for (const entries of Object.values(byOutput)) {
    if (entries.length < 2) continue;
    for (let i = 0; i < entries.length - 1; i++) {
      const prevSeg = entries[i].seg;
      const nextSeg = entries[i + 1].seg;
      const prevEnd = prevSeg.end;
      const nextStart = nextSeg.start;
      const x1 = pad.left + prevEnd[0] * cellW + cellW / 2;
      const y1d = isBottomLeft ? (gridH - 1 - prevEnd[1]) : prevEnd[1];
      const y1 = pad.top + y1d * cellH + cellH / 2;
      const x2 = pad.left + nextStart[0] * cellW + cellW / 2;
      const y2d = isBottomLeft ? (gridH - 1 - nextStart[1]) : nextStart[1];
      const y2 = pad.top + y2d * cellH + cellH / 2;
      parts.push(`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#555" stroke-width="1" stroke-dasharray="3,3" />`);
    }
  }

  // Draw segments
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    const color = segmentColor(i);
    const sx = pad.left + seg.start[0] * cellW + cellW / 2;
    const sy_raw = isBottomLeft ? (gridH - 1 - seg.start[1]) : seg.start[1];
    const sy = pad.top + sy_raw * cellH + cellH / 2;
    const ex = pad.left + seg.end[0] * cellW + cellW / 2;
    const ey_raw = isBottomLeft ? (gridH - 1 - seg.end[1]) : seg.end[1];
    const ey = pad.top + ey_raw * cellH + cellH / 2;

    // Thick line for segment path
    parts.push(`<line x1="${sx}" y1="${sy}" x2="${ex}" y2="${ey}" stroke="${color}" stroke-width="3" stroke-linecap="round" />`);

    // Circle at start (LED 0)
    parts.push(`<circle cx="${sx}" cy="${sy}" r="4" fill="${color}" stroke="#000" stroke-width="1" />`);

    // Arrow/triangle at end (direction)
    const dx = ex - sx;
    const dy = ey - sy;
    const len = Math.sqrt(dx * dx + dy * dy);
    if (len > 0) {
      const ux = dx / len;
      const uy = dy / len;
      const arrowSize = 5;
      const ax = ex;
      const ay = ey;
      const p1x = ax - ux * arrowSize + uy * arrowSize * 0.6;
      const p1y = ay - uy * arrowSize - ux * arrowSize * 0.6;
      const p2x = ax - ux * arrowSize - uy * arrowSize * 0.6;
      const p2y = ay - uy * arrowSize + ux * arrowSize * 0.6;
      parts.push(`<polygon points="${ax},${ay} ${p1x},${p1y} ${p2x},${p2y}" fill="${color}" />`);
    }
  }

  svg.innerHTML = parts.join('\n');

  // Grid info text
  const gridInfo = document.getElementById('pm-grid-info');
  if (gridInfo && data.grid) {
    gridInfo.textContent = `${data.grid.width} x ${data.grid.height} — ${data.grid.total_mapped_leds} mapped LEDs`;
  }
}

function renderSegmentTable(data) {
  const tbody = document.getElementById('pm-segment-tbody');
  if (!tbody) return;
  tbody.innerHTML = '';

  const segments = data.segments || [];
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    const ledCount = seg.led_count || (Math.abs(seg.end[0] - seg.start[0]) + Math.abs(seg.end[1] - seg.start[1]) + 1);
    const color = segmentColor(i);
    const colorOpts = COLOR_ORDERS.map(o =>
      `<option value="${o}" ${o === (seg.color_order || 'BGR') ? 'selected' : ''}>${o}</option>`
    ).join('');
    const outputOpts = Array.from({ length: 8 }, (_, n) =>
      `<option value="${n}" ${n === seg.output ? 'selected' : ''}>${n}</option>`
    ).join('');

    const row = document.createElement('tr');
    row.dataset.segIndex = i;
    row.innerHTML = `
      <td><span class="pm-seg-swatch" style="background:${color}"></span></td>
      <td><input type="number" data-field="sx" value="${seg.start[0]}" min="0" max="999"></td>
      <td><input type="number" data-field="sy" value="${seg.start[1]}" min="0" max="9999"></td>
      <td><input type="number" data-field="ex" value="${seg.end[0]}" min="0" max="999"></td>
      <td><input type="number" data-field="ey" value="${seg.end[1]}" min="0" max="9999"></td>
      <td class="pm-led-count">${ledCount}</td>
      <td><select data-field="output">${outputOpts}</select></td>
      <td><select data-field="color_order">${colorOpts}</select></td>
      <td><button class="pm-seg-delete" title="Delete segment">&times;</button></td>
    `;
    tbody.appendChild(row);

    // Auto-update LED count on coordinate changes
    row.querySelectorAll('input[type="number"]').forEach(input => {
      input.addEventListener('input', () => {
        const sx = parseInt(row.querySelector('[data-field="sx"]').value) || 0;
        const sy = parseInt(row.querySelector('[data-field="sy"]').value) || 0;
        const ex = parseInt(row.querySelector('[data-field="ex"]').value) || 0;
        const ey = parseInt(row.querySelector('[data-field="ey"]').value) || 0;
        const count = Math.abs(ex - sx) + Math.abs(ey - sy) + 1;
        row.querySelector('.pm-led-count').textContent = count;
        // Also update cards view
        syncTableToCards();
      });
    });

    // Delete segment
    row.querySelector('.pm-seg-delete').addEventListener('click', () => {
      row.remove();
      reindexSegmentTable();
      syncTableToCards();
    });
  }
}

function renderSegmentCards(data) {
  const container = document.getElementById('pm-segment-cards');
  if (!container) return;
  container.innerHTML = '';

  const segments = data.segments || [];
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    const ledCount = seg.led_count || (Math.abs(seg.end[0] - seg.start[0]) + Math.abs(seg.end[1] - seg.start[1]) + 1);
    const color = segmentColor(i);

    const card = document.createElement('div');
    card.className = 'pm-seg-card';
    card.dataset.segIndex = i;
    card.innerHTML = `
      <span class="pm-seg-swatch" style="background:${color}"></span>
      <span class="pm-seg-card-coords">(${seg.start[0]},${seg.start[1]}) &rarr; (${seg.end[0]},${seg.end[1]})</span>
      <span class="pm-seg-card-leds">${ledCount} LEDs</span>
      <span class="pm-seg-card-detail">Out: ${seg.output} &nbsp; Color: ${seg.color_order || 'BGR'}</span>
      <button class="pm-seg-delete" title="Delete segment">&times;</button>
    `;
    container.appendChild(card);

    card.querySelector('.pm-seg-delete').addEventListener('click', () => {
      // Remove from table too
      const tableRow = document.querySelector(`#pm-segment-tbody tr[data-seg-index="${i}"]`);
      if (tableRow) tableRow.remove();
      card.remove();
      reindexSegmentTable();
    });
  }
}

function syncTableToCards() {
  const segments = collectSegments();
  const container = document.getElementById('pm-segment-cards');
  if (!container) return;
  container.innerHTML = '';
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    const ledCount = Math.abs(seg.end[0] - seg.start[0]) + Math.abs(seg.end[1] - seg.start[1]) + 1;
    const color = segmentColor(i);
    const card = document.createElement('div');
    card.className = 'pm-seg-card';
    card.innerHTML = `
      <span class="pm-seg-swatch" style="background:${color}"></span>
      <span class="pm-seg-card-coords">(${seg.start[0]},${seg.start[1]}) &rarr; (${seg.end[0]},${seg.end[1]})</span>
      <span class="pm-seg-card-leds">${ledCount} LEDs</span>
      <span class="pm-seg-card-detail">Out: ${seg.output} &nbsp; Color: ${seg.color_order}</span>
    `;
    container.appendChild(card);
  }
}

function reindexSegmentTable() {
  const rows = document.querySelectorAll('#pm-segment-tbody tr');
  rows.forEach((row, idx) => {
    row.dataset.segIndex = idx;
    const swatch = row.querySelector('.pm-seg-swatch');
    if (swatch) swatch.style.background = segmentColor(idx);
  });
  // Update summary with current table state
  const segments = collectSegments();
  const outputs = new Set(segments.map(s => s.output));
  const totalLeds = segments.reduce((sum, s) =>
    sum + Math.abs(s.end[0] - s.start[0]) + Math.abs(s.end[1] - s.start[1]) + 1, 0);
  const maxX = segments.length > 0 ? Math.max(...segments.map(s => Math.max(s.start[0], s.end[0]))) + 1 : 0;
  const maxY = segments.length > 0 ? Math.max(...segments.map(s => Math.max(s.start[1], s.end[1]))) + 1 : 0;
  const sumEl = document.getElementById('pm-summary-text');
  if (sumEl) {
    sumEl.textContent = `${segments.length} segments · ${outputs.size} outputs · ${totalLeds} LEDs · Grid ${maxX}x${maxY}`;
  }
}

function updateSummary(data) {
  const segments = data.segments || [];
  const outputs = new Set(segments.map(s => s.output));
  const totalLeds = segments.reduce((sum, s) => sum + (s.led_count || 0), 0);
  const gridW = data.grid ? data.grid.width : 0;
  const gridH = data.grid ? data.grid.height : 0;
  const el = document.getElementById('pm-summary-text');
  if (el) {
    el.textContent = `${segments.length} segments · ${outputs.size} outputs · ${totalLeds} LEDs · Grid ${gridW}x${gridH}`;
  }
}

function addSegmentRow(defaults) {
  const tbody = document.getElementById('pm-segment-tbody');
  if (!tbody) return;
  const idx = tbody.querySelectorAll('tr').length;
  const seg = defaults || {};
  const sx = seg.sx ?? idx;
  const sy = seg.sy ?? 0;
  const ex = seg.ex ?? sx;
  const ey = seg.ey ?? 171;
  const output = seg.output ?? 0;
  const colorOrder = seg.color_order ?? 'BGR';
  const ledCount = Math.abs(ex - sx) + Math.abs(ey - sy) + 1;
  const color = segmentColor(idx);

  const colorOpts = COLOR_ORDERS.map(o =>
    `<option value="${o}" ${o === colorOrder ? 'selected' : ''}>${o}</option>`
  ).join('');
  const outputOpts = Array.from({ length: 8 }, (_, n) =>
    `<option value="${n}" ${n === output ? 'selected' : ''}>${n}</option>`
  ).join('');

  const row = document.createElement('tr');
  row.dataset.segIndex = idx;
  row.innerHTML = `
    <td><span class="pm-seg-swatch" style="background:${color}"></span></td>
    <td><input type="number" data-field="sx" value="${sx}" min="0" max="999"></td>
    <td><input type="number" data-field="sy" value="${sy}" min="0" max="9999"></td>
    <td><input type="number" data-field="ex" value="${ex}" min="0" max="999"></td>
    <td><input type="number" data-field="ey" value="${ey}" min="0" max="9999"></td>
    <td class="pm-led-count">${ledCount}</td>
    <td><select data-field="output">${outputOpts}</select></td>
    <td><select data-field="color_order">${colorOpts}</select></td>
    <td><button class="pm-seg-delete" title="Delete segment">&times;</button></td>
  `;
  tbody.appendChild(row);

  row.querySelectorAll('input[type="number"]').forEach(input => {
    input.addEventListener('input', () => {
      const rsx = parseInt(row.querySelector('[data-field="sx"]').value) || 0;
      const rsy = parseInt(row.querySelector('[data-field="sy"]').value) || 0;
      const rex = parseInt(row.querySelector('[data-field="ex"]').value) || 0;
      const rey = parseInt(row.querySelector('[data-field="ey"]').value) || 0;
      row.querySelector('.pm-led-count').textContent = Math.abs(rex - rsx) + Math.abs(rey - rsy) + 1;
      syncTableToCards();
    });
  });

  row.querySelector('.pm-seg-delete').addEventListener('click', () => {
    row.remove();
    reindexSegmentTable();
    syncTableToCards();
  });

  syncTableToCards();
  reindexSegmentTable();
}

function collectSegments() {
  const rows = document.querySelectorAll('#pm-segment-tbody tr');
  const segments = [];
  rows.forEach(row => {
    const sx = parseInt(row.querySelector('[data-field="sx"]').value) || 0;
    const sy = parseInt(row.querySelector('[data-field="sy"]').value) || 0;
    const ex = parseInt(row.querySelector('[data-field="ex"]').value) || 0;
    const ey = parseInt(row.querySelector('[data-field="ey"]').value) || 0;
    const output = parseInt(row.querySelector('[data-field="output"]').value) || 0;
    const colorOrder = row.querySelector('[data-field="color_order"]').value || 'BGR';
    segments.push({
      start: [sx, sy],
      end: [ex, ey],
      output,
      color_order: colorOrder,
    });
  });
  return segments;
}

async function applyPixelMap() {
  const origin = document.getElementById('pm-origin-select').value;
  const segments = collectSegments();
  if (segments.length === 0) {
    showPmStatus('No segments to apply', true);
    return;
  }
  const result = await api('POST', '/api/pixel-map/apply', { origin, segments });
  if (result && !result.error) {
    showPmStatus('Pixel map applied successfully');
    _pixelMapData = result;
    renderGridSVG(result);
    renderSegmentTable(result);
    renderSegmentCards(result);
    updateSummary(result);
  } else {
    showPmStatus(result?.detail || result?.error || 'Failed to apply pixel map', true);
  }
}

async function validatePixelMap() {
  const origin = document.getElementById('pm-origin-select').value;
  const segments = collectSegments();
  if (segments.length === 0) {
    showPmStatus('No segments to validate', true);
    return;
  }
  const result = await api('POST', '/api/pixel-map/validate', { origin, segments });
  if (!result) {
    showPmStatus('Validation request failed', true);
    return;
  }
  if (result.valid) {
    showPmStatus('Configuration is valid');
  } else {
    const errors = (result.errors || []).join('; ');
    showPmStatus(`Validation errors: ${errors}`, true);
  }
}

async function loadTeensyStatus() {
  const data = await api('GET', '/api/pixel-map/teensy-status');
  if (!data) return;
  const el = document.getElementById('pm-teensy-output');
  if (!el) return;

  const lines = [];
  lines.push(`Connected: ${data.connected ? 'Yes' : 'No'}`);
  if (data.caps) {
    lines.push(`Firmware: ${data.caps.firmware_version || '--'}`);
  }
  if (data.teensy_config) {
    lines.push(`Outputs: ${data.teensy_config.outputs}`);
    lines.push(`Max LEDs/Output: ${data.teensy_config.max_leds_per_output}`);
    lines.push(`Wire Order: ${data.teensy_config.wire_order}`);
  }
  if (data.output_config) {
    lines.push('');
    lines.push('Output Allocation:');
    const outputConfig = data.output_config;
    if (Array.isArray(outputConfig)) {
      outputConfig.forEach((count, pin) => {
        if (count > 0) lines.push(`  Pin ${pin}: ${count} LEDs`);
      });
    }
  }
  lines.push(`\nLast CONFIG ACK: ${data.last_config_ack === true ? 'ACK' : data.last_config_ack === false ? 'NAK' : '--'}`);
  el.textContent = lines.join('\n');
}

function initSetup() {
  // Add segment
  document.getElementById('pm-add-segment-btn').addEventListener('click', () => {
    const segments = collectSegments();
    const nextX = segments.length;
    addSegmentRow({ sx: nextX, sy: 0, ex: nextX, ey: 171, output: 0, color_order: 'BGR' });
  });

  // Validate button
  document.getElementById('pm-validate-btn').addEventListener('click', () => validatePixelMap());

  // Apply button
  document.getElementById('pm-apply-btn').addEventListener('click', () => applyPixelMap());
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
    activePreviewCanvas = 'sim-canvas';
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

  // View toggle: Fit vs 1:1
  document.querySelectorAll('.sim-view-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.sim-view-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const wrap = document.getElementById('sim-canvas-wrap');
      if (btn.dataset.mode === 'fit') {
        wrap.classList.add('fit-mode');
      } else {
        wrap.classList.remove('fit-mode');
      }
    });
  });
}

async function loadSimEffects() {
  const data = await api('GET', '/api/effects/catalog');
  if (!data || !data.effects) return;
  const select = document.getElementById('sim-effect-select');
  select.innerHTML = '';
  for (const [name, info] of Object.entries(data.effects)) {
    if (name.startsWith('diag_')) continue;
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = info.label || name.replace(/_/g, ' ');
    select.appendChild(opt);
  }
  // Load strip labels for the sim
  loadSimStripLabels();
}

async function loadSimStripLabels() {
  const data = await api('GET', '/api/pixel-map/');
  if (!data || !data.segments) return;
  const container = document.getElementById('sim-strip-labels');
  container.innerHTML = '';
  // Group segments by X column for strip labels
  const xCols = new Set(data.segments.map(s => s.start[0]));
  const sorted = Array.from(xCols).sort((a, b) => a - b);
  for (const x of sorted) {
    const seg = data.segments.find(s => s.start[0] === x);
    const div = document.createElement('div');
    div.className = 'sim-strip-label';
    const arrow = seg.start[1] < seg.end[1] ? '\u2191' : '\u2193';
    div.innerHTML = `S${x}<br>${arrow}`;
    container.appendChild(div);
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

  const canvas = document.getElementById(activePreviewCanvas);
  if (!canvas) return;
  const pixelSize = 6;
  const gap = 2;
  const pitch = pixelSize + gap;
  const margin = 4;
  const canvasW = width * pitch + margin * 2 - gap;
  const canvasH = height * pitch + margin * 2 - gap;

  // Only resize if dimensions changed
  if (canvas.width !== canvasW || canvas.height !== canvasH) {
    canvas.width = canvasW;
    canvas.height = canvasH;
  }

  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#08080c';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const radius = pixelSize / 2;
  for (let x = 0; x < width; x++) {
    for (let y = 0; y < height; y++) {
      const srcIdx = (x * height + y) * 3;
      const r = pixels[srcIdx];
      const g = pixels[srcIdx + 1];
      const b = pixels[srcIdx + 2];

      // Draw dark gray for off pixels, actual color for lit
      const cx = x * pitch + radius + margin;
      const cy = y * pitch + radius + margin;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      if (r === 0 && g === 0 && b === 0) {
        ctx.fillStyle = '#08080c';
      } else {
        ctx.fillStyle = `rgb(${r},${g},${b})`;
      }
      ctx.fill();
    }
  }
}

// --- Help panels ---

function initHelp() {
  document.querySelectorAll('.help-toggle').forEach(btn => {
    const panel = btn.closest('.help-panel');
    const content = panel.querySelector('.help-content');
    const tabId = panel.dataset.tab || 'unknown';

    // Restore state from localStorage
    const saved = localStorage.getItem(`help-${tabId}`);
    if (saved === 'open') {
      content.hidden = false;
      btn.setAttribute('aria-expanded', 'true');
      panel.classList.remove('collapsed');
    }

    btn.addEventListener('click', () => {
      const expanded = btn.getAttribute('aria-expanded') === 'true';
      btn.setAttribute('aria-expanded', String(!expanded));
      content.hidden = expanded;
      panel.classList.toggle('collapsed', expanded);
      localStorage.setItem(`help-${tabId}`, expanded ? 'closed' : 'open');
    });
  });
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

// --- Setup live preview: mirrors live LED output on Setup screen ---

let setupLiveWs = null;
const SETUP_LIVE_HEADER_SIZE = 10;  // 1 + 4 + 2 + 2 + 1

function startSetupLivePreview() {
  if (setupLiveWs) return;
  // Small delay to ensure canvas is visible and has dimensions after tab switch
  setTimeout(() => _connectSetupLiveWs(), 100);
}

function _connectSetupLiveWs() {
  if (setupLiveWs) return;
  const canvas = document.getElementById('setup-live-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${proto}//${window.location.host}/api/preview/live`;
  const ws = new WebSocket(url);
  ws.binaryType = 'arraybuffer';
  ws.onmessage = (evt) => {
    try {
      const buf = new DataView(evt.data);
      const msgType = buf.getUint8(0);
      if (msgType !== 0x01) return;
      const width = buf.getUint16(5, true);
      const height = buf.getUint16(7, true);
      const pixels = new Uint8Array(evt.data, SETUP_LIVE_HEADER_SIZE);
      renderSetupLiveFrame(ctx, canvas, width, height, pixels);
    } catch (e) {}
  };
  ws.onclose = () => { setupLiveWs = null; };
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
  setupLiveWs = ws;
}

function stopSetupLivePreview() {
  if (setupLiveWs) {
    try { setupLiveWs.close(); } catch (e) {}
    setupLiveWs = null;
  }
}

function renderSetupLiveFrame(ctx, canvas, width, height, pixels) {
  // Frame is (width, height, 3) — columns are strips, y=0 is bottom.
  // Canvas is horizontal: strips side-by-side as vertical bars.
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  if (rect.width < 10) return;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const cw = rect.width;
  const ch = rect.height;
  const gap = 2;
  const colW = (cw - gap * (width + 1)) / width;

  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, cw, ch);

  for (let x = 0; x < width; x++) {
    const px = gap + x * (colW + gap);
    for (let y = 0; y < height; y++) {
      const srcIdx = (x * height + y) * 3;
      const r = pixels[srcIdx];
      const g = pixels[srcIdx + 1];
      const b = pixels[srcIdx + 2];
      // Flip y: y=0 (bottom) draws at canvas bottom
      const py = ch * (1 - (y + 1) / height);
      const rowH = ch / height;
      ctx.fillStyle = `rgb(${r},${g},${b})`;
      ctx.fillRect(px, py, colW, Math.ceil(rowH) + 1);
    }
  }

  // Strip labels at bottom
  ctx.font = '9px system-ui, sans-serif';
  ctx.fillStyle = '#888';
  ctx.textAlign = 'center';
  for (let x = 0; x < width; x++) {
    const px = gap + x * (colW + gap) + colW / 2;
    ctx.fillText(`S${x}`, px, ch - 2);
  }
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
  initHelp();
  connectWS();
  loadPresets();
});
