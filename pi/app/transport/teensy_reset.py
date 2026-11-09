"""
Remote Teensy reboot — recovers a wedged Teensy without physical access.

Opening the port at 134 baud is handled by the Teensy USB core itself (not the
sketch), so it works even when the firmware has stopped reading serial. That
drops the Teensy into the HalfKay bootloader; `teensy_loader_cli -b` then
boots the already-flashed firmware without reprogramming it.

Needs the HalfKay USB device (16c0:0478) to be accessible to the service user
— see pi/config/udev/49-teensy.rules (installed by setup.sh).
"""

import asyncio
import logging
import shutil

import serial

logger = logging.getLogger(__name__)

BOOTLOADER_BAUD = 134


async def reboot_teensy(port: str, loader: str = 'teensy_loader_cli',
                        mcu: str = 'TEENSY41', timeout: float = 20.0) -> bool:
  """Reboot the Teensy on `port`. Returns True if the loader reported a boot."""
  # Never drop into HalfKay without a way back out
  if not _loader_path(loader):
    return False
  try:
    await asyncio.to_thread(_touch_bootloader_baud, port)
  except (serial.SerialException, OSError) as e:
    logger.error(f"Teensy reboot: could not open {port} at {BOOTLOADER_BAUD} baud: {e}")
    return False
  return await boot_from_bootloader(loader, mcu, timeout)


async def boot_from_bootloader(loader: str = 'teensy_loader_cli',
                               mcu: str = 'TEENSY41', timeout: float = 20.0) -> bool:
  """Boot a Teensy already sitting in HalfKay (no serial port exists then)."""
  loader_path = _loader_path(loader)
  if not loader_path:
    return False

  proc = await asyncio.create_subprocess_exec(
    loader_path, f'--mcu={mcu}', '-w', '-b',
    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
  )
  try:
    output, _ = await asyncio.wait_for(proc.communicate(), timeout)
  except asyncio.TimeoutError:
    await kill_and_reap(proc)
    logger.error("Teensy reboot: loader timed out waiting for bootloader")
    return False
  except BaseException:
    # Cancellation (shutdown) or any other exit: a loader left running would
    # act on whatever Teensy enumerates next, long after we reported failure
    await kill_and_reap(proc)
    raise

  text = output.decode(errors='replace').strip()
  if proc.returncode != 0 or 'claim interface' in text:
    logger.error(f"Teensy reboot failed (rc={proc.returncode}): {text[-200:]}")
    return False
  logger.warning("Teensy rebooted via bootloader")
  return True


async def kill_and_reap(proc) -> None:
  """Kill a still-running child and wait for it, so no zombie or orphan
  survives the caller's exit path. Safe on a process that already exited."""
  if proc.returncode is None:
    try:
      proc.kill()
    except ProcessLookupError:
      pass
  await proc.wait()


def _loader_path(loader: str):
  path = shutil.which(loader)
  if not path:
    logger.error(f"Teensy reboot unavailable: {loader} not installed")
  return path


def _touch_bootloader_baud(port: str):
  with serial.Serial(port, baudrate=BOOTLOADER_BAUD):
    pass
