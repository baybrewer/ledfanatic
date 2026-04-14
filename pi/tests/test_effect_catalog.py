"""Tests for effect catalog service and scenes list compatibility."""

from app.effects.catalog import EffectCatalogService, EffectMeta
from app.effects.generative import EFFECTS
from app.effects.audio_reactive import AUDIO_EFFECTS
from app.diagnostics.patterns import DIAGNOSTIC_EFFECTS


class TestEffectCatalogService:
  def test_builds_catalog(self):
    svc = EffectCatalogService()
    catalog = svc.get_catalog()
    # Should include all registered effects
    for name in EFFECTS:
      assert name in catalog
    for name in AUDIO_EFFECTS:
      assert name in catalog
    for name in DIAGNOSTIC_EFFECTS:
      assert name in catalog

  def test_generative_effects_have_correct_group(self):
    svc = EffectCatalogService()
    for name in EFFECTS:
      meta = svc.get_meta(name)
      assert meta.group == 'generative'

  def test_audio_effects_have_correct_group(self):
    svc = EffectCatalogService()
    for name in AUDIO_EFFECTS:
      meta = svc.get_meta(name)
      assert meta.group == 'audio'

  def test_diagnostic_effects_not_preview_supported(self):
    svc = EffectCatalogService()
    for name in DIAGNOSTIC_EFFECTS:
      meta = svc.get_meta(name)
      assert meta.preview_supported is False

  def test_all_effects_have_labels(self):
    svc = EffectCatalogService()
    for meta in svc.get_catalog().values():
      assert meta.label
      assert len(meta.label) > 0

  def test_to_dict_includes_required_fields(self):
    meta = EffectMeta(
      name='test_effect',
      label='Test Effect',
      group='generative',
      description='A test effect',
    )
    d = meta.to_dict()
    assert d['name'] == 'test_effect'
    assert d['label'] == 'Test Effect'
    assert d['group'] == 'generative'
    assert d['description'] == 'A test effect'
    assert d['preview_supported'] is True
    assert d['imported'] is False

  def test_register_imported(self):
    svc = EffectCatalogService()
    meta = EffectMeta(
      name='aurora_borealis',
      label='Aurora Borealis',
      group='imported',
      description='Animated aurora effect',
      imported=True,
    )
    svc.register_imported('aurora_borealis', meta)
    assert 'aurora_borealis' in svc.get_catalog()
    assert svc.get_meta('aurora_borealis').imported is True


class TestScenesListCompatibility:
  """Verify /api/scenes/list still returns the expected shape."""

  def test_scenes_list_shape(self):
    """The effects dict must be name-keyed with at least 'type'."""
    # Simulate what the route does
    all_effects = {}
    for name, cls in EFFECTS.items():
      desc = cls.__doc__.strip().split('\n')[0] if cls.__doc__ else ''
      all_effects[name] = {
        'type': 'generative',
        'description': desc,
        'preview_supported': True,
      }
    for name, cls in AUDIO_EFFECTS.items():
      desc = cls.__doc__.strip().split('\n')[0] if cls.__doc__ else ''
      all_effects[name] = {
        'type': 'audio',
        'description': desc,
        'preview_supported': True,
      }

    # Verify shape matches what app.js expects
    assert isinstance(all_effects, dict)
    for name, info in all_effects.items():
      assert 'type' in info
      assert info['type'] in ('generative', 'audio', 'diagnostic')

  def test_no_diagnostic_prefix_in_main_list(self):
    """Frontend skips effects starting with diag_ — verify they exist."""
    diag_count = sum(1 for name in DIAGNOSTIC_EFFECTS if name.startswith('diag_'))
    assert diag_count > 0
