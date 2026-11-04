"""Home Assistant MQTT payloads + bridge (fake paho client, no broker)."""

import asyncio
import json
import threading

import pytest

from app.core.overhead import OverheadCommand, OverheadState
from app.integrations import ha_payloads as hp
from app.integrations.ha_mqtt import HAMqttBridge, create_bridge

EFFECTS = [('oh_white', 'White'), ('oh_color', 'Color'), ('oh_aurora', 'Aurora Glow')]
CFG = {'enabled': True, 'node_id': 'ledfanatic', 'discovery_prefix': 'homeassistant',
       'name': 'Overhead Light'}


def _state(**overrides):
  base = dict(on=True, brightness=0.5, effect='oh_white', kelvin=2700,
              rgb=(255, 0, 64), color_mode='color_temp', params={})
  base.update(overrides)
  return OverheadState(**base)


# --- Topics / discovery ---

def test_topics_derived_from_cfg():
  topic = hp.topics({'node_id': 'den', 'discovery_prefix': 'ha'})
  assert topic.config == 'ha/light/den/overhead/config'
  assert topic.command == 'den/overhead/set'
  assert topic.state == 'den/overhead/state'
  assert topic.availability == 'den/status'
  assert topic.ha_status == 'ha/status'


def test_discovery_required_keys():
  disc = hp.discovery_payload(CFG, EFFECTS)
  assert disc['schema'] == 'json'
  assert disc['unique_id'] == 'ledfanatic_overhead'
  assert disc['command_topic'] == 'ledfanatic/overhead/set'
  assert disc['state_topic'] == 'ledfanatic/overhead/state'
  assert disc['availability_topic'] == 'ledfanatic/status'
  assert disc['brightness'] is True
  assert disc['brightness_scale'] == 255
  assert disc['supported_color_modes'] == ['color_temp', 'rgb']
  assert disc['color_temp_kelvin'] is True
  assert (disc['min_kelvin'], disc['max_kelvin']) == (2200, 6500)
  assert disc['effect'] is True
  assert disc['effect_list'] == ['White', 'Color', 'Aurora Glow']
  assert disc['transition'] is False
  assert disc['flash'] is False
  device = disc['device']
  assert device['identifiers'] == ['ledfanatic_ledfanatic']
  for key in ('name', 'manufacturer', 'model', 'sw_version'):
    assert device[key]
  json.dumps(disc)  # must be serializable


# --- State ---

def test_state_payload_on_white():
  payload = hp.state_payload(_state(), EFFECTS)
  assert payload == {
    'state': 'ON', 'brightness': 128, 'color_mode': 'color_temp', 'color_temp': 2700,
    'color': {'r': 255, 'g': 0, 'b': 64}, 'effect': 'White',
  }


def test_state_payload_off_and_label_mapping():
  payload = hp.state_payload(_state(on=False, effect='oh_aurora', color_mode='rgb',
                                    brightness=1.0), EFFECTS)
  assert payload['state'] == 'OFF'
  assert payload['brightness'] == 255
  assert payload['effect'] == 'Aurora Glow'
  assert payload['color_mode'] == 'rgb'


# --- Commands ---

def test_parse_on_off():
  assert hp.parse_command('{"state":"ON"}', EFFECTS) == OverheadCommand(on=True)
  assert hp.parse_command(b'{"state":"OFF"}', EFFECTS) == OverheadCommand(on=False)


@pytest.mark.parametrize('raw,expected', [(255, 1.0), (0, 0.0), (102, 0.4), (999, 1.0), (-5, 0.0)])
def test_parse_brightness_scaling(raw, expected):
  cmd = hp.parse_command(json.dumps({'state': 'ON', 'brightness': raw}), EFFECTS)
  assert cmd.brightness == pytest.approx(expected)


def test_brightness_round_trip():
  for raw in (1, 64, 128, 200, 255):
    cmd = hp.parse_command(json.dumps({'brightness': raw}), EFFECTS)
    assert hp.state_payload(_state(brightness=cmd.brightness), EFFECTS)['brightness'] == raw


def test_parse_effect_label_and_name():
  assert hp.parse_command('{"effect":"Aurora Glow"}', EFFECTS).effect == 'oh_aurora'
  assert hp.parse_command('{"effect":"oh_color"}', EFFECTS).effect == 'oh_color'


def test_effect_round_trip():
  for name, label in EFFECTS:
    payload = hp.state_payload(_state(effect=name), EFFECTS)
    assert hp.parse_command(json.dumps({'effect': payload['effect']}), EFFECTS).effect == name


def test_parse_color_temp_kelvin():
  cmd = hp.parse_command('{"state":"ON","color_temp":3000}', EFFECTS)
  assert cmd.kelvin == 3000 and cmd.rgb is None


def test_parse_rgb():
  cmd = hp.parse_command('{"state":"ON","color":{"r":10,"g":20,"b":300}}', EFFECTS)
  assert cmd.rgb == (10, 20, 255)


def test_parse_ha_combined_payload():
  # HA sends effect + color + brightness together when a scene is recalled
  payload = {'state': 'ON', 'brightness': 51, 'effect': 'Color',
             'color': {'r': 0, 'g': 0, 'b': 255, 'h': 240.0, 's': 100.0}}
  cmd = hp.parse_command(json.dumps(payload), EFFECTS)
  assert cmd == OverheadCommand(on=True, brightness=pytest.approx(0.2), effect='oh_color',
                                rgb=(0, 0, 255))


def test_parse_ignores_unknown_keys():
  assert hp.parse_command('{"state":"ON","transition":2}', EFFECTS) == OverheadCommand(on=True)


@pytest.mark.parametrize('bad', [
  'not json', '', '[1,2]', '"ON"', b'\xff\xfe',
  '{"state":"MAYBE"}',
  '{"brightness":"high"}',
  '{"brightness":true}',
  '{"color":{"r":1,"g":2}}',
  '{"color":[1,2,3]}',
  '{"color_temp":"warm"}',
  '{"effect":"Disco"}',
])
def test_parse_malformed_raises_value_error(bad):
  with pytest.raises(ValueError):
    hp.parse_command(bad, EFFECTS)


# --- Bridge with a fake paho client ---

class _FakeInfo:
  def __init__(self):
    self.waited = None

  def wait_for_publish(self, timeout=None):
    self.waited = timeout


class _FakeClient:
  def __init__(self, client_id):
    self.client_id = client_id
    self.published = []   # (topic, payload, qos, retain)
    self.subscribed = []
    self.will = None
    self.calls = []
    self.lock = threading.Lock()

  def username_pw_set(self, username, password=None):
    self.calls.append(('auth', username, password))

  def will_set(self, topic, payload=None, qos=0, retain=False):
    self.will = (topic, payload, qos, retain)

  def reconnect_delay_set(self, min_delay=1, max_delay=120):
    pass

  def connect_async(self, host, port=1883, keepalive=60):
    self.calls.append(('connect_async', host, port))

  def loop_start(self):
    self.calls.append(('loop_start',))

  def loop_stop(self):
    self.calls.append(('loop_stop',))

  def disconnect(self):
    self.calls.append(('disconnect',))

  def subscribe(self, topics):
    self.subscribed.extend(topics)

  def publish(self, topic, payload=None, qos=0, retain=False):
    with self.lock:
      self.published.append((topic, payload, qos, retain))
    return _FakeInfo()

  def on_topic(self, topic):
    return [p for p in self.published if p[0] == topic]


class _ReasonCode:
  is_failure = False


class _Message:
  def __init__(self, topic, payload):
    self.topic = topic
    self.payload = payload.encode() if isinstance(payload, str) else payload


class _FakeService:
  def __init__(self):
    self.applied = []
    self.state = _state()

  def effects(self):
    return list(EFFECTS)

  def snapshot(self):
    return self.state

  async def apply(self, cmd):
    self.applied.append(cmd)
    self.state = _state(effect='oh_color', color_mode='rgb', rgb=(0, 0, 255))
    return self.state


async def _settle():
  for _ in range(5):
    await asyncio.sleep(0.01)


def _make_bridge(service, **cfg):
  clients = []

  def factory(client_id):
    clients.append(_FakeClient(client_id))
    return clients[-1]

  bridge = HAMqttBridge({**CFG, 'username': 'u', 'password': 'p', **cfg}, service,
                        client_factory=factory, poll_interval=60)
  return bridge, clients


def test_bridge_full_lifecycle():
  async def scenario():
    service = _FakeService()
    bridge, clients = _make_bridge(service)
    topic = bridge.topics
    await bridge.start()
    client = clients[0]
    assert client.will == (topic.availability, 'offline', 1, True)
    assert ('auth', 'u', 'p') in client.calls
    assert ('loop_start',) in client.calls

    # on_connect arrives from the paho thread
    await asyncio.to_thread(client.on_connect, client, None, {}, _ReasonCode(), None)
    await _settle()
    assert client.on_topic(topic.availability)[-1] == (topic.availability, 'online', 1, True)
    disc = client.on_topic(topic.config)
    assert disc and disc[-1][3] is True
    assert json.loads(disc[-1][1])['unique_id'] == 'ledfanatic_overhead'
    states = client.on_topic(topic.state)
    assert states and states[-1][3] is True
    assert json.loads(states[-1][1])['state'] == 'ON'
    assert {t for t, _ in client.subscribed} == {topic.command, topic.ha_status}

    # Command → service.apply → state published
    cmd = '{"state":"ON","effect":"Color","color":{"r":0,"g":0,"b":255}}'
    await asyncio.to_thread(client.on_message, client, None, _Message(topic.command, cmd))
    await _settle()
    assert service.applied == [OverheadCommand(on=True, effect='oh_color', rgb=(0, 0, 255))]
    latest = json.loads(client.on_topic(topic.state)[-1][1])
    assert latest['effect'] == 'Color' and latest['color'] == {'r': 0, 'g': 0, 'b': 255}

    # Bad payload is swallowed, not applied
    await asyncio.to_thread(client.on_message, client, None, _Message(topic.command, '{oops'))
    await _settle()
    assert len(service.applied) == 1

    # HA birth → discovery + state republished
    before = len(client.on_topic(topic.config))
    await asyncio.to_thread(client.on_message, client, None, _Message(topic.ha_status, 'online'))
    await _settle()
    assert len(client.on_topic(topic.config)) == before + 1

    await bridge.stop()
    assert client.published[-1] == (topic.availability, 'offline', 1, True)
    assert client.calls[-2:] == [('disconnect',), ('loop_stop',)]

  asyncio.run(scenario())


def test_bridge_poll_publishes_only_on_change():
  async def scenario():
    service = _FakeService()
    bridge, clients = _make_bridge(service)
    bridge.poll_interval = 0.01
    await bridge.start()
    client = clients[0]
    await asyncio.sleep(0.08)
    assert len(client.on_topic(bridge.topics.state)) == 1   # unchanged → no repeats
    service.state = _state(brightness=0.9)
    await asyncio.sleep(0.05)
    states = client.on_topic(bridge.topics.state)
    assert len(states) == 2 and json.loads(states[-1][1])['brightness'] == 230
    await bridge.stop()

  asyncio.run(scenario())


def test_bridge_disabled_does_nothing():
  async def scenario():
    bridge, clients = _make_bridge(_FakeService(), enabled=False)
    await bridge.start()
    await bridge.stop()
    assert clients == []

  asyncio.run(scenario())


def test_create_bridge_reads_mqtt_section_with_defaults():
  bridge = create_bridge({'mqtt': {'enabled': True, 'host': 'broker'}}, _FakeService())
  assert bridge.cfg['host'] == 'broker'
  assert bridge.cfg['port'] == 1883
  assert bridge.topics.config == 'homeassistant/light/ledfanatic/overhead/config'
  assert create_bridge({}, _FakeService()).cfg['enabled'] is False
