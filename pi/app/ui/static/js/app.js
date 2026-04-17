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

// Strip-to-channel mapping colors (one per strip, cycling)
const STRIP_COLORS = [
  '#e74c3c','#3498db','#2ecc71','#f39c12','#9b59b6',
  '#1abc9c','#e67e22','#e84393','#00b894','#fd79a8',
  '#6c5ce7','#00cec9','#fdcb6e','#d63031','#74b9ff','#a29bfe',
];

const COLOR_ORDERS = ['RGB','RBG','GRB','GBR','BRG','BGR'];

let _pixelMapData = null;

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

  // Set origin selector
  const originSelect = document.getElementById('pm-origin-select');
  if (originSelect && data.origin) {
    originSelect.value = data.origin;
  }

  // Grid info
  const gridInfo = document.getElementById('pm-grid-info');
  if (gridInfo && data.grid) {
    gridInfo.textContent = `${data.grid.width} x ${data.grid.height} — ${data.grid.total_mapped_leds} mapped LEDs`;
  }

  renderGridPreview(data);
  renderStripList(data);
}

function renderGridPreview(pixelMap) {
  const canvas = document.getElementById('grid-preview');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  const gridW = pixelMap.grid ? pixelMap.grid.width : 0;
  const gridH = pixelMap.grid ? pixelMap.grid.height : 0;

  if (gridW === 0 || gridH === 0) {
    canvas.width = 400;
    canvas.height = 60;
    ctx.fillStyle = '#666';
    ctx.font = '14px system-ui, sans-serif';
    ctx.fillText('No grid data', 20, 35);
    return;
  }

  // Build a grid of strip IDs: grid[x][y] = strip_id or -1
  const grid = Array.from({ length: gridW }, () => new Int8Array(gridH).fill(-1));

  for (const strip of (pixelMap.strips || [])) {
    for (const scanline of (strip.scanlines || [])) {
      const [sx, sy] = scanline.start;
      const [ex, ey] = scanline.end;
      const dx = ex - sx;
      const dy = ey - sy;
      const steps = Math.abs(dx) + Math.abs(dy);
      const stepX = steps === 0 ? 0 : (dx > 0 ? 1 : dx < 0 ? -1 : 0);
      const stepY = steps === 0 ? 0 : (dy > 0 ? 1 : dy < 0 ? -1 : 0);
      let cx = sx, cy = sy;
      for (let i = 0; i <= steps; i++) {
        if (cx >= 0 && cx < gridW && cy >= 0 && cy < gridH) {
          grid[cx][cy] = strip.id;
        }
        cx += stepX;
        cy += stepY;
      }
    }
  }

  // Draw cells scaled to fit container
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const containerW = rect.width || 400;

  // Cell size: scale to fit container width, minimum 3px
  const cellSize = Math.max(3, Math.floor(containerW / gridW));
  const canvasW = gridW * cellSize;
  const canvasH = gridH * cellSize;

  canvas.width = canvasW * dpr;
  canvas.height = canvasH * dpr;
  canvas.style.width = canvasW + 'px';
  canvas.style.height = canvasH + 'px';
  ctx.scale(dpr, dpr);

  // Origin: bottom-left means y=0 is bottom of canvas
  const isBottomLeft = (pixelMap.origin || 'bottom-left') === 'bottom-left';

  for (let gx = 0; gx < gridW; gx++) {
    for (let gy = 0; gy < gridH; gy++) {
      const stripId = grid[gx][gy];
      const px = gx * cellSize;
      // If bottom-left origin, flip Y so y=0 draws at canvas bottom
      const py = isBottomLeft ? (gridH - 1 - gy) * cellSize : gy * cellSize;

      if (stripId >= 0) {
        ctx.fillStyle = STRIP_COLORS[stripId % STRIP_COLORS.length];
      } else {
        ctx.fillStyle = '#333';
      }
      ctx.fillRect(px, py, cellSize - 1, cellSize - 1);
    }
  }
}

function renderStripList(pixelMap) {
  const container = document.getElementById('pm-strip-list');
  if (!container) return;
  container.innerHTML = '';

  const strips = pixelMap.strips || [];
  if (strips.length === 0) {
    container.innerHTML = '<p class="text-dim">No strips configured</p>';
    return;
  }

  for (const strip of strips) {
    const card = document.createElement('div');
    card.className = 'pm-strip-card';
    card.dataset.stripId = strip.id;

    const color = STRIP_COLORS[strip.id % STRIP_COLORS.length];
    const scanlineCount = (strip.scanlines || []).length;
    const segmentCount = (strip.segments || []).length;
    const totalScanlineLeds = (strip.scanlines || []).reduce((sum, sc) => {
      const dx = Math.abs(sc.end[0] - sc.start[0]);
      const dy = Math.abs(sc.end[1] - sc.start[1]);
      return sum + dx + dy + 1;
    }, 0);

    card.innerHTML = `
      <div class="pm-strip-header">
        <span class="pm-strip-chevron">&#9654;</span>
        <span class="pm-strip-color-dot" style="background:${color}"></span>
        <span class="pm-strip-title">Strip ${strip.id}</span>
        <span class="pm-strip-summary">Out ${strip.output}+${strip.output_offset} | ${strip.total_leds} LEDs | ${scanlineCount} scanlines</span>
        <button class="pm-strip-delete" data-strip-id="${strip.id}">Delete</button>
      </div>
      <div class="pm-strip-body">
        <div class="pm-field-row">
          <div class="pm-field">
            <label>Output Pin</label>
            <input type="number" data-field="output" value="${strip.output}" min="0" max="7" step="1">
          </div>
          <div class="pm-field">
            <label>Output Offset</label>
            <input type="number" data-field="output_offset" value="${strip.output_offset}" min="0" max="2400" step="1">
          </div>
          <div class="pm-field">
            <label>Total LEDs</label>
            <input type="number" data-field="total_leds" value="${strip.total_leds}" min="0" max="2400" step="1">
          </div>
        </div>

        <div class="pm-section-label">Scanlines</div>
        <table class="pm-sub-table pm-scanline-table">
          <thead>
            <tr><th>Start X</th><th>Start Y</th><th>End X</th><th>End Y</th><th>LEDs</th><th></th></tr>
          </thead>
          <tbody></tbody>
        </table>
        <button class="pm-add-row-btn pm-add-scanline" data-strip-id="${strip.id}">+ Scanline</button>

        <div class="pm-section-label">Segments</div>
        <table class="pm-sub-table pm-segment-table">
          <thead>
            <tr><th>Range Start</th><th>Range End</th><th>Color Order</th><th></th></tr>
          </thead>
          <tbody></tbody>
        </table>
        <button class="pm-add-row-btn pm-add-segment" data-strip-id="${strip.id}">+ Segment</button>
      </div>
    `;
    container.appendChild(card);

    // Populate scanline rows
    const scanTbody = card.querySelector('.pm-scanline-table tbody');
    for (let si = 0; si < (strip.scanlines || []).length; si++) {
      const sc = strip.scanlines[si];
      const ledCount = Math.abs(sc.end[0] - sc.start[0]) + Math.abs(sc.end[1] - sc.start[1]) + 1;
      const row = document.createElement('tr');
      row.innerHTML = `
        <td><input type="number" data-idx="${si}" data-coord="sx" value="${sc.start[0]}" min="0" max="999"></td>
        <td><input type="number" data-idx="${si}" data-coord="sy" value="${sc.start[1]}" min="0" max="9999"></td>
        <td><input type="number" data-idx="${si}" data-coord="ex" value="${sc.end[0]}" min="0" max="999"></td>
        <td><input type="number" data-idx="${si}" data-coord="ey" value="${sc.end[1]}" min="0" max="9999"></td>
        <td class="pm-led-count">${ledCount}</td>
        <td><button class="pm-row-delete" data-idx="${si}" data-type="scanline">&#x2715;</button></td>
      `;
      scanTbody.appendChild(row);
    }

    // Populate segment rows
    const segTbody = card.querySelector('.pm-segment-table tbody');
    for (let si = 0; si < (strip.segments || []).length; si++) {
      const seg = strip.segments[si];
      const colorOpts = COLOR_ORDERS.map(o =>
        `<option value="${o}" ${o === seg.color_order ? 'selected' : ''}>${o}</option>`
      ).join('');
      const row = document.createElement('tr');
      row.innerHTML = `
        <td><input type="number" data-idx="${si}" data-field="range_start" value="${seg.range_start}" min="0" max="9999"></td>
        <td><input type="number" data-idx="${si}" data-field="range_end" value="${seg.range_end}" min="0" max="9999"></td>
        <td><select data-idx="${si}" data-field="color_order">${colorOpts}</select></td>
        <td><button class="pm-row-delete" data-idx="${si}" data-type="segment">&#x2715;</button></td>
      `;
      segTbody.appendChild(row);
    }

    // Wire up events for this card
    wireStripCardEvents(card, strip.id);
  }
}

function wireStripCardEvents(card, stripId) {
  // Toggle expand/collapse
  const header = card.querySelector('.pm-strip-header');
  header.addEventListener('click', (e) => {
    if (e.target.closest('.pm-strip-delete')) return;
    card.classList.toggle('expanded');
  });

  // Delete strip
  card.querySelector('.pm-strip-delete').addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!confirm(`Delete strip ${stripId}?`)) return;
    const result = await api('DELETE', `/api/pixel-map/strips/${stripId}`);
    if (result && result.status === 'ok') {
      showPmStatus(`Strip ${stripId} deleted`);
      await loadPixelMap();
    } else {
      showPmStatus(result?.detail || 'Failed to delete strip', true);
    }
  });

  // Field changes (output, output_offset, total_leds) — debounced save of full strip
  card.querySelectorAll('.pm-field input[type="number"]').forEach(input => {
    let debounce = null;
    const saveStrip = () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => saveFullStrip(card, stripId), 500);
    };
    input.addEventListener('input', saveStrip);
    input.addEventListener('change', () => { clearTimeout(debounce); saveFullStrip(card, stripId); });
  });

  // Scanline input changes — debounced save
  card.querySelectorAll('.pm-scanline-table input').forEach(input => {
    let debounce = null;
    input.addEventListener('input', () => {
      // Update LED count display
      const row = input.closest('tr');
      updateScanlineLedCount(row);
      clearTimeout(debounce);
      debounce = setTimeout(() => saveFullStrip(card, stripId), 500);
    });
    input.addEventListener('change', () => { clearTimeout(debounce); saveFullStrip(card, stripId); });
  });

  // Segment input changes — debounced save
  card.querySelectorAll('.pm-segment-table input, .pm-segment-table select').forEach(input => {
    let debounce = null;
    input.addEventListener('input', () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => saveFullStrip(card, stripId), 500);
    });
    input.addEventListener('change', () => { clearTimeout(debounce); saveFullStrip(card, stripId); });
  });

  // Delete scanline/segment row
  card.querySelectorAll('.pm-row-delete').forEach(btn => {
    btn.addEventListener('click', () => {
      btn.closest('tr').remove();
      saveFullStrip(card, stripId);
    });
  });

  // Add scanline
  card.querySelector('.pm-add-scanline').addEventListener('click', () => {
    const tbody = card.querySelector('.pm-scanline-table tbody');
    const idx = tbody.querySelectorAll('tr').length;
    const row = document.createElement('tr');
    row.innerHTML = `
      <td><input type="number" data-idx="${idx}" data-coord="sx" value="0" min="0" max="999"></td>
      <td><input type="number" data-idx="${idx}" data-coord="sy" value="0" min="0" max="9999"></td>
      <td><input type="number" data-idx="${idx}" data-coord="ex" value="0" min="0" max="999"></td>
      <td><input type="number" data-idx="${idx}" data-coord="ey" value="0" min="0" max="9999"></td>
      <td class="pm-led-count">1</td>
      <td><button class="pm-row-delete" data-idx="${idx}" data-type="scanline">&#x2715;</button></td>
    `;
    tbody.appendChild(row);
    // Wire events for new row
    row.querySelectorAll('input').forEach(input => {
      let debounce = null;
      input.addEventListener('input', () => {
        updateScanlineLedCount(row);
        clearTimeout(debounce);
        debounce = setTimeout(() => saveFullStrip(card, stripId), 500);
      });
      input.addEventListener('change', () => { clearTimeout(debounce); saveFullStrip(card, stripId); });
    });
    row.querySelector('.pm-row-delete').addEventListener('click', () => {
      row.remove();
      saveFullStrip(card, stripId);
    });
  });

  // Add segment
  card.querySelector('.pm-add-segment').addEventListener('click', () => {
    const tbody = card.querySelector('.pm-segment-table tbody');
    const idx = tbody.querySelectorAll('tr').length;
    const colorOpts = COLOR_ORDERS.map(o =>
      `<option value="${o}" ${o === 'BGR' ? 'selected' : ''}>${o}</option>`
    ).join('');
    const row = document.createElement('tr');
    row.innerHTML = `
      <td><input type="number" data-idx="${idx}" data-field="range_start" value="0" min="0" max="9999"></td>
      <td><input type="number" data-idx="${idx}" data-field="range_end" value="0" min="0" max="9999"></td>
      <td><select data-idx="${idx}" data-field="color_order">${colorOpts}</select></td>
      <td><button class="pm-row-delete" data-idx="${idx}" data-type="segment">&#x2715;</button></td>
    `;
    tbody.appendChild(row);
    row.querySelectorAll('input, select').forEach(input => {
      let debounce = null;
      input.addEventListener('input', () => {
        clearTimeout(debounce);
        debounce = setTimeout(() => saveFullStrip(card, stripId), 500);
      });
      input.addEventListener('change', () => { clearTimeout(debounce); saveFullStrip(card, stripId); });
    });
    row.querySelector('.pm-row-delete').addEventListener('click', () => {
      row.remove();
      saveFullStrip(card, stripId);
    });
  });
}

function updateScanlineLedCount(row) {
  const inputs = row.querySelectorAll('input[type="number"]');
  if (inputs.length < 4) return;
  const sx = parseInt(inputs[0].value) || 0;
  const sy = parseInt(inputs[1].value) || 0;
  const ex = parseInt(inputs[2].value) || 0;
  const ey = parseInt(inputs[3].value) || 0;
  const count = Math.abs(ex - sx) + Math.abs(ey - sy) + 1;
  const countEl = row.querySelector('.pm-led-count');
  if (countEl) countEl.textContent = count;
}

function readStripFromCard(card) {
  const stripId = parseInt(card.dataset.stripId);

  // Read top-level fields
  const output = parseInt(card.querySelector('input[data-field="output"]').value) || 0;
  const outputOffset = parseInt(card.querySelector('input[data-field="output_offset"]').value) || 0;
  const totalLeds = parseInt(card.querySelector('input[data-field="total_leds"]').value) || 0;

  // Read scanlines
  const scanlines = [];
  card.querySelectorAll('.pm-scanline-table tbody tr').forEach(row => {
    const inputs = row.querySelectorAll('input[type="number"]');
    if (inputs.length < 4) return;
    scanlines.push({
      start: [parseInt(inputs[0].value) || 0, parseInt(inputs[1].value) || 0],
      end: [parseInt(inputs[2].value) || 0, parseInt(inputs[3].value) || 0],
    });
  });

  // Read segments
  const segments = [];
  card.querySelectorAll('.pm-segment-table tbody tr').forEach(row => {
    const rangeStart = parseInt(row.querySelector('input[data-field="range_start"]')?.value) || 0;
    const rangeEnd = parseInt(row.querySelector('input[data-field="range_end"]')?.value) || 0;
    const colorOrder = row.querySelector('select[data-field="color_order"]')?.value || 'BGR';
    segments.push({ range_start: rangeStart, range_end: rangeEnd, color_order: colorOrder });
  });

  return {
    id: stripId,
    output,
    output_offset: outputOffset,
    total_leds: totalLeds,
    scanlines,
    segments,
  };
}

async function saveFullStrip(card, stripId) {
  const body = readStripFromCard(card);
  const result = await api('POST', `/api/pixel-map/strips/${stripId}`, body);
  if (result && result.status === 'ok') {
    showPmStatus(`Strip ${stripId} saved`);
    // Reload to refresh grid preview
    await loadPixelMap();
  } else {
    showPmStatus(result?.detail || 'Error saving strip', true);
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
    for (const [pin, entries] of Object.entries(data.output_config)) {
      for (const e of entries) {
        lines.push(`  Pin ${pin}: strip ${e.strip_id} @ offset ${e.offset}, ${e.count} LEDs`);
      }
    }
  }
  lines.push(`\nLast CONFIG ACK: ${data.last_config_ack === true ? 'ACK' : data.last_config_ack === false ? 'NAK' : '--'}`);
  el.textContent = lines.join('\n');
}

function initSetup() {
  // Origin change
  document.getElementById('pm-origin-select').addEventListener('change', async (e) => {
    const result = await api('POST', '/api/pixel-map/origin', { origin: e.target.value });
    if (result && result.status === 'ok') {
      showPmStatus(`Origin set to ${e.target.value}`);
      await loadPixelMap();
    } else {
      showPmStatus(result?.detail || 'Error setting origin', true);
    }
  });

  // Add strip
  document.getElementById('pm-add-strip-btn').addEventListener('click', async () => {
    // Find next available strip ID
    const existingIds = (_pixelMapData?.strips || []).map(s => s.id);
    let nextId = 0;
    while (existingIds.includes(nextId)) nextId++;

    const newStrip = {
      id: nextId,
      output: 0,
      output_offset: 0,
      total_leds: 172,
      scanlines: [{ start: [nextId, 0], end: [nextId, 171] }],
      segments: [{ range_start: 0, range_end: 171, color_order: 'BGR' }],
    };

    const result = await api('POST', '/api/pixel-map/strips', newStrip);
    if (result && result.status === 'ok') {
      showPmStatus(`Strip ${nextId} added`);
      await loadPixelMap();
    } else {
      showPmStatus(result?.detail || 'Error adding strip', true);
    }
  });

  // Validate button
  document.getElementById('pm-validate-btn').addEventListener('click', async () => {
    const result = await api('POST', '/api/pixel-map/validate');
    const el = document.getElementById('pm-validation-result');
    if (!result) {
      el.innerHTML = '<div class="pm-errors">Failed to validate</div>';
      return;
    }
    if (result.valid) {
      el.innerHTML = '<div class="pm-valid">Configuration is valid</div>';
    } else {
      const items = (result.errors || []).map(e => `<li>${e}</li>`).join('');
      el.innerHTML = `<div class="pm-errors">Validation errors:<ul>${items}</ul></div>`;
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
  if (!data || !data.strips) return;
  const container = document.getElementById('sim-strip-labels');
  container.innerHTML = '';
  for (const strip of data.strips) {
    const div = document.createElement('div');
    div.className = 'sim-strip-label';
    // Infer direction from first scanline: if start Y < end Y, it goes up (bottom-to-top)
    let arrow = '\u2193';
    if (strip.scanlines && strip.scanlines.length > 0) {
      const sc = strip.scanlines[0];
      arrow = sc.start[1] < sc.end[1] ? '\u2191' : '\u2193';
    }
    div.innerHTML = `S${strip.id}<br>${arrow}`;
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
