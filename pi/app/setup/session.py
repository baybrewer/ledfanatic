"""
Setup session service.

Manages non-destructive setup sessions: snapshot live context, stage edits,
drive setup patterns, restore on cancel, persist on commit.
"""

import copy
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from ..config.installation import (
  InstallationConfig, StripConfig, save_installation, VALID_COLOR_ORDERS,
  VALID_CHIPSETS, VALID_DIRECTIONS,
)
from ..mapping.runtime_plan import compile_output_plan, ControllerProfile

logger = logging.getLogger(__name__)


@dataclass
class SetupSnapshot:
  """Captured live context for restore on cancel."""
  current_scene: Optional[str]
  current_params: dict
  blackout: bool
  media_scene: Optional[str] = None


@dataclass
class SetupSession:
  """Active setup session with staged edits."""
  session_id: str
  snapshot: SetupSnapshot
  staged_installation: InstallationConfig
  active_pattern: Optional[dict] = None
  started_at: float = field(default_factory=time.monotonic)


class SetupSessionService:
  """Manages the lifecycle of setup sessions."""

  def __init__(self, installation: InstallationConfig, controller: ControllerProfile,
               config_dir, renderer, render_state, state_manager):
    self.installation = installation
    self.controller = controller
    self.config_dir = config_dir
    self.renderer = renderer
    self.render_state = render_state
    self.state_manager = state_manager
    self._session: Optional[SetupSession] = None

  @property
  def active_session(self) -> Optional[SetupSession]:
    return self._session

  def start_session(self) -> SetupSession:
    """Start a new setup session, snapshotting current live context."""
    if self._session is not None:
      raise ValueError("A setup session is already active")

    snapshot = SetupSnapshot(
      current_scene=self.render_state.current_scene,
      current_params=copy.deepcopy(
        self.state_manager.current_params or {}
      ),
      blackout=self.render_state.blackout,
    )

    # Deep-copy the current installation for staged edits
    staged = copy.deepcopy(self.installation)

    session = SetupSession(
      session_id=uuid.uuid4().hex[:12],
      snapshot=snapshot,
      staged_installation=staged,
    )
    self._session = session
    logger.info(f"Setup session started: {session.session_id}")
    return session

  def get_session(self) -> Optional[SetupSession]:
    return self._session

  def update_staged_installation(self, strip_updates: list[dict]) -> InstallationConfig:
    """Apply strip row updates to the staged installation."""
    if self._session is None:
      raise ValueError("No active setup session")

    staged = self._session.staged_installation
    strip_map = {s.id: s for s in staged.strips}

    for update in strip_updates:
      strip_id = update.get('id')
      if strip_id is None or strip_id not in strip_map:
        continue
      strip = strip_map[strip_id]
      for field_name in ('label', 'enabled', 'logical_order', 'output_channel',
                         'output_slot', 'direction', 'installed_led_count',
                         'color_order', 'chipset'):
        if field_name in update:
          setattr(strip, field_name, update[field_name])

    return staged

  def run_pattern(self, pattern_config: dict):
    """Drive a setup pattern through the renderer."""
    if self._session is None:
      raise ValueError("No active setup session")
    self._session.active_pattern = pattern_config
    # Pattern rendering is handled by the route layer via renderer

  def clear_pattern(self):
    """Clear the active setup pattern."""
    if self._session:
      self._session.active_pattern = None

  def cancel(self) -> SetupSnapshot:
    """Cancel the session and restore the prior live context."""
    if self._session is None:
      raise ValueError("No active setup session")

    snapshot = self._session.snapshot
    self._session = None

    # Restore live context
    self.render_state.blackout = snapshot.blackout
    if snapshot.current_scene:
      self.renderer.activate_scene(
        snapshot.current_scene,
        snapshot.current_params,
      )
    logger.info("Setup session cancelled — live context restored")
    return snapshot

  def commit(self, broadcast_state=None) -> dict:
    """Validate staged installation, persist, compile, and hot-apply."""
    if self._session is None:
      raise ValueError("No active setup session")

    staged = self._session.staged_installation
    errors = staged.validate()
    if errors:
      raise ValueError(f"Staged installation invalid: {'; '.join(errors)}")

    # Save atomically
    save_installation(staged, self.config_dir)

    # Update live installation
    self.installation.__dict__.update(staged.__dict__)

    # Compile new runtime plan and hot-swap into renderer
    new_plan = compile_output_plan(staged, self.controller)
    self.renderer.apply_output_plan(new_plan)

    # Restore live context from snapshot
    snapshot = self._session.snapshot
    self.render_state.blackout = snapshot.blackout
    if snapshot.current_scene:
      self.renderer.activate_scene(
        snapshot.current_scene,
        snapshot.current_params,
      )

    self._session = None
    logger.info("Setup session committed — installation saved and plan recompiled")

    if broadcast_state:
      import asyncio
      try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_state())
      except RuntimeError:
        pass

    return {
      'status': 'committed',
      'strips': len(staged.strips),
      'plan_channels': new_plan.channels,
      'plan_leds_per_channel': new_plan.leds_per_channel,
    }
