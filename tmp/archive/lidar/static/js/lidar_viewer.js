/**
 * Go2 LiDAR 3D Viewer
 * Real-time point cloud visualization using Three.js
 */

class LidarViewer {
    constructor() {
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.controls = null;
        this.pointCloud = null;
        this.socket = null;
        
        // Stats
        this.frameCount = 0;
        this.lastFpsUpdate = Date.now();
        this.currentFps = 0;
        
        // Point cloud settings
        this.pointSize = 4.0;  // Increased for better visibility
        this.colorScheme = 'distance'; // 'distance', 'height', 'rainbow'
        
        this.init();
    }
    
    init() {
        this.initThreeJS();
        this.initSocketIO();
        this.animate();
    }
    
    initThreeJS() {
        // Scene
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0x1a1a1a);
        this.scene.fog = new THREE.Fog(0x1a1a1a, 50, 300);
        
        // Rotate scene -90 degrees around Y axis (matches original script)
        this.scene.rotation.y = THREE.MathUtils.degToRad(-90);
        
        // Camera
        const container = document.getElementById('viewer');
        this.camera = new THREE.PerspectiveCamera(
            60,
            window.innerWidth / window.innerHeight,
            0.1,
            1000
        );
        this.camera.position.set(50, 50, 50);
        this.camera.lookAt(0, 0, 0);
        
        // Renderer
        this.renderer = new THREE.WebGLRenderer({ 
            antialias: true,
            alpha: true 
        });
        this.renderer.setSize(window.innerWidth, window.innerHeight);
        this.renderer.setPixelRatio(window.devicePixelRatio);
        container.appendChild(this.renderer.domElement);
        
        // Controls
        this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.05;
        this.controls.screenSpacePanning = true;
        this.controls.minDistance = 10;
        this.controls.maxDistance = 500;
        this.controls.maxPolarAngle = Math.PI;
        
        // Lighting
        const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
        this.scene.add(ambientLight);
        
        const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8);
        directionalLight.position.set(50, 50, 50);
        this.scene.add(directionalLight);
        
        // Grid helper
        const gridHelper = new THREE.GridHelper(100, 20, 0x00d4ff, 0x333333);
        this.scene.add(gridHelper);
        
        // Axes helper
        const axesHelper = new THREE.AxesHelper(20);
        this.scene.add(axesHelper);
        
        // Window resize handler
        window.addEventListener('resize', () => this.onWindowResize(), false);
    }
    
    initSocketIO() {
        this.socket = io();
        
        this.socket.on('connect', () => {
            console.log('Connected to server');
            this.updateStatus('connected', 'Connected - Receiving LiDAR data');
        });
        
        this.socket.on('disconnect', (reason) => {
            console.warn('Disconnected:', reason);
            this.updateStatus('disconnected', 'Disconnected: ' + reason);
        });
        
        // Listen for BINARY lidar data (faster, more efficient)
        this.socket.on('lidar_data_binary', (data) => {
            this.updatePointCloudBinary(data);
        });
    }
    
    updateStatus(state, message) {
        const statusEl = document.getElementById('status');
        statusEl.className = 'status ' + state;
        statusEl.textContent = message;
    }
    
    updatePointCloudBinary(data) {
        // Parse binary data
        const pointsBuffer = data.points;
        const distancesBuffer = data.distances;
        const metadata = data.metadata || {};
        const stats = metadata.stats || {};
        
        // Measure lag
        const now = Date.now() / 1000;
        const serverTime = metadata.timestamp || now;
        const lag = (now - serverTime) * 1000;  // Convert to ms
        
        if (!pointsBuffer || pointsBuffer.byteLength === 0) {
            console.warn('Received empty point buffer');
            return;
        }
        
        // Convert binary buffers to Float32Arrays
        const points = new Float32Array(pointsBuffer);
        const distances = new Float32Array(distancesBuffer);
        
        if (points.length === 0) {
            console.warn('Received empty point array');
            return;
        }
        
        // Log every 20 frames
        if (stats.message_count % 20 === 0) {
            const payloadKB = (pointsBuffer.byteLength + distancesBuffer.byteLength) / 1024;
            console.log(`Msg ${stats.message_count}: ${points.length / 3} pts, ${payloadKB.toFixed(1)}KB, LAG: ${lag.toFixed(0)}ms`);
        }
        
        // Remove old point cloud
        if (this.pointCloud) {
            this.scene.remove(this.pointCloud);
            this.pointCloud.geometry.dispose();
            this.pointCloud.material.dispose();
        }
        
        // Create geometry
        const geometry = new THREE.BufferGeometry();
        
        // Points are already a flat Float32Array - perfect for THREE.js
        geometry.setAttribute('position', new THREE.BufferAttribute(points, 3));
        
        // Create colors based on distance
        const colors = new Float32Array(points.length * 3);
        const maxDistance = Math.max(...distances);
        
        for (let i = 0; i < distances.length; i++) {
            const color = new THREE.Color();
            const normalized = distances[i] / maxDistance;
            
            // Color gradient from blue (close) to red (far) with brighter colors
            color.setHSL(0.6 - normalized * 0.6, 1.0, 0.7);  // Increased lightness from 0.5 to 0.7
            
            colors[i * 3] = color.r;
            colors[i * 3 + 1] = color.g;
            colors[i * 3 + 2] = color.b;
        }
        
        geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
        
        // Compute bounding box for camera adjustment
        geometry.computeBoundingBox();
        
        // Create material with better visibility settings
        const material = new THREE.PointsMaterial({
            size: this.pointSize,
            vertexColors: true,
            sizeAttenuation: false,  // Changed to false for consistent point size
            transparent: false,
            opacity: 1.0,
            depthWrite: true,
            depthTest: true
        });
        
        // Create point cloud
        this.pointCloud = new THREE.Points(geometry, material);
        this.scene.add(this.pointCloud);
        
        // Update stats
        document.getElementById('stat-messages').textContent = stats.message_count || 0;
        document.getElementById('stat-points').textContent = stats.after_filter || 0;
        
        // Auto-frame on first data
        if (stats.message_count === 1) {
            this.framePointCloud();
        }
    }
    
    framePointCloud() {
        if (!this.pointCloud || !this.pointCloud.geometry.boundingBox) {
            return;
        }
        
        const box = this.pointCloud.geometry.boundingBox;
        const center = new THREE.Vector3();
        const size = new THREE.Vector3();
        
        box.getCenter(center);
        box.getSize(size);
        
        const maxDim = Math.max(size.x, size.y, size.z);
        const fov = this.camera.fov * (Math.PI / 180);
        let cameraDistance = Math.abs(maxDim / Math.sin(fov / 2));
        
        cameraDistance *= 1.5; // Add some padding
        
        const direction = new THREE.Vector3(1, 1, 1).normalize();
        const newPosition = center.clone().add(direction.multiplyScalar(cameraDistance));
        
        this.camera.position.copy(newPosition);
        this.camera.lookAt(center);
        this.controls.target.copy(center);
        this.controls.update();
    }
    
    animate() {
        requestAnimationFrame(() => this.animate());
        
        this.controls.update();
        this.renderer.render(this.scene, this.camera);
        
        // Update FPS
        this.frameCount++;
        const now = Date.now();
        if (now - this.lastFpsUpdate >= 1000) {
            this.currentFps = this.frameCount;
            document.getElementById('stat-fps').textContent = this.currentFps;
            this.frameCount = 0;
            this.lastFpsUpdate = now;
        }
    }
    
    onWindowResize() {
        this.camera.aspect = window.innerWidth / window.innerHeight;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(window.innerWidth, window.innerHeight);
    }
}

// Initialize viewer when page loads
document.addEventListener('DOMContentLoaded', () => {
    window.lidarViewer = new LidarViewer();
});

