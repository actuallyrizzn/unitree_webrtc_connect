# LiDAR Visualization Debug Notes

## Data Flow Analysis

### Current Status (After Latest Fixes)

**Raw Data:**
- Receiving ~1.62M position values per message
- Converts to ~540k 3D points

**Processing Pipeline:**
1. **After Rotation:** 541k points ✓
2. **After Y-Filter [-1,4]:** 10k points ⚠️ (98% lost!)
3. **After unique():** 2.5k points ⚠️ (76% duplicates!)

### Fixes Applied

1. **Expanded Y-Filter:** Changed from `[-1, 4]` to `[-50, 50]` meters
   - Should capture much more of the scene
   - Can adjust based on what looks right

2. **Removed unique() deduplication:**
   - Was removing 76% of points
   - Now using all filtered points (with downsampling if >100k)

3. **Added downsampling:** If >100k points, sample every Nth point
   - Keeps performance good
   - Still shows dense point cloud

4. **Adjusted point size:** Reduced from 5.0 to 2.0
   - Better for dense clouds
   - Less overlapping

### Next Steps (When Robot is Back)

1. Check terminal output for new point counts
2. Adjust Y-filter range if needed (check Y-values in debug output)
3. May need to adjust rotation angles if orientation is wrong
4. Could add intensity-based coloring instead of distance

### Visualization Settings

- Point size: 2.0 (adjustable in `lidar_viewer.js`)
- Size attenuation: false (consistent size)
- Downsampling threshold: 100k points
- FPS target: 60

### To Adjust Filters

Edit `tmp/lidar/app.py`:
```python
minYValue = -50  # Lower bound (meters)
maxYValue = 50   # Upper bound (meters)
ROTATE_X_ANGLE = 0    # Rotation around X-axis
ROTATE_Z_ANGLE = 180  # Rotation around Z-axis
```

### Performance Notes

- 540k raw points → Should see 100-500k displayed
- Target: 60 FPS rendering
- WebSocket transmits every frame (no throttling currently)

