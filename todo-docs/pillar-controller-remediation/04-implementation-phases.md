# Implementation Phases

## Phase A: Foundation fixes (no new features)

### A1: Repo hygiene & secrets cleanup
- [ ] Remove tracked venv/cache artifacts
- [ ] Tighten .gitignore
- [ ] Rename system.yaml → system.yaml.example with placeholders
- [ ] Add system.yaml to .gitignore
- [ ] Update setup docs for secret provisioning

### A2: Fix deployment/packaging
- [ ] Fix pyproject.toml entrypoint
- [ ] Fix systemd service ExecStart and WorkingDirectory
- [ ] Fix setup.sh to install from pyproject.toml
- [ ] Fix deploy.sh to sync and pip install
- [ ] Remove hardcoded /opt/pillar from state.py

### A3: Fix protocol issues
- [ ] Fix stats parser threshold (32 → 28)
- [ ] Add STATS_PAYLOAD_SIZE constant
- [ ] Fix blackout from toggle to explicit (Pi side)
- [ ] Fix blackout from toggle to explicit (Teensy side)
- [ ] Fix COBS decoder _pending_zero reset

### A4: Fix media metadata
- [ ] Fix metadata dict key: 'type' → 'media_type'
- [ ] Fix scan_library to use 'media_type' key
- [ ] Make media dimensions config-driven
- [ ] Fix MediaItem to_dict mapping

### A5: Fix metrics
- [ ] Separate frames_rendered vs frames_sent
- [ ] Only increment frames_sent on successful transport
- [ ] Update RenderState.to_dict() with corrected names

### A6: Fix thread safety
- [ ] Add threading.Lock to audio analyzer
- [ ] Write snapshot dict under lock
- [ ] Read snapshot in renderer under lock (or atomic copy)

### A7: Fix persistence
- [ ] Add mark_dirty() / flush() pattern
- [ ] Remove per-setter save() calls
- [ ] Add periodic flush task
- [ ] Add force_save() for shutdown

### A8: Fix shutdown lifecycle
- [ ] Track background tasks in main.py
- [ ] Add shutdown event handler
- [ ] Cancel render loop
- [ ] Cancel reconnect loop
- [ ] Stop audio analyzer
- [ ] Close transport
- [ ] Force save state

**SELF-REVIEW CHECKPOINT A**: Verify all existing tests still pass.
Continue automatically.

## Phase B: Auth + security

### B1: Create auth module
- [ ] Create pi/app/api/auth.py
- [ ] Implement require_auth dependency
- [ ] Read token from config

### B2: Apply auth to endpoints
- [ ] Add Depends(require_auth) to all protected endpoints
- [ ] Add token to WebSocket handshake
- [ ] Fix os.system → subprocess for reboot/restart

### B3: Add auth tests
- [ ] Test authorized request succeeds
- [ ] Test unauthorized request returns 401
- [ ] Test missing token returns 401
- [ ] Test invalid token returns 401

**SELF-REVIEW CHECKPOINT B**: Run auth tests. Continue automatically.

## Phase C: Brightness + solar automation

### C1: Create brightness engine
- [ ] Create pi/app/core/brightness.py
- [ ] Implement BrightnessEngine class
- [ ] Implement five-phase solar model
- [ ] Add astral dependency to pyproject.toml

### C2: Integrate brightness engine
- [ ] Wire into renderer (replace direct state.brightness usage)
- [ ] Add brightness config to system.yaml.example
- [ ] Add location/timezone to hardware.yaml
- [ ] Add brightness API endpoints
- [ ] Update state manager for brightness persistence

### C3: Add brightness UI
- [ ] Add brightness section to index.html
- [ ] Add manual slider, auto toggle, timezone selector
- [ ] Add effective brightness display
- [ ] Wire JS to new API endpoints

### C4: Add brightness tests
- [ ] Test manual mode
- [ ] Test auto mode at different times
- [ ] Test phase transitions
- [ ] Test timezone handling
- [ ] Test fallback on calc failure

**SELF-REVIEW CHECKPOINT C**: Run brightness tests. Continue automatically.

## Phase D: Upload safety + production config

### D1: Fix upload handling
- [ ] Add request body size limit
- [ ] Stream uploads to temp file (not memory)
- [ ] Validate extension and content-type
- [ ] Add upload limit config

### D2: Fix production config
- [ ] Add PILLAR_DEV env var detection
- [ ] Use correct port based on mode
- [ ] Ensure config paths work in both modes

### D3: Clean dependencies
- [ ] Remove duplicated pip install list from setup.sh
- [ ] Reference pyproject.toml as SSOT for deps
- [ ] Add astral to dependencies

### D4: Add upload tests
- [ ] Test valid upload accepted
- [ ] Test oversized upload rejected
- [ ] Test invalid type rejected

**SELF-REVIEW CHECKPOINT D**: Run all tests. Continue automatically.

## Phase E: UI updates + final integration

### E1: Update frontend
- [ ] Add auth token input/storage
- [ ] Add Authorization header to API calls
- [ ] Update brightness controls
- [ ] Update blackout to explicit on/off
- [ ] Show effective brightness

### E2: Update CLAUDE.md
- [ ] Document new modules
- [ ] Document auth setup
- [ ] Document brightness config

### E3: Final test pass
- [ ] Run all tests
- [ ] Fix any failures
- [ ] Document untestable items

## Dependencies

```
A1 → A2 → A3 (protocol must work before features)
A4, A5, A6, A7 (independent, can be done in any order after A2)
A8 depends on A2 (lifecycle needs correct module layout)
B depends on A (auth needs working API)
C depends on A + B (brightness needs working renderer + auth)
D depends on A (upload needs working API)
E depends on all above
```
