"""
Forensic incident bundles for Teensy link episodes and manual maintenance.

Evidence only: nothing here acts on the link. A capture never raises — write
failures are logged, retried, then diverted to a fallback root — and the
caller (detector task or a user-requested reset) is never blocked by a probe
for longer than the probe timeouts below.

The transport trace is frozen BEFORE any live probe (DIAG/CRASH request) so
the probe's own traffic cannot overwrite the evidence it is meant to explain.
"""

import asyncio
import json
import logging
import os
import re
import secrets
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
INCIDENT_ID_RE = re.compile(r'^\d{8}-\d{6}-[a-z_]+-[0-9a-f]{6}$')
INCIDENT_FILE_RE = re.compile(r'^\d{8}-\d{6}-[a-z_]+-[0-9a-f]{6}\.json$')
WRITE_ATTEMPTS = 3
RETRY_DELAY_S = 2.0
LIVE_DIAG_TIMEOUT_S = 0.5
JOURNAL_TIMEOUT_S = 5.0
JOURNAL_SERVICE_CMD = ['journalctl', '-u', 'ledfanatic', '-n', '300', '--no-pager', '-o', 'short-iso']
JOURNAL_KERNEL_CMD = ['journalctl', '-k', '--since', '-10min', '--no-pager', '-o', 'short-iso']


@dataclass
class CaptureResult:
  id: str
  path: Optional[Path]
  error: Optional[str]

  def to_dict(self) -> dict:
    return {'id': self.id, 'path': str(self.path) if self.path else None, 'error': self.error}


def _safe(label: str, fn: Callable[[], Any]):
  """Evidence gathering must degrade field-by-field, never lose the bundle."""
  try:
    return fn()
  except Exception as e:
    logger.warning(f"incident capture: {label} failed: {e!r}")
    return {'error': repr(e)}


class IncidentRecorder:
  def __init__(self, transport, render_state, state_manager, brightness_engine, logs_dir: Path,
               fallback_dir: Path = Path('/tmp/ledfanatic-incidents'),
               retry_delay_s: float = RETRY_DELAY_S,
               live_diag_timeout_s: float = LIVE_DIAG_TIMEOUT_S):
    self.transport = transport
    self.render_state = render_state
    self.state_manager = state_manager
    self.brightness_engine = brightness_engine
    self.incidents_dir = Path(logs_dir) / 'incidents'
    self.fallback_dir = Path(fallback_dir)
    self.retry_delay_s = retry_delay_s
    self.live_diag_timeout_s = live_diag_timeout_s

  @property
  def roots(self) -> list[Path]:
    return [self.incidents_dir, self.fallback_dir]

  # --- capture ---

  async def capture_event(self, kind: str, detail: dict) -> CaptureResult:
    """LinkDetector.on_incident hook. `detail` carries reason/since plus the
    detector's evidence (previous-connection snapshot, crash record, ...)."""
    detail = dict(detail or {})
    reason = str(detail.pop('reason', None) or kind)
    return await self.capture(kind, reason, extra=detail)

  async def capture(self, trigger: str, reason: str, extra: Optional[dict] = None) -> CaptureResult:
    """Build and persist a bundle. Never raises."""
    trigger = _sanitize_trigger(trigger)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    try:
      bundle = await self._build_bundle(trigger, reason, extra)
    except Exception as e:
      # Even a broken probe path must leave a file behind
      logger.exception("incident capture: bundle build failed")
      bundle = {'schema_version': SCHEMA_VERSION, 'trigger': trigger, 'reason': reason,
                'at': datetime.now().isoformat(timespec='seconds'), 'at_epoch': time.time(),
                'build_error': repr(e), 'extra': extra}
    try:
      incident_id, path, error = await asyncio.to_thread(self._persist, stamp, trigger, bundle)
    except Exception as e:
      logger.exception("incident capture: persist failed unexpectedly")
      incident_id, path, error = f'{stamp}-{trigger}-{secrets.token_hex(3)}', None, repr(e)
    if error:
      logger.error(f"incident {incident_id} NOT saved: {error}")
    else:
      logger.warning(f"incident {incident_id} saved: {path}")
    return CaptureResult(incident_id, path, error)

  async def _build_bundle(self, trigger: str, reason: str, extra: Optional[dict]) -> dict:
    t = self.transport
    # Synchronous snapshot first: the frozen trace must predate the probes below
    diag_at = _safe('last_diag_at', lambda: getattr(t, 'last_diag_at', None))
    last_diag = _safe('last_diag', lambda: getattr(t, 'last_teensy_diag', None))
    if isinstance(last_diag, dict) and 'error' not in last_diag:
      age = round(time.monotonic() - diag_at, 2) if isinstance(diag_at, (int, float)) else None
      last_diag = {**last_diag, 'age_s': age}
    bundle = {
      'schema_version': SCHEMA_VERSION,
      'trigger': trigger,
      'reason': reason,
      'at': datetime.now().isoformat(timespec='seconds'),
      'at_epoch': time.time(),
      'transport': _safe('transport.get_status', t.get_status),
      'stalled': _safe('stalled', lambda: getattr(t, 'stalled', None)),
      'last_stats': _safe('last_stats', lambda: getattr(t, 'last_teensy_stats', None)),
      'last_diag': last_diag,
      'trace': _safe('trace.freeze', lambda: t.trace.freeze() if getattr(t, 'trace', None) else None),
    }
    bundle['live_diag'] = await self._live_diag()
    bundle['crash'] = await self._live_crash()
    bundle['render'] = _safe('render', self._render_snapshot)
    bundle['system'] = _safe('system', _system_snapshot)
    bundle['journal_service'] = await asyncio.to_thread(_run_journal, JOURNAL_SERVICE_CMD)
    bundle['journal_kernel'] = await asyncio.to_thread(_run_journal, JOURNAL_KERNEL_CMD)
    bundle['extra'] = extra
    return bundle

  async def _live_diag(self) -> dict:
    request_diag = getattr(self.transport, 'request_diag', None)
    if request_diag is None:
      return {'error': 'unsupported'}
    try:
      # Outer wait_for bounds a transport that ignores its own timeout argument
      diag = await asyncio.wait_for(request_diag(timeout=self.live_diag_timeout_s),
                                    self.live_diag_timeout_s * 2)
    except asyncio.TimeoutError:
      return {'error': 'timeout'}
    except Exception as e:
      return {'error': repr(e)}
    return diag if diag is not None else {'error': 'timeout'}

  async def _live_crash(self) -> Optional[dict]:
    request_crash = getattr(self.transport, 'request_crash', None)
    if request_crash is None:
      return None
    try:
      return await asyncio.wait_for(request_crash(), self.live_diag_timeout_s * 2)
    except asyncio.TimeoutError:
      return {'error': 'timeout'}
    except Exception as e:
      return {'error': repr(e)}

  def _render_snapshot(self) -> dict:
    rs = self.render_state
    return {
      'scene': getattr(rs, 'current_scene', None),
      'params': getattr(self.state_manager, 'current_params', None),
      'target_fps': getattr(rs, 'target_fps', None),
      'actual_fps': round(getattr(rs, 'actual_fps', 0.0) or 0.0, 1),
      'blackout': getattr(rs, 'blackout', None),
      'brightness_effective': self.brightness_engine.get_status().get('effective_brightness'),
    }

  # --- persistence ---

  def _persist(self, stamp: str, trigger: str, bundle: dict) -> tuple[str, Optional[Path], Optional[str]]:
    """Blocking. Try the primary root WRITE_ATTEMPTS times, then the fallback."""
    text = json.dumps(bundle, indent=1, default=str)
    errors = []
    for root_index, root in enumerate(self.roots):
      attempts = WRITE_ATTEMPTS if root_index == 0 else 1
      for attempt in range(attempts):
        try:
          incident_id, path = self._write_new(root, stamp, trigger, text)
          if root_index > 0:
            logger.error(f"incident {incident_id} written to fallback {root} after: {errors}")
          return incident_id, path, None
        except OSError as e:
          errors.append(f'{root}: {e!r}')
          logger.warning(f"incident write to {root} failed (attempt {attempt + 1}/{attempts}): {e!r}")
          if attempt + 1 < attempts and self.retry_delay_s > 0:
            time.sleep(self.retry_delay_s)
    # Nothing on disk: the id is still handed out so logs can correlate the loss
    return f'{stamp}-{trigger}-{secrets.token_hex(3)}', None, '; '.join(errors)

  def _write_new(self, root: Path, stamp: str, trigger: str, text: str) -> tuple[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    while True:
      incident_id = f'{stamp}-{trigger}-{secrets.token_hex(3)}'
      final = root / f'{incident_id}.json'
      try:
        # 'x' reserves the id atomically: a second capture in the same second
        # with the same random suffix gets FileExistsError and re-rolls
        with open(final, 'x'):
          pass
      except FileExistsError:
        continue
      _replace_contents(final, text)
      return incident_id, final

  async def append(self, incident_id: str, key: str, value) -> bool:
    """Atomically add `key` to an existing bundle. False (and the value logged
    so it is never lost) when the bundle is missing or unwritable."""
    try:
      return await asyncio.to_thread(self._append_sync, incident_id, key, value)
    except Exception as e:
      logger.error(f"incident {incident_id}: append {key!r} failed: {e!r}; value={_dump(value)}")
      return False

  def _append_sync(self, incident_id: str, key: str, value) -> bool:
    path = self.path_for(incident_id)
    if path is None:
      logger.error(f"incident {incident_id}: not found, cannot append {key!r}; value={_dump(value)}")
      return False
    try:
      with open(path, 'r', encoding='utf-8') as f:
        bundle = json.load(f)
      bundle[key] = value
      _replace_contents(path, json.dumps(bundle, indent=1, default=str))
      return True
    except (OSError, ValueError) as e:
      logger.error(f"incident {incident_id}: append {key!r} failed: {e!r}; value={_dump(value)}")
      return False

  # --- lookup ---

  def list(self) -> list[dict]:
    """Bundles in both roots, newest first."""
    items = []
    for root_index, root in enumerate(self.roots):
      if not root.is_dir():
        continue
      for path in root.iterdir():
        if not INCIDENT_FILE_RE.match(path.name) or not path.is_file():
          continue
        incident_id = path.stem
        try:
          size = path.stat().st_size
        except OSError:
          size = None
        items.append({
          'id': incident_id,
          'trigger': incident_id.split('-', 2)[2].rsplit('-', 1)[0],
          'at': _at_from_id(incident_id),
          'size_bytes': size,
          'fallback': root_index > 0,
        })
    items.sort(key=lambda item: item['id'], reverse=True)
    return items

  def path_for(self, incident_id: str) -> Optional[Path]:
    """Resolve an id to its file, searching the primary root then the fallback.
    The regex forbids separators; containment is still checked per root."""
    if not isinstance(incident_id, str) or not INCIDENT_ID_RE.match(incident_id):
      return None
    for root in self.roots:
      candidate = root / f'{incident_id}.json'
      try:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root.resolve()):
          continue
        if resolved.is_file():
          return resolved
      except OSError:
        continue
    return None


# --- helpers ---

def _sanitize_trigger(trigger: str) -> str:
  cleaned = re.sub(r'[^a-z_]', '_', str(trigger or 'unknown').lower())
  return cleaned.strip('_') or 'unknown'


def _at_from_id(incident_id: str) -> Optional[str]:
  try:
    return datetime.strptime(incident_id[:15], '%Y%m%d-%H%M%S').isoformat(timespec='seconds')
  except ValueError:
    return None


def _replace_contents(final: Path, text: str):
  tmp = final.with_name(final.name + '.tmp')
  with open(tmp, 'w', encoding='utf-8') as f:
    f.write(text)
    f.flush()
    os.fsync(f.fileno())
  os.replace(tmp, final)


def _dump(value) -> str:
  try:
    return json.dumps(value, default=str)
  except Exception:
    return repr(value)


def _run_journal(cmd: list[str]) -> dict:
  """Blocking journalctl call → {rc, text} or {rc: None, error}."""
  try:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=JOURNAL_TIMEOUT_S)
  except (OSError, subprocess.SubprocessError) as e:
    return {'rc': None, 'error': repr(e)}
  result = {'rc': proc.returncode, 'text': proc.stdout}
  if proc.returncode != 0 and proc.stderr:
    result['error'] = proc.stderr.strip()
  return result


def _system_snapshot() -> dict:
  info: dict = {'uptime_s': None, 'loadavg': None, 'mem_available_kb': None}
  try:
    with open('/proc/uptime') as f:
      info['uptime_s'] = float(f.read().split()[0])
  except (OSError, ValueError, IndexError):
    pass
  try:
    info['loadavg'] = list(os.getloadavg())
  except (OSError, AttributeError):
    pass
  try:
    with open('/proc/meminfo') as f:
      for line in f:
        if line.startswith('MemAvailable:'):
          info['mem_available_kb'] = int(line.split()[1])
          break
  except (OSError, ValueError, IndexError):
    pass
  return info
