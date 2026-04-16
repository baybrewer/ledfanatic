"""Tests for strip mapping data model and migration."""

import pytest
import tempfile
from pathlib import Path

from app.config.installation import (
  StripMapping, StripInstallation, VALID_COLOR_ORDERS, VALID_DIRECTIONS,
  synthesize_default_strips, load_installation, save_installation,
  migrate_v1_to_strips, migrate_v2_to_strips,
)


class TestStripMapping:
  def test_default_strips(self):
    inst = synthesize_default_strips()
    assert len(inst.strips) == 10
    for i, s in enumerate(inst.strips):
      assert s.channel == i // 2
      assert s.offset == (i % 2) * 172
      assert s.led_count == 172
      assert s.color_order == 'BGR'
    assert inst.strips[0].direction == 'bottom_to_top'
    assert inst.strips[1].direction == 'top_to_bottom'

  def test_validate_valid(self):
    inst = synthesize_default_strips()
    assert inst.validate() == []

  def test_validate_bad_color_order(self):
    inst = synthesize_default_strips()
    inst.strips[0].color_order = 'XYZ'
    errors = inst.validate()
    assert any('color_order' in e for e in errors)

  def test_validate_led_count_zero(self):
    inst = synthesize_default_strips()
    inst.strips[0].led_count = 0
    errors = inst.validate()
    assert any('led_count' in e for e in errors)

  def test_validate_overlap(self):
    inst = synthesize_default_strips()
    inst.strips[2].channel = 0
    inst.strips[2].offset = 0
    errors = inst.validate()
    assert any('overlap' in e.lower() for e in errors)

  def test_validate_exceeds_channel(self):
    inst = synthesize_default_strips()
    inst.strips[0].offset = 1000
    inst.strips[0].led_count = 200
    errors = inst.validate()
    assert any('exceed' in e.lower() or '1100' in e for e in errors)


class TestMigration:
  def test_migrate_v1(self):
    old_data = {
      'schema_version': 1,
      'strips': [
        {'id': 0, 'output_channel': 0, 'output_slot': 0, 'installed_led_count': 172,
         'color_order': 'BGR', 'direction': 'bottom_to_top', 'enabled': True},
        {'id': 1, 'output_channel': 0, 'output_slot': 1, 'installed_led_count': 172,
         'color_order': 'BGR', 'direction': 'top_to_bottom', 'enabled': True},
      ],
    }
    inst = migrate_v1_to_strips(old_data)
    assert inst.schema_version == 3
    assert len(inst.strips) == 2
    assert inst.strips[0].channel == 0
    assert inst.strips[0].offset == 0
    assert inst.strips[1].channel == 0
    assert inst.strips[1].offset == 172

  def test_migrate_v2(self):
    old_data = {
      'schema_version': 2,
      'channels': [
        {'channel': 0, 'color_order': 'GRB', 'led_count': 344},
        {'channel': 1, 'color_order': 'BGR', 'led_count': 344},
        {'channel': 2, 'color_order': 'BGR', 'led_count': 0},
      ],
    }
    inst = migrate_v2_to_strips(old_data)
    assert inst.schema_version == 3
    assert len(inst.strips) == 4
    assert inst.strips[0].channel == 0
    assert inst.strips[0].color_order == 'GRB'
    assert inst.strips[1].channel == 0
    assert inst.strips[1].offset == 172


class TestPersistence:
  def test_save_and_load(self):
    with tempfile.TemporaryDirectory() as tmp:
      config_dir = Path(tmp)
      inst = synthesize_default_strips()
      inst.strips[0].color_order = 'RGB'
      inst.strips[0].led_count = 100
      save_installation(inst, config_dir)

      loaded = load_installation(config_dir)
      assert loaded.schema_version == 3
      assert loaded.strips[0].color_order == 'RGB'
      assert loaded.strips[0].led_count == 100
      assert len(loaded.strips) == 10
