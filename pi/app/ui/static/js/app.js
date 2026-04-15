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
  if (tab.dataset.tab === 'audio') loadAudioDevices();
  if (tab.dataset.tab === 'diag') loadStats();
  if (tab.dataset.tab === 'system') loadSystemStatus();
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
  if (!hasSpeed) {
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

  wrap.classList.remove('hidden');
}

async function activateEffect(name) {
  activeEffectName = name;
  // Activate without sending params — backend keeps saved/default params
  const result = await api('POST', '/api/scenes/activate', { effect: name });
  // Load the actual active params from the backend
  const catalog = await api('GET', '/api/effects/catalog');
  if (catalog && catalog.current_params && Object.keys(catalog.current_params).length > 0) {
    currentEffectParams = { ...catalog.current_params };
  } else {
    currentEffectParams = {};
  }
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

let setupStripsCache = [];
let setupStageTimer = null;

async function loadStripInventory() {
  const data = await api('GET', '/api/setup/installation');
  if (!data || !data.strips) return;
  setupStripsCache = data.strips;

  const container = document.getElementById('strip-cards');
  container.innerHTML = '';

  for (const strip of data.strips) {
    const dirArrow = strip.direction === 'bottom_to_top' ? '\u2191' : '\u2193';
    const card = document.createElement('div');
    card.className = 'strip-card';
    card.dataset.id = strip.id;

    const colorOrders = ['RGB','RBG','GRB','GBR','BRG','BGR'];
    const colorOpts = colorOrders.map(o =>
      `<option value="${o}" ${o === strip.color_order ? 'selected' : ''}>${o}</option>`
    ).join('');

    const dirOpts = `
      <option value="bottom_to_top" ${strip.direction === 'bottom_to_top' ? 'selected' : ''}>\u2191 Bottom\u2192Top</option>
      <option value="top_to_bottom" ${strip.direction === 'top_to_bottom' ? 'selected' : ''}>\u2193 Top\u2192Bottom</option>
    `;

    const chipsets = ['WS2812B','WS2812','WS2811','SK6812','WS2813','WS2815'];
    const chipOpts = chipsets.map(c =>
      `<option value="${c}" ${c === (strip.chipset || 'WS2812B') ? 'selected' : ''}>${c}</option>`
    ).join('');

    card.innerHTML = `
      <div class="strip-card-header">
        <span class="strip-summary">${strip.label} &mdash; Ch${strip.output_channel}:${strip.output_slot} ${dirArrow} ${strip.installed_led_count} LEDs ${strip.color_order}</span>
        <span class="expand-icon">\u25B6</span>
      </div>
      <div class="strip-card-body" hidden>
        <div class="strip-field"><label>Label</label><input type="text" value="${strip.label}" data-field="label" data-id="${strip.id}" maxlength="10"></div>
        <div class="strip-field"><label>Enabled</label><input type="checkbox" ${strip.enabled ? 'checked' : ''} data-field="enabled" data-id="${strip.id}"></div>
        <div class="strip-field"><label>Logical Order</label><input type="number" value="${strip.logical_order !== undefined ? strip.logical_order : strip.id}" data-field="logical_order" data-id="${strip.id}" min="0" max="9"></div>
        <div class="strip-field"><label>LEDs</label><input type="number" value="${strip.installed_led_count}" data-field="installed_led_count" data-id="${strip.id}" min="0" max="172"></div>
        <div class="strip-field"><label>Color Order</label><select data-field="color_order" data-id="${strip.id}">${colorOpts}</select></div>
        <div class="strip-field"><label>Direction</label><select data-field="direction" data-id="${strip.id}">${dirOpts}</select></div>
        <div class="strip-field"><label>Channel</label><input type="number" value="${strip.output_channel}" data-field="output_channel" data-id="${strip.id}" min="0" max="4"></div>
        <div class="strip-field"><label>Slot</label><input type="number" value="${strip.output_slot}" data-field="output_slot" data-id="${strip.id}" min="0" max="1"></div>
        <div class="strip-field"><label>Chipset</label><select data-field="chipset" data-id="${strip.id}">${chipOpts}</select></div>
      </div>
    `;
    container.appendChild(card);
  }

  // Accordion behavior
  container.querySelectorAll('.strip-card-header').forEach(header => {
    header.addEventListener('click', () => {
      const card = header.closest('.strip-card');
      const body = card.querySelector('.strip-card-body');
      const wasHidden = body.hidden;
      // Collapse all
      container.querySelectorAll('.strip-card').forEach(c => {
        c.querySelector('.strip-card-body').hidden = true;
        c.classList.remove('expanded');
      });
      if (wasHidden) {
        body.hidden = false;
        card.classList.add('expanded');
      }
    });
  });

  // Attach change handlers for auto-staging
  container.querySelectorAll('input, select').forEach(el => {
    el.addEventListener('change', () => stageStripUpdate(el));
  });

  validateSetupStrips();
}

function stageStripUpdate(el) {
  const id = parseInt(el.dataset.id);
  const field = el.dataset.field;
  let value;

  if (el.type === 'checkbox') {
    value = el.checked;
  } else if (el.type === 'number') {
    value = parseInt(el.value);
  } else {
    value = el.value;
  }

  // Update local cache
  const cached = setupStripsCache.find(s => s.id === id);
  if (cached) cached[field] = value;

  // Update summary in card header
  const card = el.closest('.strip-card');
  if (card && cached) {
    const dirArrow = cached.direction === 'bottom_to_top' ? '\u2191' : '\u2193';
    card.querySelector('.strip-summary').innerHTML =
      `${cached.label} &mdash; Ch${cached.output_channel}:${cached.output_slot} ${dirArrow} ${cached.installed_led_count} LEDs ${cached.color_order}`;
  }

  // Client-side validation
  const valid = validateSetupStrips();

  // Debounce PUT to server
  clearTimeout(setupStageTimer);
  setupStageTimer = setTimeout(async () => {
    if (!setupSessionId) return;
    const update = { id };
    update[field] = value;
    await api('PUT', '/api/setup/session/installation', {
      session_id: setupSessionId,
      strips: [update],
    });
  }, 300);
}

function validateSetupStrips() {
  const msg = document.getElementById('setup-validation-msg');
  const errors = [];

  // Clear all invalid states
  document.querySelectorAll('#strip-cards .invalid').forEach(el => el.classList.remove('invalid'));

  // Check duplicate logical orders among enabled strips
  const enabledStrips = setupStripsCache.filter(s => s.enabled);
  const logOrders = enabledStrips.map(s => s.logical_order !== undefined ? s.logical_order : s.id);
  const logOrderSet = new Set(logOrders);
  if (logOrderSet.size < logOrders.length) {
    errors.push('Duplicate logical orders among enabled strips');
    // Mark duplicates
    const seen = {};
    for (const s of enabledStrips) {
      const lo = s.logical_order !== undefined ? s.logical_order : s.id;
      if (seen[lo]) {
        markFieldInvalid(s.id, 'logical_order');
        markFieldInvalid(seen[lo], 'logical_order');
      }
      seen[lo] = s.id;
    }
  }

  // Check duplicate channel/slot pairs
  const chSlots = enabledStrips.map(s => `${s.output_channel}:${s.output_slot}`);
  const chSlotSet = new Set(chSlots);
  if (chSlotSet.size < chSlots.length) {
    errors.push('Duplicate channel/slot pairs');
    const seen = {};
    for (const s of enabledStrips) {
      const key = `${s.output_channel}:${s.output_slot}`;
      if (seen[key]) {
        markFieldInvalid(s.id, 'output_channel');
        markFieldInvalid(s.id, 'output_slot');
        markFieldInvalid(seen[key], 'output_channel');
        markFieldInvalid(seen[key], 'output_slot');
      }
      seen[key] = s.id;
    }
  }

  // Check LED count range
  for (const s of setupStripsCache) {
    if (s.installed_led_count < 0 || s.installed_led_count > 172) {
      errors.push(`Strip ${s.id}: LED count out of range`);
      markFieldInvalid(s.id, 'installed_led_count');
    }
  }

  if (errors.length === 0) {
    msg.textContent = 'All strips valid';
    msg.className = '';
    msg.id = 'setup-validation-msg';
  } else {
    msg.textContent = errors.join('; ');
    msg.className = 'has-errors';
    msg.id = 'setup-validation-msg';
  }

  return errors.length === 0;
}

function markFieldInvalid(stripId, fieldName) {
  const el = document.querySelector(`[data-id="${stripId}"][data-field="${fieldName}"]`);
  if (el) el.classList.add('invalid');
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
  const data = await api('GET', '/api/setup/installation');
  if (!data || !data.strips) return;
  const container = document.getElementById('sim-strip-labels');
  container.innerHTML = '';
  for (const strip of data.strips) {
    const div = document.createElement('div');
    div.className = 'sim-strip-label';
    const arrow = strip.direction === 'bottom_to_top' ? '\u2191' : '\u2193';
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
