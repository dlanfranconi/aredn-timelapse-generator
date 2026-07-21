(function () {
    const params = new URLSearchParams(window.location.search);
    const cameraName = params.get('camera') || '';
    const streamKind = params.get('stream') || 'full';
    const sessionId = (window.crypto && crypto.randomUUID)
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const frame = document.getElementById('live-frame');
    const message = document.getElementById('message');
    const title = document.getElementById('camera-name');
    const status = document.getElementById('live-status');
    let heartbeatTimer = null;

    function authHeaders() {
        const token = localStorage.getItem('fenetreAuthToken');
        return token ? { Authorization: `Bearer ${token}` } : {};
    }

    function showMessage(text) {
        frame.hidden = true;
        frame.src = 'about:blank';
        message.textContent = text;
        message.hidden = false;
        status.textContent = 'Unavailable';
    }

    function mutedGo2rtcPlayerUrl(rawUrl) {
        if (!rawUrl) {
            return rawUrl;
        }
        try {
            const url = new URL(rawUrl, window.location.href);
            const page = url.pathname.split('/').pop();
            if (page === 'stream.html' || page === 'webrtc.html') {
                url.searchParams.set('media', 'video');
                url.searchParams.set('muted', '1');
                return url.href;
            }
        } catch (error) {
            if (/(^|\/)(stream|webrtc)\.html(?:\?|$)/.test(rawUrl)) {
                const separator = rawUrl.includes('?') ? '&' : '?';
                return `${rawUrl}${separator}media=video&muted=1`;
            }
        }
        return rawUrl;
    }

    async function heartbeat(active = true) {
        const payload = {
            camera: cameraName,
            stream: streamKind,
            session_id: sessionId,
            ttl_s: 45,
            active
        };
        const response = await fetch('/api/live-view/heartbeat', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', ...authHeaders() },
            body: JSON.stringify(payload)
        });
        if (!response.ok && active) {
            const result = await response.json().catch(() => ({}));
            throw new Error(result.error || `Heartbeat failed: ${response.status}`);
        }
    }

    function stopHeartbeat() {
        if (heartbeatTimer) {
            clearInterval(heartbeatTimer);
            heartbeatTimer = null;
        }
    }

    function startHeartbeat() {
        stopHeartbeat();
        heartbeat(true).catch(error => {
            status.textContent = error.message;
        });
        heartbeatTimer = setInterval(() => {
            heartbeat(true).catch(error => {
                status.textContent = error.message;
            });
        }, 15000);
    }

    async function loadLiveView() {
        if (!cameraName) {
            showMessage('Camera is required.');
            return;
        }
        const response = await fetch('/api/cameras', {
            credentials: 'same-origin',
            headers: authHeaders()
        });
        if (!response.ok) {
            showMessage(response.status === 401 ? 'Login required.' : 'Could not load cameras.');
            return;
        }
        const metadata = await response.json();
        const cameras = Array.isArray(metadata.cameras) ? metadata.cameras : [];
        const camera = cameras.find(item => item.title === cameraName);
        if (!camera || !camera.go2rtc || !camera.go2rtc.enabled) {
            showMessage('Live view is not configured for this camera.');
            return;
        }
        const go2rtc = camera.go2rtc;
        const rawPlayerUrl = streamKind === 'preview'
            ? (go2rtc.player_url || go2rtc.preview_url)
            : (go2rtc.full_player_url || go2rtc.player_url);
        const playerUrl = mutedGo2rtcPlayerUrl(rawPlayerUrl);
        if (!playerUrl) {
            showMessage('Live view URL is not available.');
            return;
        }

        title.textContent = camera.title;
        status.textContent = 'Live';
        message.hidden = true;
        frame.hidden = false;
        frame.src = playerUrl;
        startHeartbeat();
    }

    window.addEventListener('beforeunload', () => {
        stopHeartbeat();
        const payload = JSON.stringify({
            camera: cameraName,
            stream: streamKind,
            session_id: sessionId,
            active: false
        });
        if (navigator.sendBeacon) {
            navigator.sendBeacon(
                '/api/live-view/heartbeat',
                new Blob([payload], { type: 'application/json' })
            );
            return;
        }
        fetch('/api/live-view/heartbeat', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', ...authHeaders() },
            body: payload,
            keepalive: true
        }).catch(() => {});
    });

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible' && !frame.hidden) {
            heartbeat(true).catch(error => {
                status.textContent = error.message;
            });
        }
    });

    loadLiveView().catch(error => {
        showMessage(error.message);
    });
})();
