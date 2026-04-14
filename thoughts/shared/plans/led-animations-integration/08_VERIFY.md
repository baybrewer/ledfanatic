# Phase 8 — Integration Test, Deploy, Verify

## Pre-deploy checklist

### Python
- [ ] `python -m compileall pi/app` — no syntax errors
- [ ] `PYTHONPATH=. pytest tests/ -q` — all tests pass (expect only the pre-existing migration test failure)
- [ ] No Pygame imports in any production code
- [ ] All 27 animations registered in effect catalog
- [ ] Animation Switcher registered as special effect
- [ ] All existing effects still work

### UI
- [ ] All 7 tabs load without JS errors
- [ ] Help panels expand/collapse on every tab
- [ ] Effects tab shows categorized effects with controls
- [ ] Sim tab renders pixel-dot preview
- [ ] Setup fields are editable during active session
- [ ] Blackout/Resume work
- [ ] WebSocket connects and shows FPS

### Integration
- [ ] Effect activation via API works for all 27 imported effects
- [ ] Speed slider changes take effect immediately
- [ ] Palette switching works for palette-capable effects
- [ ] Animation Switcher cycles through selected effects
- [ ] Cross-fade transition is smooth (no flicker)
- [ ] Preview runs independently of live output
- [ ] Setup commit hot-applies color order changes to live render
- [ ] Cancel restores previous scene (including media scenes)

### Deploy
1. Push to GitHub: `git push origin main`
2. Rsync to Pi: `rsync --rsync-path="sudo rsync" pi/ jim@ledfanatic.local:/opt/pillar/src/`
3. Fix ownership: `sudo chown -R pillar:pillar /opt/pillar/src/`
4. Restart: `sudo systemctl restart pillar`
5. Check logs: `sudo journalctl -u pillar -n 20`
6. Verify UI: open http://ledfanatic.local

### Post-deploy verification
- [ ] Open each tab, verify no errors in browser console
- [ ] Activate 3 different imported effects
- [ ] Adjust speed slider on one, verify LED output changes
- [ ] Switch palette on one, verify color change
- [ ] Start Animation Switcher with 4 effects, verify cycling
- [ ] Open Sim tab, verify pixel preview matches live output
- [ ] Start a setup session, edit one strip's color order, commit, verify live output correct
- [ ] Cancel a setup session, verify scene restores

## Rollback plan

If something breaks:
1. `git revert HEAD` and re-deploy
2. Or: `ssh jim@ledfanatic.local "sudo -u pillar git -C /opt/pillar/src checkout HEAD~1 && sudo systemctl restart pillar"`

## Definition of done

The work is complete when:
- 27 imported animations render on the physical LEDs
- Every animation has speed and palette controls in the UI
- Animation Switcher cross-fades between selected effects
- Sim tab shows pixel-dot preview matching physical layout
- Setup strip table is fully editable with validation
- Every page has help instructions
- All tests pass
- Deployed and verified on ledfanatic.local
