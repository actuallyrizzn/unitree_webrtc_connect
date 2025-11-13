// Go2 Audio Stream Player
// Handles WebSocket connection and audio playback via Web Audio API

class AudioPlayer {
    constructor() {
        this.socket = null;
        this.audioContext = null;
        this.nextStartTime = 0;
        this.isReady = false;
        this.sampleRate = null;
        this.channels = null;
        this.statsInterval = null;
        this.logElement = null;

        this.init();
    }

    init() {
        this.logElement = document.getElementById('log-output');
        const startButton = document.getElementById('start-audio');
        const statusMessage = document.getElementById('audio-status');

        startButton.addEventListener('click', async () => {
            await this.initializeAudioContext();
            if (this.isReady) {
                startButton.disabled = true;
                statusMessage.textContent = 'Audio context running. Listening for stream...';
            }
        });

        this.socket = io();

        this.socket.on('connect', () => {
            this.appendLog('Connected to server');
            this.updateConnectionState(true);
        });

        this.socket.on('disconnect', () => {
            this.appendLog('Disconnected from server');
            this.updateConnectionState(false);
            this.isReady = false;
        });

        this.socket.on('audio_chunk', (chunk) => {
            this.handleAudioChunk(chunk);
        });

        this.socket.on('stats', (stats) => {
            this.updateStats(stats);
        });

        this.startStatsUpdates();
    }

    async initializeAudioContext() {
        if (!this.audioContext) {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            if (!AudioContext) {
                this.appendLog('Web Audio API not supported in this browser');
                return;
            }
            this.audioContext = new AudioContext();
        }

        if (this.audioContext.state === 'suspended') {
            await this.audioContext.resume();
        }

        this.nextStartTime = this.audioContext.currentTime;
        this.isReady = true;
        this.appendLog('Audio context initialized');
    }

    handleAudioChunk(chunk) {
        if (!this.isReady || !this.audioContext) {
            return;
        }

        try {
            const { data, sample_rate, channels } = chunk;
            if (!data) {
                return;
            }

            this.sampleRate = sample_rate || this.sampleRate || 48000;
            this.channels = channels || this.channels || 1;

            const buffer = this.base64ToArrayBuffer(data);
            const audioBuffer = this.buildAudioBuffer(buffer, this.sampleRate, this.channels);

            const source = this.audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(this.audioContext.destination);

            const startTime = Math.max(this.nextStartTime, this.audioContext.currentTime);
            source.start(startTime);
            this.nextStartTime = startTime + audioBuffer.duration;
        } catch (error) {
            console.error('Error handling audio chunk:', error);
            this.appendLog(`Audio error: ${error.message}`);
        }
    }

    base64ToArrayBuffer(base64) {
        const binaryString = atob(base64);
        const len = binaryString.length;
        const bytes = new Uint8Array(len);
        for (let i = 0; i < len; i++) {
            bytes[i] = binaryString.charCodeAt(i);
        }
        return bytes.buffer;
    }

    buildAudioBuffer(arrayBuffer, sampleRate, channels) {
        const int16Data = new Int16Array(arrayBuffer);
        const frameCount = Math.floor(int16Data.length / channels);
        const audioBuffer = this.audioContext.createBuffer(channels, frameCount, sampleRate);

        for (let channel = 0; channel < channels; channel++) {
            const channelData = audioBuffer.getChannelData(channel);
            for (let i = 0; i < frameCount; i++) {
                const sample = int16Data[i * channels + channel] / 32768;
                channelData[i] = Math.max(-1, Math.min(1, sample));
            }
        }

        return audioBuffer;
    }

    updateStats(stats) {
        const stateElement = document.getElementById('connection-state');
        if (stateElement) {
            stateElement.textContent = stats.connection_state;
            stateElement.classList.toggle('connected', stats.connection_state === 'connected');
            stateElement.classList.toggle('disconnected', stats.connection_state !== 'connected');
        }

        const uptimeElement = document.getElementById('uptime');
        if (uptimeElement) {
            uptimeElement.textContent = this.formatTime(stats.uptime);
        }

        const chunksReceived = document.getElementById('chunks-received');
        if (chunksReceived) {
            chunksReceived.textContent = (stats.chunks_received || 0).toLocaleString();
        }

        const chunksSent = document.getElementById('chunks-sent');
        if (chunksSent) {
            chunksSent.textContent = (stats.chunks_sent || 0).toLocaleString();
        }

        const sampleRateElement = document.getElementById('sample-rate');
        if (sampleRateElement) {
            sampleRateElement.textContent = stats.sample_rate ? `${stats.sample_rate} Hz` : '—';
        }

        const channelsElement = document.getElementById('channels');
        if (channelsElement) {
            channelsElement.textContent = stats.channels ? `${stats.channels}` : '—';
        }
    }

    updateConnectionState(isConnected) {
        const stateElement = document.getElementById('connection-state');
        if (stateElement) {
            stateElement.textContent = isConnected ? 'connected' : 'disconnected';
            stateElement.classList.toggle('connected', isConnected);
            stateElement.classList.toggle('disconnected', !isConnected);
        }
    }

    startStatsUpdates() {
        this.statsInterval = setInterval(() => {
            if (this.socket && this.socket.connected) {
                this.socket.emit('get_stats');
            }
        }, 1000);
    }

    formatTime(seconds) {
        const totalSeconds = Math.floor(seconds || 0);
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const secs = totalSeconds % 60;

        if (hours > 0) {
            return `${hours}h ${minutes}m ${secs}s`;
        }
        if (minutes > 0) {
            return `${minutes}m ${secs}s`;
        }
        return `${secs}s`;
    }

    appendLog(message) {
        if (!this.logElement) return;
        const timestamp = new Date().toLocaleTimeString();
        const entry = document.createElement('p');
        entry.textContent = `[${timestamp}] ${message}`;
        this.logElement.appendChild(entry);
        this.logElement.scrollTop = this.logElement.scrollHeight;
    }

    cleanup() {
        if (this.statsInterval) {
            clearInterval(this.statsInterval);
        }

        if (this.socket) {
            this.socket.disconnect();
        }

        if (this.audioContext) {
            this.audioContext.close();
        }
    }
}

let audioPlayer;

document.addEventListener('DOMContentLoaded', function () {
    audioPlayer = new AudioPlayer();
});

window.addEventListener('beforeunload', function () {
    if (audioPlayer) {
        audioPlayer.cleanup();
    }
});
