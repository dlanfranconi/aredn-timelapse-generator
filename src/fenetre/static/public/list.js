const themeToggle = document.getElementById('theme-toggle');
const body = document.body;
const mapToggleButton = document.getElementById('map-toggle');
const loginToggle = document.getElementById('login-toggle');
const accountMenu = document.getElementById('account-menu');
const accountMenuRole = document.getElementById('account-menu-role');
const loginPanel = document.getElementById('login-panel');
const loginUsername = document.getElementById('login-username');
const loginPassword = document.getElementById('login-password');
const loginSubmit = document.getElementById('login-submit');
const changePasswordToggle = document.getElementById('change-password-toggle');
const logoutSubmit = document.getElementById('logout-submit');
const loginStatus = document.getElementById('login-status');
const passwordPanel = document.getElementById('password-panel');
const currentPassword = document.getElementById('current-password');
const newPassword = document.getElementById('new-password');
const confirmPassword = document.getElementById('confirm-password');
const passwordSubmit = document.getElementById('password-submit');
const passwordCancel = document.getElementById('password-cancel');
const passwordStatus = document.getElementById('password-status');
const privateLanding = document.getElementById('private-landing');
const privateLandingName = document.getElementById('private-landing-name');
const privateLoginButton = document.getElementById('private-login-button');
const launchDashboardLink = document.getElementById('launch-dashboard-link');
let authToken = localStorage.getItem('fenetreAuthToken') || '';
let authUser = null;
let siteIsPublic = true;
let launchWorkflowEnabled = false;
let deploymentName = 'Fenetre';
const defaultGo2rtcMode = 'webrtc,webrtc/tcp,mse,mp4';

function syncThemeToggleIcon() {
    themeToggle.classList.toggle('dark-mode-active', body.classList.contains('dark-mode'));
}

const prefersDarkMode = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
const storedTheme = localStorage.getItem('theme');

if (storedTheme === 'dark' || (storedTheme === null && prefersDarkMode)) {
    body.classList.add('dark-mode');
}
syncThemeToggleIcon();

themeToggle.addEventListener('click', () => {
    body.classList.toggle('dark-mode');
    const theme = body.classList.contains('dark-mode') ? 'dark' : 'light';
    localStorage.setItem('theme', theme);
    syncThemeToggleIcon();
    applyMapTheme(theme === 'dark');
});

const cameraListElement = document.getElementById('camera-list');
const mapPanel = document.getElementById('map-panel');
const listPanel = document.getElementById('list-panel');
const mapElement = document.getElementById('map');

const lightTileLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18,
    minZoom: 0,
    attribution: '&copy; OpenStreetMap contributors'
});

const darkTileLayer = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    maxZoom: 18,
    minZoom: 0,
    subdomains: 'abcd',
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO'
});

const initialTileLayer = body.classList.contains('dark-mode') ? darkTileLayer : lightTileLayer;
const map = L.map('map', {
    layers: [initialTileLayer]
}).setView([0, 0], 2);

let activeTileLayer = initialTileLayer;
let latestMarkerBounds = null;
const markerBoundsFitOptions = { padding: [50, 50] };
let mapVisible = false;
let mapVisibilityInitialized = false;
let remoteFetchGeneration = 0;

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
    const mode = encodeURIComponent(go2rtcPlayerMode(go2rtc));
    return `${baseUrl}/stream.html?src=${encodeURIComponent(streamName)}&mode=${mode}&media=video&muted=1`;
}

function sameHostGo2rtcPreviewUrl(go2rtc, streamName) {
    if (!streamName) {
        return '';
    }
    const baseUrl = sameHostGo2rtcBaseUrl(go2rtc);
    if (!baseUrl) {
        return '';
    }
    const mode = encodeURIComponent(go2rtcPreviewMode(go2rtc));
    return `${baseUrl}/stream.html?src=${encodeURIComponent(streamName)}&mode=${mode}&media=video&muted=1`;
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

function go2rtcPreviewPlayerUrl(go2rtc) {
    const mode = go2rtcPreviewMode(go2rtc);
    const hostUrl = hostSpecificGo2rtcPlayerUrl(
        go2rtc.preview_urls || go2rtc.player_urls,
        mode
    );
    if (hostUrl) {
        return hostUrl;
    }
    const configuredUrl = mutedGo2rtcPlayerUrl(
        go2rtc.preview_url || go2rtc.player_url,
        mode
    );
    if (configuredUrl) {
        return configuredUrl;
    }
    if (canUseSameHostGo2rtcFallback(go2rtc)) {
        return sameHostGo2rtcPreviewUrl(go2rtc, go2rtc.stream);
    }
    return '';
}

function go2rtcFullPlayerUrl(go2rtc) {
    const mode = go2rtcPlayerMode(go2rtc);
    const hostUrl = hostSpecificGo2rtcPlayerUrl(
        go2rtc.full_player_urls || go2rtc.player_urls,
        mode
    );
    if (hostUrl) {
        return hostUrl;
    }
    const configuredUrl = mutedGo2rtcPlayerUrl(
        go2rtc.full_player_url || go2rtc.player_url,
        mode
    );
    if (configuredUrl) {
        return configuredUrl;
    }
    if (canUseSameHostGo2rtcFallback(go2rtc)) {
        return sameHostGo2rtcPlayerUrl(go2rtc, go2rtc.full_stream || go2rtc.stream);
    }
    return '';
}

function fitMapToMarkers() {
    setTimeout(() => {
        map.invalidateSize();
        if (latestMarkerBounds && latestMarkerBounds.isValid()) {
            map.fitBounds(latestMarkerBounds, markerBoundsFitOptions);
        }
    }, 250);
}

function setMapVisible(visible) {
    mapVisible = visible;
    body.classList.toggle('map-visible', visible);
    mapToggleButton.classList.toggle('map-open', visible);
    mapToggleButton.setAttribute('aria-pressed', String(visible));
    if (visible) {
        fitMapToMarkers();
    }
}

mapToggleButton.addEventListener('click', () => {
    setMapVisible(!mapVisible);
});

function authHeaders() {
    return authToken ? { Authorization: `Bearer ${authToken}` } : {};
}

function newClientSessionId() {
    if (window.crypto && crypto.randomUUID) {
        return crypto.randomUUID();
    }
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function sendLiveViewHeartbeat(camera, stream, sessionId, active = true) {
    if (!camera || !sessionId || !authToken) {
        return null;
    }
    const response = await fetch('/api/live-view/heartbeat', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
            camera,
            stream,
            session_id: sessionId,
            ttl_s: 45,
            active
        })
    });
    if (!response.ok && active) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.error || `Live-view heartbeat failed: ${response.status}`);
    }
    return response.json().catch(() => null);
}

function storeAuthToken(token) {
    authToken = token || '';
    if (authToken) {
        localStorage.setItem('fenetreAuthToken', authToken);
        document.cookie = `fenetreAuthToken=${encodeURIComponent(authToken)}; Path=/; SameSite=Lax`;
    } else {
        localStorage.removeItem('fenetreAuthToken');
        document.cookie = 'fenetreAuthToken=; Path=/; Max-Age=0; SameSite=Lax';
    }
}

function setDeploymentName(name) {
    deploymentName = name || deploymentName;
    document.querySelector('#list-header h1').textContent = `${deploymentName} Cameras`;
    privateLandingName.textContent = deploymentName;
    document.title = `${deploymentName} Cameras`;
}

function updateLaunchDashboardLink() {
    if (launchDashboardLink) {
        launchDashboardLink.hidden = !launchWorkflowEnabled;
    }
}

function updatePrivateLanding() {
    const locked = !siteIsPublic && !authUser;
    privateLanding.hidden = !locked;
    cameraListElement.hidden = locked;
    if (locked) {
        cameraListElement.innerHTML = '';
        clearCameraLayers();
        setMapVisible(false);
    }
}

function syncLoginUi() {
    loginToggle.textContent = authUser ? authUser.username : 'Login';
    loginToggle.setAttribute('aria-expanded', authUser && !accountMenu.hidden ? 'true' : 'false');
    loginSubmit.hidden = Boolean(authUser);
    loginUsername.hidden = Boolean(authUser);
    loginPassword.hidden = Boolean(authUser);
    loginStatus.textContent = '';
    accountMenuRole.textContent = authUser ? `${authUser.role || 'viewer'} access` : '';
    if (authUser) {
        loginPanel.hidden = true;
    } else {
        closeAccountMenu();
        passwordPanel.hidden = true;
    }
    updatePrivateLanding();
}

async function loadAuthStatus() {
    try {
        const response = await fetch('/api/auth/status', { headers: authHeaders() });
        const data = await response.json();
        siteIsPublic = data.public_site !== false;
        launchWorkflowEnabled = data.launch_workflow_enabled === true;
        setDeploymentName(data.deployment_name || deploymentName);
        authUser = data.authenticated ? data.user : null;
        if (!authUser) {
            storeAuthToken('');
        }
    } catch (error) {
        authUser = null;
    }
    updateLaunchDashboardLink();
    syncLoginUi();
}

loginToggle.addEventListener('click', () => {
    if (authUser) {
        toggleAccountMenu();
        return;
    }
    openLoginPanel();
});

privateLoginButton.addEventListener('click', openLoginPanel);

function openLoginPanel() {
    closeAccountMenu();
    loginPanel.hidden = false;
    loginStatus.textContent = '';
    window.setTimeout(() => loginUsername.focus(), 0);
}

function closeAccountMenu() {
    accountMenu.hidden = true;
    loginToggle.setAttribute('aria-expanded', 'false');
}

function toggleAccountMenu() {
    loginPanel.hidden = true;
    accountMenu.hidden = !accountMenu.hidden;
    loginToggle.setAttribute('aria-expanded', accountMenu.hidden ? 'false' : 'true');
}

async function loginWithJsonCredentials() {
    loginStatus.textContent = 'Signing in...';
    try {
        const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: loginUsername.value.trim(), password: loginPassword.value })
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || `Login failed: ${response.status}`);
        }
        storeAuthToken(data.token);
        authUser = data.user;
        loginPassword.value = '';
        syncLoginUi();
        window.location.reload();
    } catch (error) {
        loginStatus.textContent = error.message;
    }
}

loginSubmit.addEventListener('click', loginWithJsonCredentials);
loginUsername.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
        loginWithJsonCredentials();
    }
});
loginPassword.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
        loginWithJsonCredentials();
    }
});

logoutSubmit.addEventListener('click', async () => {
    closeAccountMenu();
    if (authToken) {
        await fetch('/api/auth/logout', { method: 'POST', headers: authHeaders() }).catch(() => {});
    }
    storeAuthToken('');
    authUser = null;
    passwordPanel.hidden = true;
    syncLoginUi();
    updateAllCameras();
});

changePasswordToggle.addEventListener('click', () => {
    closeAccountMenu();
    passwordPanel.hidden = false;
    passwordStatus.textContent = '';
    if (!passwordPanel.hidden) {
        currentPassword.focus();
    }
});

document.addEventListener('click', event => {
    if (
        authUser
        && !accountMenu.hidden
        && !accountMenu.contains(event.target)
        && event.target !== loginToggle
    ) {
        closeAccountMenu();
    }
});

document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
        closeAccountMenu();
    }
});

passwordCancel.addEventListener('click', () => {
    currentPassword.value = '';
    newPassword.value = '';
    confirmPassword.value = '';
    passwordStatus.textContent = '';
    passwordPanel.hidden = true;
});

async function changeOwnPassword() {
    if (!authToken) {
        passwordStatus.textContent = 'Login required.';
        return;
    }
    if (newPassword.value !== confirmPassword.value) {
        passwordStatus.textContent = 'New passwords do not match.';
        return;
    }
    passwordSubmit.disabled = true;
    passwordStatus.textContent = 'Saving...';
    try {
        const response = await fetch('/api/auth/change-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...authHeaders() },
            body: JSON.stringify({
                current_password: currentPassword.value,
                new_password: newPassword.value
            })
        });
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || `Password change failed: ${response.status}`);
        }
        currentPassword.value = '';
        newPassword.value = '';
        confirmPassword.value = '';
        passwordStatus.textContent = result.message || 'Password changed.';
        storeAuthToken('');
        authUser = null;
        window.setTimeout(() => window.location.reload(), 700);
    } catch (error) {
        passwordStatus.textContent = error.message;
    } finally {
        passwordSubmit.disabled = false;
    }
}

passwordSubmit.addEventListener('click', changeOwnPassword);
[currentPassword, newPassword, confirmPassword].forEach(input => {
    input.addEventListener('keydown', event => {
        if (event.key === 'Enter') {
            changeOwnPassword();
        }
    });
});

function applyMapTheme(isDark) {
    const desiredLayer = isDark ? darkTileLayer : lightTileLayer;
    if (desiredLayer === activeTileLayer) {
        return;
    }
    map.addLayer(desiredLayer);
    map.removeLayer(activeTileLayer);
    activeTileLayer = desiredLayer;
}

var markerCluster = L.markerClusterGroup();
var circleLayerGroup = L.layerGroup();
map.addLayer(markerCluster);
map.addLayer(circleLayerGroup);

var cameraMarkers = {};

function clearCameraLayers() {
    markerCluster.clearLayers();
    circleLayerGroup.clearLayers();
    cameraMarkers = {};
    latestMarkerBounds = null;
}

function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
}

function getLayerBounds(layer) {
    if (!layer) {
        return null;
    }
    if (typeof layer.getBounds === 'function') {
        return layer.getBounds();
    }
    if (typeof layer.getLatLng === 'function') {
        const latLng = layer.getLatLng();
        return L.latLngBounds(latLng, latLng);
    }
    return null;
}

function extendBoundsWithLayer(layer) {
    const layerBounds = getLayerBounds(layer);
    if (!layerBounds) {
        return;
    }
    if (latestMarkerBounds) {
        latestMarkerBounds.extend(layerBounds.getSouthWest());
        latestMarkerBounds.extend(layerBounds.getNorthEast());
    } else {
        latestMarkerBounds = layerBounds;
    }
}

function focusCameraLayer(layer) {
    if (!layer) {
        return;
    }
    if (layer instanceof L.Marker && markerCluster.hasLayer(layer)) {
        markerCluster.zoomToShowLayer(layer, () => layer.openPopup());
        return;
    }
    const bounds = getLayerBounds(layer);
    if (bounds) {
        map.fitBounds(bounds, markerBoundsFitOptions);
        if (typeof layer.openPopup === 'function') {
            layer.openPopup();
        }
    }
}

function addCameraLayer(lat, lon, radiusMeters, popupHtml) {
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
        return null;
    }

    const coords = L.latLng(lat, lon);
    const radius = Number.isFinite(radiusMeters) ? radiusMeters : 0;
    let layer;

    if (radius > 0) {
        layer = L.circle(coords, {
            radius,
            color: '#3388ff',
            fillColor: '#3388ff',
            fillOpacity: 0.15,
            weight: 1,
        });
    } else {
        layer = L.marker(coords);
    }

    if (popupHtml) {
        layer.bindPopup(popupHtml);
    }

    if (layer instanceof L.Marker) {
        markerCluster.addLayer(layer);
    } else {
        circleLayerGroup.addLayer(layer);
    }

    extendBoundsWithLayer(layer);
    return layer;
}

function createPopupContent(camera) {
    const description = camera.description ? `<br><span>${escapeHtml(camera.description)}</span>` : '';
    return `<b>${escapeHtml(cameraDisplayName(camera))}</b>${description}`;
}

function cameraId(camera) {
    return camera.id || camera.title;
}

function cameraDisplayName(camera) {
    return camera.title || camera.id || '';
}

function updateCameraMap(cameras) {
    clearCameraLayers();
    cameras.forEach(camera => {
        const lat = camera.lat == null ? NaN : Number(camera.lat);
        const lon = camera.lon == null ? NaN : Number(camera.lon);
        const layer = addCameraLayer(
            lat,
            lon,
            Number(camera.map_radius_m || 0),
            createPopupContent(camera)
        );
        if (layer) {
            cameraMarkers[cameraId(camera)] = layer;
        }
    });
    if (mapVisible) {
        fitMapToMarkers();
    }
}

function updateHeaderLinks(uiConfig) {
    const mainWebsiteLink = document.getElementById('main-website-link');
    const githubLink = document.getElementById('github-link');

    if (mainWebsiteLink) {
        mainWebsiteLink.href = uiConfig.main_website_url || 'https://fenetre.cam';
        mainWebsiteLink.style.display = uiConfig.show_main_website_icon === false ? 'none' : 'flex';
    }

    if (githubLink) {
        githubLink.style.display = uiConfig.show_github_icon === false ? 'none' : 'flex';
    }
}

function parseTimestampFromFilename(filename) {
    try {
        const match = filename.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})/);
        if (!match) return null;

        const date = new Date(
            parseInt(match[1], 10),
            parseInt(match[2], 10) - 1,
            parseInt(match[3], 10),
            parseInt(match[4], 10),
            parseInt(match[5], 10),
            parseInt(match[6], 10)
        );

        return isNaN(date.getTime()) ? null : date;
    } catch (e) {
        console.error('Error parsing timestamp:', e);
        return null;
    }
}

function formatDate(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function buildTimelapsePlayerUrl(src, title) {
    const params = new URLSearchParams({ src, title });
    return `timelapse.html?${params.toString()}`;
}

async function fetchCameraTimelapses(cameraName) {
    const response = await fetch(`/api/timelapses?camera=${encodeURIComponent(cameraName)}`, { headers: authHeaders() });
    if (!response.ok) {
        throw new Error(`Failed to load timelapses for ${cameraName}: ${response.status}`);
    }
    return response.json();
}

function applyTimelapseLink(link, timelapse, title) {
    if (!timelapse) {
        link.style.display = 'none';
        link.removeAttribute('href');
        return;
    }

    if (timelapse.format === 'm3u8') {
        link.href = buildTimelapsePlayerUrl(timelapse.url, title);
    } else {
        link.href = timelapse.url;
    }
    link.style.display = 'inline-block';
}

function configureTodayTimelapseLink(link, camera, dateString, cameraData) {
    const frequentTimelapseExtension = cameraData.global.frequent_timelapse_file_extension || 'mp4';
    const id = cameraId(camera);
    const displayName = cameraDisplayName(camera);
    const photoDir = `/photos/${id}`;
    const startOfDay = new Date(new Date().getFullYear(), new Date().getMonth(), new Date().getDate());
    const minutesElapsed = (new Date() - startOfDay) / 60000;
    const cacheBuster = Math.floor(minutesElapsed / 20);
    const url = `${photoDir}/${dateString}/${dateString}.${frequentTimelapseExtension}?v=${cacheBuster}`;

    if (frequentTimelapseExtension === 'm3u8') {
        link.href = buildTimelapsePlayerUrl(
            url,
            `${displayName} ${dateString} Frequent Timelapse`
        );
    } else {
        link.href = url;
    }
    link.style.display = 'inline-block';
}

function populateTimelapseArchive(select, timelapses, todayStr) {
    const archiveItems = timelapses
        .filter(item => item.date !== todayStr)
        .filter(item => item.type === 'daily')
        .filter(item => item.format !== 'm3u8' && item.url);
    select.innerHTML = '';

    if (archiveItems.length === 0) {
        select.style.display = 'none';
        return;
    }

    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Previous Timelapses';
    select.appendChild(placeholder);

    archiveItems.forEach(item => {
        const option = document.createElement('option');
        option.value = item.url;
        option.dataset.format = item.format;
        option.dataset.date = item.date;
        option.dataset.type = item.type;
        option.textContent = item.date;
        select.appendChild(option);
    });

    select.style.display = 'inline-block';
}

async function updateTimelapseArchiveSelect(camera, select, todayStr) {
    const id = cameraId(camera);
    try {
        const timelapseData = await fetchCameraTimelapses(id);
        const timelapses = timelapseData.timelapses || [];
        populateTimelapseArchive(select, timelapses, todayStr);
    } catch (error) {
        select.style.display = 'none';
        console.error(`Failed to load timelapse archive for ${id}:`, error);
    }
}

function cacheBustedImageUrl(url) {
    if (!url) {
        return url;
    }
    const separator = url.includes('?') ? '&' : '?';
    return `${url}${separator}_fenetre_view=${Date.now()}`;
}

function applyCameraMetadata(camera, listItem, metadata, options = {}) {
    const id = cameraId(camera);
    const thumbImg = listItem.querySelector('.camera-header img');
    const lastPictureTime = listItem.querySelector('.last-picture-time');
    const cameraMetadata = listItem.querySelector('.camera-metadata');
    const status = listItem.querySelector('.status');
    const detailsImg = listItem.querySelector('.camera-details img');
    const fullscreenImageLink = listItem.querySelector('.fullscreen-image-link');
    const filenameLink = listItem.querySelector('.camera-details .filename');
    const linkFullscreen = listItem.querySelector('.link-fullscreen');
    const linkToday = listItem.querySelector('.link-today');
    const linkHistory = listItem.querySelector('.link-history');
    const todayStr = formatDate(new Date());
    const photoDir = `/photos/${id}`;
    const lastPictureUrl = metadata.last_picture_url;
    if (!lastPictureUrl) {
        lastPictureTime.textContent = 'No picture available';
        status.className = 'status offline';
        return '';
    }

    const basePath = camera.dynamic_metadata.substring(0, camera.dynamic_metadata.lastIndexOf('/'));
    const fullImageUrl = `/${basePath}/${lastPictureUrl}`;
    const filename = lastPictureUrl.substring(lastPictureUrl.lastIndexOf('/') + 1);

    const imageSrc = options.cacheBustImages
        ? cacheBustedImageUrl(fullImageUrl)
        : fullImageUrl;
    thumbImg.src = imageSrc;
    detailsImg.src = imageSrc;
    listItem.dataset.lastPictureUrl = fullImageUrl;
    filenameLink.textContent = `Download: ${filename}`;
    filenameLink.href = fullImageUrl;

    const imageDate = parseTimestampFromFilename(filename);
    if (imageDate) {
        lastPictureTime.textContent = `Last picture: ${imageDate.toLocaleString()}`;
        status.className = `status ${(new Date() - imageDate) < 180000 ? 'online' : 'offline'}`;
    }

    if (metadata.iso || metadata.shutter_speed) {
        cameraMetadata.textContent = `ISO ${metadata.iso || '?'} | ${metadata.shutter_speed || '?'}`;
    } else {
        cameraMetadata.textContent = '';
    }

    const fullscreenUrl = camera.fullscreen_url || `fullscreen.html?camera=${encodeURIComponent(id)}`;
    linkFullscreen.href = fullscreenUrl;
    fullscreenImageLink.href = fullscreenUrl;
    linkToday.href = `${photoDir}/${todayStr}/`;
    linkHistory.href = `${photoDir}/daylight.html`;
    return fullImageUrl;
}

async function refreshCameraMetadata(camera, listItem, options = {}) {
    if (!camera.dynamic_metadata) {
        return false;
    }
    const response = await fetch(camera.dynamic_metadata, {
        cache: 'no-store',
        headers: authHeaders()
    });
    if (!response.ok) {
        throw new Error('Network response was not ok.');
    }
    const metadata = await response.json();
    const basePath = camera.dynamic_metadata.substring(0, camera.dynamic_metadata.lastIndexOf('/'));
    const lastPictureUrl = metadata.last_picture_url;
    const fullImageUrl = lastPictureUrl ? `/${basePath}/${lastPictureUrl}` : '';
    if (options.waitForChange && options.previousImageUrl && fullImageUrl === options.previousImageUrl) {
        return false;
    }
    return Boolean(applyCameraMetadata(camera, listItem, metadata, options));
}

function pollCameraSnapshotRefresh(camera, listItem, previousImageUrl) {
    const ptzStatus = listItem.querySelector('.ptz-status');
    let attempts = 0;
    const maxAttempts = 60;
    const delayMs = 1500;

    const poll = () => {
        attempts += 1;
        refreshCameraMetadata(camera, listItem, {
            cacheBustImages: true,
            waitForChange: true,
            previousImageUrl
        })
            .then(updated => {
                if (updated) {
                    ptzStatus.textContent = 'Snapshot refreshed';
                    return;
                }
                if (attempts >= maxAttempts) {
                    ptzStatus.textContent = 'Preset moved; waiting for next snapshot';
                    return;
                }
                setTimeout(poll, delayMs);
            })
            .catch(error => {
                if (attempts >= maxAttempts) {
                    ptzStatus.textContent = `Snapshot refresh failed: ${error.message}`;
                    return;
                }
                setTimeout(poll, delayMs);
            });
    };

    setTimeout(poll, delayMs);
}

function createCameraListItem(camera) {
    const id = cameraId(camera);
    const displayName = cameraDisplayName(camera);
    const escapedDisplayName = escapeHtml(displayName);
    const listItem = document.createElement('li');
    listItem.className = 'camera-item';
    listItem.dataset.cameraId = id;

    listItem.innerHTML = `
        <div class="camera-header">
            <img src="" alt="${escapedDisplayName} thumbnail">
            <div class="camera-info">
                <div class="camera-name">${escapedDisplayName}</div>
                <div class="camera-description"></div>
                <div class="last-picture-time">Loading...</div>
                <div class="camera-metadata"></div>
            </div>
            <div class="status"></div>
        </div>
        <div class="camera-details">
            <a class="fullscreen-image-link" href="#" target="_blank">
                <img src="" alt="Full image for ${escapedDisplayName}">
            </a>
            <a class="filename" href="#" download></a>
            <div class="links">
                <a class="link-fullscreen" href="#" target="_blank">Fullscreen</a>
                <a class="link-today" href="#" target="_blank">Today's Pictures</a>
                <a class="link-timelapse-today" href="#" target="_blank">Today's Timelapse</a>
                <select class="select-timelapse-archive" aria-label="Timelapse archive"></select>
                <a class="link-history" href="#" target="_blank">History</a>
            </div>
            <div class="ptz-presets" hidden>
                <select class="select-ptz-preset" aria-label="PTZ preset"></select>
                <button class="btn-ptz-preset" type="button">Go</button>
                <div class="ptz-manual" hidden>
                    <div class="ptz-manual-settings">
                        <label class="ptz-speed-control">Speed
                            <input class="input-ptz-speed" type="range" min="0.05" max="1" step="0.05" value="0.35">
                        </label>
                        <label class="ptz-duration-control">Nudge
                            <select class="select-ptz-duration" aria-label="PTZ nudge duration">
                                <option value="150">Short</option>
                                <option value="250" selected>Medium</option>
                                <option value="500">Long</option>
                                <option value="1000">Very long</option>
                            </select>
                        </label>
                    </div>
                    <div class="ptz-control-panel">
                        <div class="ptz-rose" aria-label="Pan and tilt controls">
                            <span></span>
                            <button class="ptz-rose-button" type="button" data-axis="tilt" data-pan="0" data-tilt="1" aria-label="Tilt up">&uarr;</button>
                            <span></span>
                            <button class="ptz-rose-button" type="button" data-axis="pan" data-pan="-1" data-tilt="0" aria-label="Pan left">&larr;</button>
                            <button class="ptz-rose-stop" type="button" data-stop="1">Stop</button>
                            <button class="ptz-rose-button" type="button" data-axis="pan" data-pan="1" data-tilt="0" aria-label="Pan right">&rarr;</button>
                            <span></span>
                            <button class="ptz-rose-button" type="button" data-axis="tilt" data-pan="0" data-tilt="-1" aria-label="Tilt down">&darr;</button>
                            <span></span>
                        </div>
                        <div class="ptz-lens-controls" aria-label="Zoom and focus controls">
                            <button type="button" data-axis="zoom" data-zoom="1">Zoom +</button>
                            <button type="button" data-axis="zoom" data-zoom="-1">Zoom -</button>
                            <button type="button" data-axis="focus" data-focus="1">Focus +</button>
                            <button type="button" data-axis="focus" data-focus="-1">Focus -</button>
                        </div>
                    </div>
                </div>
                <div class="ptz-tour" hidden>
                    <div class="ptz-tour-label">Tour control</div>
                    <button type="button" data-tour="start" title="Start tour" aria-label="Start tour"><span aria-hidden="true">&#9654;</span></button>
                    <button type="button" data-tour="stop" title="Stop tour" aria-label="Stop tour"><span aria-hidden="true">&#9632;</span></button>
                    <button type="button" data-tour="pause" title="Pause tour" aria-label="Pause tour"><span aria-hidden="true">&#9208;</span></button>
                    <button type="button" data-tour="resume" title="Resume tour" aria-label="Resume tour"><span aria-hidden="true">&#9654;</span></button>
                </div>
                <div class="ptz-live-view" hidden>
                    <button class="ptz-live-preview" type="button" aria-label="Start PTZ alignment preview">
                        <span class="ptz-live-placeholder">Click to start low-resolution aiming stream</span>
                        <img class="ptz-live-image" alt="PTZ alignment preview">
                    </button>
                    <iframe class="ptz-live-frame" title="PTZ alignment preview" loading="lazy" allow="autoplay; fullscreen" hidden></iframe>
                    <a class="ptz-live-link" href="#" target="_blank" rel="noopener">Open high-resolution stream</a>
                </div>
                <span class="ptz-status"></span>
            </div>
        </div>
    `;

    listItem.querySelector('.camera-header').addEventListener('click', () => {
        const details = listItem.querySelector('.camera-details');
        details.classList.toggle('active');
        if (mapVisible) {
            focusCameraLayer(cameraMarkers[id]);
        }
    });

    return listItem;
}

function configurePtzPresets(camera, listItem) {
    const id = cameraId(camera);
    const ptz = camera.ptz || {};
    const presets = Array.isArray(ptz.presets) ? ptz.presets : [];
    const cachedPresets = Array.isArray(listItem._ptzDiscoveredPresets)
        ? listItem._ptzDiscoveredPresets
        : presets;
    const wrapper = listItem.querySelector('.ptz-presets');
    const select = listItem.querySelector('.select-ptz-preset');
    const button = listItem.querySelector('.btn-ptz-preset');
    const manual = listItem.querySelector('.ptz-manual');
    const tourControls = listItem.querySelector('.ptz-tour');
    const speedInput = listItem.querySelector('.input-ptz-speed');
    const durationSelect = listItem.querySelector('.select-ptz-duration');
    const liveView = listItem.querySelector('.ptz-live-view');
    const livePreview = listItem.querySelector('.ptz-live-preview');
    const liveImage = listItem.querySelector('.ptz-live-image');
    const liveFrame = listItem.querySelector('.ptz-live-frame');
    const livePlaceholder = listItem.querySelector('.ptz-live-placeholder');
    const liveLink = listItem.querySelector('.ptz-live-link');
    const status = listItem.querySelector('.ptz-status');
    const userAccess = authUser && (authUser.ptz_access || 'presets');
    const userCameras = authUser && Array.isArray(authUser.ptz_cameras) ? authUser.ptz_cameras : [];
    const userAllowedCamera = authUser && (
        ['superadmin', 'superuser'].includes(authUser.role) || userCameras.includes(id)
    );
    const go2rtc = camera.go2rtc || {};
    const capabilities = ptz.capabilities || {};
    const supportsPan = capabilities.pan !== false;
    const supportsTilt = capabilities.tilt !== false;
    const supportsZoom = capabilities.zoom !== false;
    const supportsFocus = capabilities.focus === true;
    const livePreviewUrl = go2rtcPreviewPlayerUrl(go2rtc);
    const previewUsesImage = /\/api\/stream\.mjpeg|\.mjpeg(?:\?|$)/.test(livePreviewUrl);
    const fullLivePlayerUrl = go2rtc.full_view_url || go2rtcFullPlayerUrl(go2rtc);
    const fullLivePageUrl = `/live.html?camera=${encodeURIComponent(id)}&stream=full`;
    const previewLivePageUrl = `/live.html?camera=${encodeURIComponent(id)}&stream=preview`;
    const liveIdleTimeoutS = Object.prototype.hasOwnProperty.call(go2rtc, 'idle_timeout_s')
        ? Number(go2rtc.idle_timeout_s)
        : 60;
    const liveIdleTimeoutMs = liveIdleTimeoutS > 0 ? liveIdleTimeoutS * 1000 : 0;
    let liveIdleTimer = null;
    let presetsLoaded = cachedPresets.length > 0;
    const userHasPtzAccess = ptz.enabled
        && userAllowedCamera
        && ['presets', 'manual', 'admin'].includes(userAccess);
    const canUsePresets = userHasPtzAccess && ptz.allow_presets;
    const canUseManual = ptz.enabled
        && ptz.allow_manual_control
        && userAllowedCamera
        && ['manual', 'admin'].includes(userAccess);
    const canUseTour = canUseManual && ptz.tour && ptz.tour.enabled;
    const canShowLivePreview = userHasPtzAccess;
    const canUseLivePreview = canShowLivePreview && Boolean(livePreviewUrl);
    if (!canUsePresets && !canUseManual && !canShowLivePreview) {
        wrapper.hidden = true;
        if (liveIdleTimer) {
            clearTimeout(liveIdleTimer);
            liveIdleTimer = null;
        }
        liveImage.removeAttribute('src');
        liveFrame.src = 'about:blank';
        liveFrame.hidden = true;
        liveFrame.classList.remove('loaded');
        livePreview.classList.remove('loaded');
        livePreview.hidden = false;
        return;
    }

    const populatePresets = discoveredPresets => {
        select.innerHTML = '';
        discoveredPresets.forEach(preset => {
            const option = document.createElement('option');
            option.value = preset.id;
            option.textContent = preset.name;
            select.appendChild(option);
        });
        select.disabled = discoveredPresets.length === 0;
        button.disabled = discoveredPresets.length === 0;
    };
    populatePresets(cachedPresets);
    wrapper.hidden = false;
    select.hidden = !canUsePresets;
    button.hidden = !canUsePresets;
    manual.hidden = !canUseManual;
    tourControls.hidden = !canUseTour;
    const applyTourStatus = tourStatus => {
        if (!canUseTour) {
            return;
        }
        const currentStatus = tourStatus || listItem._ptzTourStatus || {};
        const state = currentStatus.state || listItem._ptzTourState || 'unknown';
        const secondsUntilResume = Number(currentStatus.seconds_until_resume || 0);
        listItem._ptzTourState = state;
        listItem._ptzTourStatus = { ...currentStatus, state };
        const buttons = {
            start: tourControls.querySelector('[data-tour="start"]'),
            stop: tourControls.querySelector('[data-tour="stop"]'),
            pause: tourControls.querySelector('[data-tour="pause"]'),
            resume: tourControls.querySelector('[data-tour="resume"]')
        };
        Object.values(buttons).forEach(item => {
            if (item) {
                item.hidden = false;
                item.disabled = false;
            }
        });
        if (state === 'running') {
            if (buttons.start) buttons.start.hidden = true;
            if (buttons.resume) buttons.resume.hidden = true;
        } else if (state === 'paused') {
            if (buttons.start) buttons.start.hidden = true;
            if (buttons.pause) buttons.pause.hidden = true;
        } else if (state === 'stopped') {
            if (buttons.stop) buttons.stop.hidden = true;
            if (buttons.pause) buttons.pause.hidden = true;
            if (buttons.resume) buttons.resume.hidden = true;
        }
        if (buttons.resume) {
            const resumeLabel = secondsUntilResume > 0
                ? `Resume tour (${secondsUntilResume}s)`
                : 'Resume tour';
            buttons.resume.title = resumeLabel;
            buttons.resume.setAttribute('aria-label', resumeLabel);
        }
        if (buttons.start) buttons.start.title = 'Start tour';
        if (buttons.stop) buttons.stop.title = 'Stop tour';
        if (buttons.pause) buttons.pause.title = 'Pause tour';
    };
    applyTourStatus(null);
    const roseControls = manual.querySelector('.ptz-rose');
    const lensControls = manual.querySelector('.ptz-lens-controls');
    manual.querySelectorAll('[data-axis]').forEach(manualButton => {
        const axis = manualButton.dataset.axis;
        manualButton.hidden = (axis === 'pan' && !supportsPan)
            || (axis === 'tilt' && !supportsTilt)
            || (axis === 'zoom' && !supportsZoom)
            || (axis === 'focus' && !supportsFocus);
    });
    if (roseControls) {
        roseControls.hidden = !(supportsPan || supportsTilt);
    }
    if (lensControls) {
        lensControls.hidden = !Array.from(lensControls.querySelectorAll('button'))
            .some(lensButton => !lensButton.hidden);
    }
    manual.querySelectorAll('[data-stop]').forEach(manualButton => {
        manualButton.hidden = ptz.stop_disabled === true || !(supportsPan || supportsTilt);
    });
    liveView.hidden = !canShowLivePreview;
    liveView.classList.toggle('ptz-live-view-unavailable', canShowLivePreview && !livePreviewUrl);
    if (canUseLivePreview) {
        liveLink.href = fullLivePlayerUrl ? fullLivePageUrl : previewLivePageUrl;
        liveLink.hidden = false;
        if (!liveImage.src && liveFrame.src === 'about:blank') {
            livePlaceholder.textContent = 'Click to start low-resolution aiming stream';
        }
    } else {
        if (liveIdleTimer) {
            clearTimeout(liveIdleTimer);
            liveIdleTimer = null;
        }
        liveImage.removeAttribute('src');
        liveFrame.src = 'about:blank';
        liveFrame.hidden = true;
        liveFrame.classList.remove('loaded');
        livePreview.classList.remove('loaded');
        livePreview.hidden = false;
        livePlaceholder.textContent = 'Live preview is not configured. Enable RTSP live view and set an RTSP or PTZ live RTSP URL.';
        liveLink.hidden = true;
    }
    const unloadLiveView = () => {
        if (listItem._ptzLiveHeartbeatTimer) {
            clearInterval(listItem._ptzLiveHeartbeatTimer);
            listItem._ptzLiveHeartbeatTimer = null;
        }
        if (listItem._ptzLiveSessionId) {
            sendLiveViewHeartbeat(id, 'preview', listItem._ptzLiveSessionId, false)
                .catch(() => {});
            listItem._ptzLiveSessionId = '';
        }
        liveImage.removeAttribute('src');
        liveFrame.src = 'about:blank';
        liveFrame.hidden = true;
        liveFrame.classList.remove('loaded');
        livePreview.hidden = false;
        livePreview.classList.remove('loaded');
        livePlaceholder.textContent = 'Click to start low-resolution aiming stream';
        liveIdleTimer = null;
        if (status.textContent === 'Aiming stream loaded') {
            status.textContent = '';
        }
    };
    const startLiveHeartbeat = () => {
        if (!authToken) {
            return;
        }
        if (!listItem._ptzLiveSessionId) {
            listItem._ptzLiveSessionId = newClientSessionId();
        }
        sendLiveViewHeartbeat(id, 'preview', listItem._ptzLiveSessionId, true)
            .catch(error => {
                status.textContent = error.message;
            });
        if (!listItem._ptzLiveHeartbeatTimer) {
            listItem._ptzLiveHeartbeatTimer = setInterval(() => {
                if (!listItem._ptzLiveSessionId) {
                    return;
                }
                sendLiveViewHeartbeat(id, 'preview', listItem._ptzLiveSessionId, true)
                    .catch(error => {
                        status.textContent = error.message;
                    });
            }, 15000);
        }
    };
    const scheduleLiveViewUnload = () => {
        if (liveIdleTimer) {
            clearTimeout(liveIdleTimer);
            liveIdleTimer = null;
        }
        if (liveIdleTimeoutMs > 0) {
            liveIdleTimer = setTimeout(unloadLiveView, liveIdleTimeoutMs);
        }
    };
    const loadLiveView = () => {
        if (!canUseLivePreview) {
            status.textContent = 'Live preview is not configured';
            return;
        }
        if (canUseLivePreview && previewUsesImage && liveImage.src !== livePreviewUrl) {
            livePlaceholder.textContent = 'Loading low-resolution aiming stream...';
            liveImage.src = livePreviewUrl;
            status.textContent = 'Aiming stream loaded';
        }
        if (canUseLivePreview && !previewUsesImage && liveFrame.src !== livePreviewUrl) {
            livePreview.hidden = true;
            liveFrame.hidden = false;
            liveFrame.src = livePreviewUrl;
            liveFrame.classList.add('loaded');
            status.textContent = 'Aiming stream loaded';
        }
        if (canUseLivePreview) {
            startLiveHeartbeat();
            scheduleLiveViewUnload();
        }
    };
    liveImage.onload = () => {
        livePreview.classList.add('loaded');
        scheduleLiveViewUnload();
    };
    liveImage.onerror = () => {
        livePreview.classList.remove('loaded');
        livePlaceholder.textContent = 'Aiming stream failed. Open high-resolution stream.';
        status.textContent = 'Aiming stream failed';
    };
    livePreview.onclick = loadLiveView;
    liveView.onclick = event => {
        if (event.target === liveView) {
            loadLiveView();
        }
    };
    if (canUsePresets && !presetsLoaded && !listItem._ptzPresetDiscoveryAttempted) {
        listItem._ptzPresetDiscoveryAttempted = true;
        select.innerHTML = '<option value="">Loading presets...</option>';
        select.disabled = true;
        button.disabled = true;
        fetch(`/api/ptz/presets?camera=${encodeURIComponent(id)}`, {
            headers: authHeaders()
        })
            .then(async response => {
                const result = await response.json();
                if (!response.ok) {
                    throw new Error(result.error || `Preset discovery failed: ${response.status}`);
                }
                presetsLoaded = true;
                listItem._ptzDiscoveredPresets = Array.isArray(result.presets) ? result.presets : [];
                populatePresets(listItem._ptzDiscoveredPresets);
                if (!select.options.length) {
                    select.innerHTML = '<option value="">No presets found</option>';
                    select.disabled = true;
                    button.disabled = true;
                }
            })
            .catch(error => {
                select.innerHTML = '<option value="">Preset discovery failed</option>';
                select.disabled = true;
                button.disabled = true;
                status.textContent = error.message;
            });
    }
    button.onclick = async () => {
        if (!select.value) {
            return;
        }
        const previousImageUrl = listItem.dataset.lastPictureUrl || '';
        button.disabled = true;
        status.textContent = 'Moving...';
        try {
            const response = await fetch('/api/ptz/preset', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', ...authHeaders() },
                body: JSON.stringify({ camera: id, preset: select.value })
            });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.error || `PTZ request failed: ${response.status}`);
            }
            status.textContent = result.session && result.session.seconds_remaining
                ? `${result.session.seconds_remaining}s`
                : 'Done';
            if (result.tour_status) {
                applyTourStatus(result.tour_status);
            }
            if (result.capture && result.capture.requested) {
                status.textContent = 'Capturing new snapshot...';
                pollCameraSnapshotRefresh(camera, listItem, previousImageUrl);
            }
        } catch (error) {
            status.textContent = error.message;
        } finally {
            button.disabled = false;
        }
    };
    manual.querySelectorAll('button').forEach(manualButton => {
        manualButton.onclick = async () => {
            loadLiveView();
            manual.querySelectorAll('button').forEach(item => { item.disabled = true; });
            status.textContent = 'Moving...';
            try {
                const isStop = manualButton.dataset.stop === '1';
                const isFocus = manualButton.dataset.axis === 'focus';
                const endpoint = isStop ? '/api/ptz/stop' : (isFocus ? '/api/ptz/focus' : '/api/ptz/move');
                status.textContent = isFocus ? 'Focusing...' : status.textContent;
                const payload = {
                    camera: id,
                    speed: Number(speedInput.value || 0.35),
                    move_duration_ms: Number(durationSelect.value || 250)
                };
                if (isFocus) {
                    payload.focus = Number(manualButton.dataset.focus || 0);
                } else {
                    payload.pan = Number(manualButton.dataset.pan || 0);
                    payload.tilt = Number(manualButton.dataset.tilt || 0);
                    payload.zoom = Number(manualButton.dataset.zoom || 0);
                }
                const response = await fetch(endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', ...authHeaders() },
                    body: JSON.stringify(payload)
                });
                const result = await response.json();
                if (!response.ok) {
                    throw new Error(result.error || `PTZ request failed: ${response.status}`);
                }
                status.textContent = isStop ? 'Stopped' : (isFocus ? 'Focused' : 'Nudged');
                if (result.tour_status) {
                    applyTourStatus(result.tour_status);
                }
            } catch (error) {
                status.textContent = error.message;
            } finally {
                manual.querySelectorAll('button').forEach(item => { item.disabled = false; });
            }
        };
    });
    tourControls.querySelectorAll('button').forEach(tourButton => {
        tourButton.onclick = async () => {
            tourControls.querySelectorAll('button').forEach(item => { item.disabled = true; });
            const action = tourButton.dataset.tour || 'pause';
            const actionProgress = {
                start: 'Starting tour...',
                stop: 'Stopping tour...',
                pause: 'Pausing tour...',
                resume: 'Resuming tour...'
            };
            status.textContent = actionProgress[action] || 'Updating tour...';
            try {
                const response = await fetch('/api/ptz/tour', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', ...authHeaders() },
                    body: JSON.stringify({ camera: id, action })
                });
                const result = await response.json();
                if (!response.ok) {
                    throw new Error(result.error || `Tour request failed: ${response.status}`);
                }
                if (action === 'pause' && result.auto_resume_s > 0) {
                    status.textContent = `Tour paused; resumes in ${result.auto_resume_s}s`;
                } else {
                    const actionDone = {
                        start: 'Tour started',
                        stop: 'Tour stopped',
                        pause: 'Tour paused',
                        resume: 'Tour resumed'
                    };
                    status.textContent = actionDone[action] || 'Tour updated';
                }
                applyTourStatus(result.tour_status || {
                    state: action === 'stop' ? 'stopped' : (action === 'pause' ? 'paused' : 'running'),
                    seconds_until_resume: action === 'pause' ? Number(result.auto_resume_s || 0) : 0
                });
            } catch (error) {
                status.textContent = error.message;
            } finally {
                if (listItem._ptzTourState) {
                    applyTourStatus(null);
                } else {
                    tourControls.querySelectorAll('button').forEach(item => { item.disabled = false; });
                }
            }
        };
    });
}

function updateCamera(camera, cameraData) {
    const id = cameraId(camera);
    const displayName = cameraDisplayName(camera);
    let listItem = Array.from(cameraListElement.querySelectorAll('li[data-camera-id]'))
        .find(item => item.dataset.cameraId === id);

    if (!listItem) {
        listItem = createCameraListItem(camera);
        cameraListElement.appendChild(listItem);
    }

    const thumbImg = listItem.querySelector('.camera-header img');
    const cameraNameElement = listItem.querySelector('.camera-name');
    const cameraDescription = listItem.querySelector('.camera-description');
    const lastPictureTime = listItem.querySelector('.last-picture-time');
    const status = listItem.querySelector('.status');
    const detailsImg = listItem.querySelector('.camera-details img');
    const linkTimelapseToday = listItem.querySelector('.link-timelapse-today');
    const timelapseArchiveSelect = listItem.querySelector('.select-timelapse-archive');
    const today = new Date();
    const todayStr = formatDate(today);
    const timelapseEnabled = camera.timelapse_enabled !== false;

    cameraNameElement.textContent = displayName;
    thumbImg.alt = `${displayName} thumbnail`;
    detailsImg.alt = `Full image for ${displayName}`;

    if (camera.description) {
        cameraDescription.textContent = camera.description;
        cameraDescription.style.display = 'block';
    } else {
        cameraDescription.textContent = '';
        cameraDescription.style.display = 'none';
    }
    configurePtzPresets(camera, listItem);
    timelapseArchiveSelect.onchange = () => {
        const selectedOption = timelapseArchiveSelect.selectedOptions[0];
        if (!selectedOption || !selectedOption.value) {
            return;
        }
        const title = `${displayName} ${selectedOption.dataset.date} Timelapse`;
        const destination = selectedOption.dataset.format === 'm3u8'
            ? buildTimelapsePlayerUrl(selectedOption.value, title)
            : selectedOption.value;
        window.open(destination, '_blank');
        timelapseArchiveSelect.value = '';
    };

    if (!timelapseEnabled) {
        linkTimelapseToday.style.display = 'none';
        timelapseArchiveSelect.style.display = 'none';
    } else {
        configureTodayTimelapseLink(linkTimelapseToday, camera, todayStr, cameraData);
        updateTimelapseArchiveSelect(camera, timelapseArchiveSelect, todayStr);
    }

    refreshCameraMetadata(camera, listItem)
        .catch(error => {
            lastPictureTime.textContent = 'Error loading metadata';
            status.className = 'status offline';
            console.error(`Failed to load metadata for ${id}:`, error);
        });
}

function updateAllCameras() {
    updatePrivateLanding();
    if (!siteIsPublic && !authUser) {
        return;
    }
    fetch('/api/cameras', { headers: authHeaders() })
        .then(async response => {
            const data = await response.json();
            if (!response.ok) {
                if (response.status === 401) {
                    siteIsPublic = data.public_site !== false;
                    setDeploymentName(data.deployment_name || deploymentName);
                    authUser = null;
                    storeAuthToken('');
                    syncLoginUi();
                    return null;
                }
                throw new Error(data.error || `Failed to load cameras: ${response.status}`);
            }
            return data;
        })
        .then(data => {
            if (!data) {
                return;
            }
            setDeploymentName(data.global.deployment_name || deploymentName);
            const uiConfig = (data.global && data.global.ui) || {};
            siteIsPublic = uiConfig.public_site !== false;
            updateHeaderLinks(uiConfig);

            const cameras = data.cameras || [];
            const visibleCameraIds = new Set(cameras.map(camera => cameraId(camera)));
            cameraListElement.querySelectorAll('li[data-camera-id]').forEach(item => {
                if (!visibleCameraIds.has(item.dataset.cameraId)) {
                    item.remove();
                }
            });
            updateCameraMap(cameras);
            if (!mapVisibilityInitialized) {
                const showMapByDefault = Boolean(uiConfig.show_map_by_default);
                setMapVisible(showMapByDefault);
                mapVisibilityInitialized = true;
            }
            updatePrivateLanding();
            cameras.forEach(camera => updateCamera(camera, data));
        })
        .catch(error => {
            console.error('Error loading cameras:', error);
        });
}

loadAuthStatus().then(updateAllCameras);
setInterval(updateAllCameras, 60000);
