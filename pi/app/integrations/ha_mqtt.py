"""
Home Assistant MQTT bridge — exposes OverheadService as one HA light.

paho runs its network loop in its own thread (loop_start). Everything that
touches OverheadService is marshalled onto the asyncio loop, so the service
never sees concurrent access from the paho thread.
"""

import asyncio
import json
import logging

from . import ha_payloads

logger = logging.getLogger(__name__)

try:
  import paho.mqtt.client as paho_mqtt
except ImportError:  # optional extra: pip install .[homeassistant]
  paho_mqtt = None

PUBLISH_QOS = 1
OFFLINE_WAIT_S = 2.0


def _default_client_factory(client_id: str):
  return paho_mqtt.Client(paho_mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)


class HAMqttBridge:
  def __init__(self, cfg: dict, service, client_factory=None, poll_interval: float = 1.0):
    self.cfg = ha_payloads.resolve_config(cfg)
    self.service = service
    self.topics = ha_payloads.topics(self.cfg)
    self.poll_interval = poll_interval
    self._client_factory = client_factory
    self._client = None
    self._loop = None
    self._poll_task = None
    self._effects = []
    self._last_state = None   # serialized payload; only touched on the asyncio loop
    self._last_error = None

  # --- Lifecycle ---

  async def start(self):
    if not self.cfg['enabled']:
      logger.info("HA MQTT bridge disabled (mqtt.enabled is false)")
      return
    factory = self._client_factory
    if factory is None:
      if paho_mqtt is None:
        logger.warning("HA MQTT bridge disabled: paho-mqtt not installed "
                       "(pip install '.[homeassistant]')")
        return
      factory = _default_client_factory

    self._loop = asyncio.get_running_loop()
    # Effect list is static catalog data; caching lets the paho thread parse
    # commands without calling into the service.
    self._effects = self.service.effects()

    client = factory(f"{self.cfg['node_id']}-overhead")
    if self.cfg['username']:
      client.username_pw_set(self.cfg['username'], self.cfg['password'] or None)
    # Retained so HA shows "unavailable" after a crash/power loss, not stale "on"
    client.will_set(self.topics.availability, 'offline', qos=PUBLISH_QOS, retain=True)
    client.on_connect = self._on_connect
    client.on_disconnect = self._on_disconnect
    client.on_message = self._on_message
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    # connect_async: a missing broker at boot must not block or fail startup;
    # the loop thread keeps retrying.
    client.connect_async(self.cfg['host'], int(self.cfg['port']), keepalive=60)
    self._client = client
    client.loop_start()
    self._poll_task = asyncio.create_task(self._poll())
    logger.info(f"HA MQTT bridge started → {self.cfg['host']}:{self.cfg['port']}")

  async def stop(self):
    if self._poll_task:
      self._poll_task.cancel()
      try:
        await self._poll_task
      except asyncio.CancelledError:
        pass
      self._poll_task = None
    client, self._client = self._client, None
    if client is None:
      return
    try:
      # A clean disconnect suppresses the LWT, so announce offline ourselves
      info = client.publish(self.topics.availability, 'offline', qos=PUBLISH_QOS, retain=True)
      await asyncio.to_thread(info.wait_for_publish, OFFLINE_WAIT_S)
    except Exception as exc:
      logger.warning(f"HA MQTT offline publish not confirmed: {exc}")
    try:
      client.disconnect()
      client.loop_stop()
    except Exception:
      logger.exception("HA MQTT shutdown error")
    logger.info("HA MQTT bridge stopped")

  # --- paho callbacks (paho thread; must never raise) ---

  def _on_connect(self, client, userdata, flags, reason_code, properties=None):
    try:
      if getattr(reason_code, 'is_failure', False):
        logger.warning(f"HA MQTT connect refused: {reason_code}")
        return
      logger.info("HA MQTT connected")
      client.publish(self.topics.availability, 'online', qos=PUBLISH_QOS, retain=True)
      self._publish_discovery(client)
      client.subscribe([(self.topics.command, PUBLISH_QOS), (self.topics.ha_status, PUBLISH_QOS)])
      # Broker may have lost retained state (restart without persistence)
      self._schedule(self._publish_state(force=True))
    except Exception:
      logger.exception("HA MQTT on_connect failed")

  def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
    try:
      logger.warning(f"HA MQTT disconnected: {reason_code}")
    except Exception:
      pass

  def _on_message(self, client, userdata, message):
    try:
      if message.topic == self.topics.ha_status:
        # HA restarted: it forgets non-retained discovery, so re-announce
        if message.payload.decode('utf-8', 'replace').strip() == 'online':
          self._publish_discovery(client)
          self._schedule(self._publish_state(force=True))
        return
      if message.topic == self.topics.command:
        try:
          cmd = ha_payloads.parse_command(message.payload, self._effects)
        except ValueError as exc:
          logger.warning(f"HA MQTT bad command ignored: {exc}")
          return
        self._schedule(self._apply(cmd))
    except Exception:
      logger.exception("HA MQTT on_message failed")

  # --- asyncio side ---

  def _schedule(self, coro):
    # Don't block the paho thread on the result: it would stall keepalives
    asyncio.run_coroutine_threadsafe(coro, self._loop)

  async def _apply(self, cmd):
    try:
      state = await self.service.apply(cmd)
      self._publish(ha_payloads.state_payload(state, self._effects), force=True)
    except ValueError as exc:
      logger.warning(f"HA MQTT command rejected: {exc}")
    except Exception:
      logger.exception("HA MQTT command failed")

  async def _publish_state(self, force: bool = False):
    try:
      state = self.service.snapshot()
      self._publish(ha_payloads.state_payload(state, self._effects), force=force)
      self._last_error = None
    except Exception as exc:
      # The 1 s poll would otherwise flood the Pi's log with identical tracebacks
      if repr(exc) != self._last_error:
        self._last_error = repr(exc)
        logger.exception("HA MQTT state publish failed")

  async def _poll(self):
    # Catches phone/pot/scene changes without hooking every mutation path
    while True:
      await asyncio.sleep(self.poll_interval)
      await self._publish_state()

  def _publish(self, payload: dict, force: bool):
    serialized = json.dumps(payload, sort_keys=True)
    if not force and serialized == self._last_state:
      return
    self._last_state = serialized
    if self._client is not None:
      self._client.publish(self.topics.state, serialized, qos=PUBLISH_QOS, retain=True)

  def _publish_discovery(self, client):
    payload = ha_payloads.discovery_payload(self.cfg, self._effects)
    client.publish(self.topics.config, json.dumps(payload), qos=PUBLISH_QOS, retain=True)


def create_bridge(sys_conf: dict, service, **kwargs) -> HAMqttBridge:
  """Build the bridge from system.yaml's `mqtt:` section (defaults fill gaps)."""
  return HAMqttBridge((sys_conf or {}).get('mqtt') or {}, service, **kwargs)
