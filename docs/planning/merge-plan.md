## Merge Plan (Candidate)

Goal: Integrate useful improvements from active forks into our branch with minimal risk. This plan groups intake by theme, outlines concrete actions, flags expected conflicts, and defines validation steps.

### Baseline
- Upstream: `legion1581/unitree_webrtc_connect@master`
- Our fork: `actuallyrizzn/unitree_webrtc_connect@master`
- Reference map: `docs/planning/branches.md`

---

### Phase 1 — Packaging, Stability, and Infra (Low Risk)
1. dimensionalOS (packaging)
   - Intake: `pyproject.toml`, `setup.py` edits, added package `__init__` files, switch `aioice` to direct dependency.
   - Actions:
     - Diff and cherry-pick packaging commits.
     - Update `requirements.txt`/dependency pins to remain consistent.
   - Risks/Conflicts: minimal; verify install/build.
   - Validation: build wheel, pip install in clean venv, run basic examples.

2. Sonam-22 (robustness)
   - Intake: error handling improvements in `pub_sub.py`, `webrtc_driver.py`, `webrtc_datachannel.py`; sync command; validation script.
   - Actions:
     - Cherry-pick targeted error handling commits.
     - Add `examples/validations/validate-aiortc.py` under `examples/validations/`.
   - Risks: subtle behavior changes in message flow.
   - Validation: run validation script; stress test data channel and reconnect logic.

---

### Phase 2 — Core Features and Helpers (Moderate Risk, High Value)
3. juhasch (helpers, LiDAR, apps)
   - Intake: `robot_helper.py`, `point_cloud_accumulator.py`, gesture and rerun apps; substantial updates to `webrtc_driver.py`, `webrtc_audiohub.py`, `msgs/rtc_inner_req.py`, and examples.
   - Actions:
     - Stage A: Add new modules (helpers, accumulator, apps) without changing existing APIs.
     - Stage B: Incrementally merge driver/audiohub/RTC message changes; resolve API diffs.
   - Risks: breaking API changes; behavior shifts; example drift.
   - Validation: unit/integration runs for LiDAR streaming, audio/video sync; test `apps/*` and `examples/*` end-to-end.

4. skooter500 (GUI/Controller)
   - Intake: `robot_controller.py` GUI controller scaffold.
   - Actions:
     - Add as optional app; do not wire into core.
   - Risks: dependency bloat if GUI libs are added; keep optional.
   - Validation: launch controller against a test device.

---

### Phase 3 — Examples and Tooling (Low-to-Moderate Risk)
5. ricoai (vision examples)
   - Intake: YOLO11 and RFDETR example scripts + minimal req files.
   - Actions:
     - Place under `examples/video/...`; include separate `requirements.txt` per example.
   - Risks: GPU/CPU env variance; keep isolated.
   - Validation: run examples with sample streams; ensure no core coupling.

6. 00make (example cleanup)
   - Intake: consolidated `examples/sportmode.py`; removal of deprecated examples.
   - Actions:
     - Add the consolidated script; keep deprecated examples for now but mark as legacy until parity confirmed.
   - Risks: accidental loss of useful snippets if removed prematurely.
   - Validation: run sport mode paths; confirm feature parity with legacy examples.

7. gomtam (sport mode debug)
   - Intake: `sportmode_debug.py` and minor tweaks.
   - Actions:
     - Add debug script; consider merging tiny fixes to core only if aligned.
   - Risks: none significant.
   - Validation: execute debug flow on a device.

---

### Phase 4 — Docs and Meta (No Risk)
8. lturr07 (README), jkutia (LICENSE), actuallyrizzn (docs)
   - Actions:
     - Merge README improvements; ensure LICENSE alignment with our repo policy.
     - Keep our extended docs; re-check links.

---

### Cross-cutting Steps
- Create topic branches per intake unit (e.g., `intake/pkg-dimensionalOS`, `intake/core-juhasch-A`, ...).
- Use `git cherry-pick -x <commit>` when possible to preserve provenance.
- If cherry-pick conflicts are heavy, `git subtree` or selective file-porting is acceptable with clear commit messages.
- Keep compare links handy for each fork (see `branches.md`).

---

### Validation Matrix
- Connectivity: signaling handshake, STUN/ICE flow, reconnect.
- Media: audio/video sync, latency checks, basic playback.
- Data channel: throughput, ordering, backpressure, error handling.
- LiDAR: decode performance, accumulation correctness, visualization.
- Apps: gesture control, rerun visualization, controller GUI.
- Packaging: sdist/wheel build, clean install, import graph sanity.

---

### Execution Checklist (initial)
- [ ] Create topic branches for each phase/unit.
- [ ] Port dimensionalOS packaging; build/install smoke tests.
- [ ] Port Sonam-22 robustness; run validation and stress tests.
- [ ] Add juhasch new modules (Stage A); verify no regressions.
- [ ] Incrementally merge juhasch core edits (Stage B); run full matrix.
- [ ] Add skooter500 GUI controller (optional path).
- [ ] Add ricoai YOLO/RFDETR examples; isolate deps.
- [ ] Add 00make consolidated sportmode; retain legacy until parity.
- [ ] Add gomtam debug script.
- [ ] Merge doc/meta updates.

---

### Links
- Upstream: `https://github.com/legion1581/unitree_webrtc_connect`
- Branch map: `docs/planning/branches.md`


