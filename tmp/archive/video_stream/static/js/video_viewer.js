// Go2 Video Stream Viewer
// Handles WebSocket connection and video frame display

class VideoViewer {
    constructor() {
        this.socket = null;
        this.canvas = null;
        this.ctx = null;
        this.statsInterval = null;
        this.isConnected = false;

        this.init();
    }

    init() {
        this.canvas = document.getElementById('video-canvas');
        this.ctx = this.canvas.getContext('2d');

        // Set up Socket.IO connection
        this.socket = io();

        this.socket.on('connect', () => {
            console.log('Connected to server');
            this.isConnected = true;
            this.hideNoVideoMessage();
        });

        this.socket.on('disconnect', () => {
            console.log('Disconnected from server');
            this.isConnected = false;
            this.showNoVideoMessage();
        });

        // Handle video frames
        this.socket.on('video_frame', (data) => {
            this.displayFrame(data.data);
        });

        // Handle stats updates
        this.socket.on('stats', (stats) => {
            this.updateStats(stats);
        });

        // Start requesting stats
        this.startStatsUpdates();

        console.log('Video viewer initialized');
    }

    displayFrame(base64Data) {
        if (!this.ctx) return;

        try {
            // Create image from base64 data
            const img = new Image();

            img.onload = () => {
                // Clear canvas
                this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

                // Calculate scaling to fit canvas while maintaining aspect ratio
                const scaleX = this.canvas.width / img.width;
                const scaleY = this.canvas.height / img.height;
                const scale = Math.min(scaleX, scaleY);

                const scaledWidth = img.width * scale;
                const scaledHeight = img.height * scale;

                const x = (this.canvas.width - scaledWidth) / 2;
                const y = (this.canvas.height - scaledHeight) / 2;

                // Draw the image
                this.ctx.drawImage(img, x, y, scaledWidth, scaledHeight);

                // Hide "no video" message
                this.hideNoVideoMessage();
            };

            img.onerror = (error) => {
                console.error('Error loading video frame:', error);
            };

            // Set image source
            img.src = 'data:image/jpeg;base64,' + base64Data;

        } catch (error) {
            console.error('Error displaying video frame:', error);
        }
    }

    updateStats(stats) {
        // Update connection status
        const stateElement = document.getElementById('connection-state');
        if (stateElement) {
            stateElement.textContent = stats.connection_state;
            stateElement.className = 'value ' + (stats.connection_state === 'connected' ? 'connected' : 'disconnected');
        }

        // Update uptime
        const uptimeElement = document.getElementById('uptime');
        if (uptimeElement) {
            uptimeElement.textContent = this.formatTime(stats.uptime);
        }

        // Update frame stats
        const framesReceivedElement = document.getElementById('frames-received');
        if (framesReceivedElement) {
            framesReceivedElement.textContent = stats.frames_received.toLocaleString();
        }

        const framesSentElement = document.getElementById('frames-sent');
        if (framesSentElement) {
            framesSentElement.textContent = stats.frames_sent.toLocaleString();
        }

        const fpsElement = document.getElementById('fps');
        if (fpsElement) {
            fpsElement.textContent = stats.fps.toFixed(1);
        }
    }

    formatTime(seconds) {
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);

        if (hours > 0) {
            return `${hours}h ${minutes}m ${secs}s`;
        } else if (minutes > 0) {
            return `${minutes}m ${secs}s`;
        } else {
            return `${secs}s`;
        }
    }

    startStatsUpdates() {
        // Request stats every second
        this.statsInterval = setInterval(() => {
            if (this.isConnected) {
                this.socket.emit('get_stats');
            }
        }, 1000);
    }

    showNoVideoMessage() {
        const message = document.getElementById('no-video-message');
        if (message) {
            message.style.display = 'block';
        }
    }

    hideNoVideoMessage() {
        const message = document.getElementById('no-video-message');
        if (message) {
            message.style.display = 'none';
        }
    }

    cleanup() {
        if (this.statsInterval) {
            clearInterval(this.statsInterval);
        }

        if (this.socket) {
            this.socket.disconnect();
        }
    }
}

// Initialize when page loads
let videoViewer;

document.addEventListener('DOMContentLoaded', function() {
    videoViewer = new VideoViewer();
});

// Cleanup on page unload
window.addEventListener('beforeunload', function() {
    if (videoViewer) {
        videoViewer.cleanup();
    }
});
