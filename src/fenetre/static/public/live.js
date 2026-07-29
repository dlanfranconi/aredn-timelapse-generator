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
    const defaultGo2rtcMode = '';

    function authHeaders() {
        const token = localStorage.getItem('fenetreAuthToken');
        return token ? { Authorization: `Bearer ${token}` } : {};
    }

    function hasAuthToken() {
        return Boolean(localStorage.getItem('fenetreAuthToken'));
    }

    function showMessage(text) {
        frame.hidden = true;
        frame.src = 'about:blank';
        message.textContent = text;
        message.hidden = false;
        status.textContent = 'Unavailable';
    }

    function go2rtcPlayerMode(go2rtc) {
        return String((go2rtc && go2rtc.player_mode) || defaultGo2rtcMode).trim() || defaultGo2rtcMode;
    }

    function go2rtcPreviewMode(go2rtc) {
        return String((go2rtc && (go2rtc.preview_mode || go2rtc.player_mode)) || defaultGo2rtcMode).trim()
            || defaultGo2rtcMode;
    }

    function mutedGo2rtcPlayerUrl(rawUrl, mode = '') {
        if (!rawUrl) {
            return rawUrl;
        }
        try {
            const url = new URL(rawUrl, window.location.href);
            const page = url.pathname.split('/').pop();
            if (page === 'stream.html' || page === 'webrtc.html') {
                if (page === 'stream.html' && mode && !url.searchParams.has('mode')) {
                    url.searchParams.set('mode', mode);
                }
                url.searchParams.set('media', 'video');
                url.searchParams.set('muted', '1');
                return url.href;
            }
        } catch (error) {
            if (/(^|\/)(stream|webrtc)\.html(?:\?|$)/.test(rawUrl)) {
                const separator = rawUrl.includes('?') ? '&' : '?';
                const modeParam = mode && /(^|[?&])mode=/.test(rawUrl) === false
                    ? `mode=${encodeURIComponent(mode)}&`
                    : '';
                return `${rawUrl}${separator}${modeParam}media=video&muted=1`;
            }
        }
        return rawUrl;
    }

    function sameHostGo2rtcBaseUrl(go2rtc) {
        const port = Number(go2rtc && go2rtc.same_host_port ? go2rtc.same_host_port : 1984);
        if (!Number.isFinite(port) || port <= 0) {
            return '';
        }
        const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:';
        return `${protocol}//${window.location.hostname}:${port}`;
    }

    function sameHostGo2rtcPlayerUrl(go2rtc, streamName) {
        if (!streamName) {
            return '';
        }
        const baseUrl = sameHostGo2rtcBaseUrl(go2rtc);
        if (!baseUrl) {
            return '';
        }
        const mode = go2rtcPlayerMode(go2rtc);
        const modeParam = mode ? `&mode=${encodeURIComponent(mode)}` : '';
        return `${baseUrl}/stream.html?src=${encodeURIComponent(streamName)}${modeParam}&media=video&muted=1`;
    }

    function sameHostGo2rtcPreviewUrl(go2rtc, streamName) {
        if (!streamName) {
            return '';
        }
        const baseUrl = sameHostGo2rtcBaseUrl(go2rtc);
        if (!baseUrl) {
            return '';
        }
        const mode = go2rtcPreviewMode(go2rtc);
        const modeParam = mode ? `&mode=${encodeURIComponent(mode)}` : '';
        return `${baseUrl}/stream.html?src=${encodeURIComponent(streamName)}${modeParam}&media=video&muted=1`;
    }

    function browserHostCandidates() {
        const candidates = [];
        [window.location.host, window.location.hostname].forEach(value => {
            const host = String(value || '').trim().toLowerCase();
            if (!host || candidates.includes(host)) {
                return;
            }
            candidates.push(host);
            const withoutDefaultPort = host
                .replace(/:443$/, '')
                .replace(/:80$/, '');
            if (withoutDefaultPort && !candidates.includes(withoutDefaultPort)) {
                candidates.push(withoutDefaultPort);
            }
        });
        return candidates;
    }

    function isLocalGo2rtcFallbackHost() {
        const host = String(window.location.hostname || '').trim().toLowerCase();
        if (!host) {
            return false;
        }
        if (host === 'localhost' || host.endsWith('.local') || host.endsWith('.local.mesh')
            || host.endsWith('.mesh') || host.endsWith('.lan') || host.endsWith('.home.arpa')
            || host.endsWith('.ts.net')) {
            return true;
        }
        if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)) {
            return true;
        }
        if (host.includes(':')) {
            return true;
        }
        return false;
    }

    function canUseSameHostGo2rtcFallback(go2rtc) {
        if (!go2rtc || go2rtc.base_url_configured !== false) {
            return false;
        }
        if (go2rtc.base_urls_configured) {
            return isLocalGo2rtcFallbackHost();
        }
        return true;
    }

    function hostSpecificGo2rtcPlayerUrl(urlsByHost, mode = '') {
        if (!urlsByHost || typeof urlsByHost !== 'object') {
            return '';
        }
        for (const host of browserHostCandidates()) {
            if (urlsByHost[host]) {
                return mutedGo2rtcPlayerUrl(urlsByHost[host], mode);
            }
        }
        return '';
    }

    function cameraId(camera) {
        return camera.id || camera.title;
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
        const camera = cameras.find(item => cameraId(item) === cameraName);
        const go2rtc = (camera && camera.go2rtc) || {};
        if (!camera) {
            showMessage('Camera was not found or is not visible for this login.');
            return;
        }
        if (go2rtc.enabled !== true || !go2rtc.stream) {
            showMessage(
                hasAuthToken()
                    ? 'Live view is not configured for this camera.'
                    : 'Login required to view live streams on this host.'
            );
            return;
        }
        const rawPlayerUrl = streamKind === 'preview'
            ? (go2rtc.preview_url || go2rtc.player_url)
            : (go2rtc.full_player_url || go2rtc.player_url);
        const fallbackStream = streamKind === 'preview'
            ? go2rtc.stream
            : (go2rtc.full_stream || go2rtc.stream);
        const mode = streamKind === 'preview'
            ? go2rtcPreviewMode(go2rtc)
            : go2rtcPlayerMode(go2rtc);
        const hostMappedPlayerUrl = streamKind === 'preview'
            ? hostSpecificGo2rtcPlayerUrl(go2rtc.preview_urls || go2rtc.player_urls, mode)
            : hostSpecificGo2rtcPlayerUrl(go2rtc.full_player_urls || go2rtc.player_urls, mode);
        const playerUrl = hostMappedPlayerUrl
            || mutedGo2rtcPlayerUrl(rawPlayerUrl, mode)
            || (canUseSameHostGo2rtcFallback(go2rtc)
                ? (streamKind === 'preview'
                    ? sameHostGo2rtcPreviewUrl(go2rtc, fallbackStream)
                    : sameHostGo2rtcPlayerUrl(go2rtc, fallbackStream))
                : '');
        if (!playerUrl) {
            const host = window.location.hostname || window.location.host || 'this host';
            if (go2rtc.base_urls_configured) {
                showMessage(`Live view URL is not available for ${host}. Add a matching global.go2rtc.base_urls entry for this browser host.`);
                return;
            }
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
