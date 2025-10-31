## Branches and Active Forks Map

This document summarizes upstream branches and actively maintained forks of `legion1581/unitree_webrtc_connect`, to guide what we might fold back into our branch.

### Upstream baseline
- **Upstream repo**: `legion1581/unitree_webrtc_connect` — `https://github.com/legion1581/unitree_webrtc_connect`
- **Default branch**: `master`
- **Upstream master head**: 2025-06-02T12:19:16Z
- **Upstream last push (repo)**: 2025-10-06T14:54:48Z
- **Other upstream branch with newer activity**: `2.x.x` (head: 2025-10-06T14:54:36Z)
  - Link: `https://github.com/legion1581/unitree_webrtc_connect/tree/2.x.x`
  - Note: More active than `master`; consider diffing for breaking changes or improvements when aligning.

---

### More active forks (summary)

Below, “ahead/behind” is relative to upstream `master` using GitHub compare. Each entry includes links and notable changes.

#### 1) `juhasch/go2_webrtc_connect`
- Repo: `https://github.com/juhasch/go2_webrtc_connect`
- Compare: `https://github.com/legion1581/unitree_webrtc_connect/compare/master...juhasch:master`
- Status: **+51 / -0** (commits ahead/behind), ~65 files changed
- Highlights:
  - Adds higher-level helpers and apps:
    - `go2_webrtc_driver/robot_helper.py`
    - `apps/gesture/hand_gestures.py`
    - `apps/rerun/rerun_video_lidar_stream.py`
    - `go2_webrtc_driver/lidar/point_cloud_accumulator.py`
  - Significant updates:
    - `go2_webrtc_driver/webrtc_driver.py`
    - `go2_webrtc_driver/webrtc_audiohub.py`
    - `go2_webrtc_driver/msgs/rtc_inner_req.py`
    - `examples/data_channel/lowstate/lowstate.py` (and other examples)
  - Recent commit themes: cleanup, app timeout fix, new examples (e.g., TrajectoryFollow), added `go2action` CLI.
  - Relevance: richer examples, gesture control, LiDAR visualization/accumulation, audiohub/driver robustness.

#### 2) `ricoai/go2_webrtc_connect`
- Repo: `https://github.com/ricoai/go2_webrtc_connect`
- Compare: `https://github.com/legion1581/unitree_webrtc_connect/compare/master...ricoai:master`
- Status: **+4 / -0**, ~12 files changed
- Highlights:
  - Adds object detection video examples:
    - `examples/video/yolo11/display_video_channel_yolo11.py`
    - `examples/video/rfdetr/display_video_channel_rfdetr.py`
    - `examples/video/rfdetr/requirements.txt`
  - Minor tweaks to examples, `.gitignore`, and `README.md`
  - Relevance: turnkey YOLO11 and RFDETR integration examples.

#### 3) `skooter500/unitree_webrtc_connect`
- Repo: `https://github.com/skooter500/unitree_webrtc_connect`
- Compare: `https://github.com/legion1581/unitree_webrtc_connect/compare/master...skooter500:master`
- Status: **+4 / -0**, ~4 files changed
- Highlights:
  - Adds large `robot_controller.py` (GUI/controller scaffold)
  - Small tweaks to example scripts (`lowstate.py`, `lidar_stream.py`, `sportmode.py`)
  - Relevance: GUI/interactive controller foundation.

#### 4) `dimensionalOS/go2_webrtc_connect`
- Repo: `https://github.com/dimensionalOS/go2_webrtc_connect`
- Compare: `https://github.com/legion1581/unitree_webrtc_connect/compare/master...dimensionalOS:master`
- Status: **+6 / -0**, ~6 files changed
- Highlights:
  - Packaging and build workflow improvements:
    - Adds `pyproject.toml`
    - Adjusts `setup.py`
    - Package discovery cleanups (`__init__.py` changes, added package `__init__`s)
    - Switches `aioice` submodule → direct dependency
  - Relevance: easier builds/publishing, cleaner packaging.

#### 5) `Sonam-22/go2_webrtc_connect`
- Repo: `https://github.com/Sonam-22/go2_webrtc_connect`
- Compare: `https://github.com/legion1581/unitree_webrtc_connect/compare/master...Sonam-22:master`
- Status: **+8 / -0**, ~5 files changed
- Highlights:
  - Robustness: error handling updates and sync command support
  - Files touched: `go2_webrtc_driver/msgs/pub_sub.py`, `webrtc_driver.py`, `webrtc_datachannel.py`, `setup.py`
  - Adds `examples/validations/validate-aiortc.py`
  - Relevance: stability and validation utilities.

#### 6) `00make/go2_webrtc_connect`
- Repo: `https://github.com/00make/go2_webrtc_connect`
- Compare: `https://github.com/legion1581/unitree_webrtc_connect/compare/master...00make:master`
- Status: **+5 / -0**, ~20 files changed
- Highlights:
  - Example set cleanup and consolidation
  - Adds `examples/sportmode.py`
  - Removes deprecated example scripts and old images; README updates
  - Relevance: slimmer, clearer examples.

#### 7) `gomtam/go2_webrtc_connect`
- Repo: `https://github.com/gomtam/go2_webrtc_connect`
- Compare: `https://github.com/legion1581/unitree_webrtc_connect/compare/master...gomtam:master`
- Status: **+3 / -0**, ~5 files changed
- Highlights:
  - Adds `examples/data_channel/sportmode/sportmode_debug.py`
  - Minor example and data channel tweaks (`webrtc_datachannel.py`)
  - Relevance: sport mode debugging.

#### 8) `lturr07/unitree_webrtc_connect`
- Repo: `https://github.com/lturr07/unitree_webrtc_connect`
- Compare: `https://github.com/legion1581/unitree_webrtc_connect/compare/master...lturr07:master`
- Status: **+1 / -0**
- Highlights: README improvements.

#### 9) `jkutia/unitree_webrtc_connect`
- Repo: `https://github.com/jkutia/unitree_webrtc_connect`
- Compare: `https://github.com/legion1581/unitree_webrtc_connect/compare/master...jkutia:master`
- Status: **+1 / -0**
- Highlights: Adds `LICENSE` (MIT).

#### 10) `actuallyrizzn/unitree_webrtc_connect`
- Repo: `https://github.com/actuallyrizzn/unitree_webrtc_connect`
- Compare: `https://github.com/legion1581/unitree_webrtc_connect/compare/master...actuallyrizzn:master`
- Status: **+3 / -0**, ~32 files changed
- Highlights:
  - Adds extensive documentation under `docs/`
  - Adds temporary test scripts under `tmp/` (e.g., `test_wave.py`, `test_simple.py`, `test_lowstate.py`, `test_connection.py`)
  - Relevance: developer docs; local validation scripts.

#### 11) `JackWPP/unitree_webrtc_connect`
- Repo: `https://github.com/JackWPP/unitree_webrtc_connect`
- Compare: `https://github.com/legion1581/unitree_webrtc_connect/compare/master...JackWPP:master`
- Status: no diff vs upstream.

#### 12) `megamass-VIRNECT/go2_webrtc_connect`
- Repo: `https://github.com/megamass-VIRNECT/go2_webrtc_connect`
- Compare: `https://github.com/legion1581/unitree_webrtc_connect/compare/master...megamass-VIRNECT:master`
- Status: behind upstream (no new default-branch commits).

---

### Recommended intake order (high-level)
- Core functionality and robustness: `juhasch` (helpers, LiDAR, audiohub/driver), `Sonam-22` (error handling), `dimensionalOS` (packaging)
- Examples and tooling: `ricoai` (YOLO/RFDETR), `skooter500` (GUI controller), `00make` (cleanup), `gomtam` (debug)
- Docs and meta: `actuallyrizzn`, `lturr07`, `jkutia`

### Notes
- All ahead/behind counts and file highlights derive from GitHub’s compare API at the time of analysis.
- For each fork above, use the provided compare link for a complete diff before merging.


