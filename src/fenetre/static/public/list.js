const themeToggle = document.getElementById('theme-toggle');
const body = document.body;
const mapToggleButton = document.getElementById('map-toggle');

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
    return `<b>${escapeHtml(camera.title)}</b>${description}`;
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

function createCameraListItem(camera) {
    const listItem = document.createElement('li');
    listItem.className = 'camera-item';
    listItem.dataset.title = camera.title;

    listItem.innerHTML = `
        <div class="camera-header">
            <img src="" alt="${camera.title} thumbnail">
            <div class="camera-info">
                <div class="camera-name">${camera.title}</div>
                <div class="camera-description"></div>
                <div class="last-picture-time">Loading...</div>
                <div class="camera-metadata"></div>
            </div>
            <div class="status"></div>
        </div>
        <div class="camera-details">
            <a class="fullscreen-image-link" href="#" target="_blank">
                <img src="" alt="Full image for ${camera.title}">
            </a>
            <a class="filename" href="#" download></a>
            <div class="links">
                <a class="link-fullscreen" href="#" target="_blank">Fullscreen</a>
                <a class="link-today" href="#" target="_blank">Today's Pictures</a>
                <a class="link-timelapse-today" href="#" target="_blank">Today's Timelapse</a>
                <a class="link-timelapse" href="#" target="_blank">Yesterday's Timelapse</a>
                <a class="link-history" href="#" target="_blank">History</a>
            </div>
        </div>
    `;

    listItem.querySelector('.camera-header').addEventListener('click', () => {
        const details = listItem.querySelector('.camera-details');
        details.classList.toggle('active');
    });

    return listItem;
}

function updateCamera(camera, cameraData) {
    let listItem = document.querySelector(`li[data-title="${camera.title}"]`);

    if (!listItem) {
        listItem = createCameraListItem(camera);
        cameraListElement.appendChild(listItem);
    }

    const thumbImg = listItem.querySelector('.camera-header img');
    const cameraDescription = listItem.querySelector('.camera-description');
    const lastPictureTime = listItem.querySelector('.last-picture-time');
    const cameraMetadata = listItem.querySelector('.camera-metadata');
    const status = listItem.querySelector('.status');
    const detailsImg = listItem.querySelector('.camera-details img');
    const fullscreenImageLink = listItem.querySelector('.fullscreen-image-link');
    const filenameLink = listItem.querySelector('.camera-details .filename');
    const linkFullscreen = listItem.querySelector('.link-fullscreen');
    const linkToday = listItem.querySelector('.link-today');
    const linkTimelapseToday = listItem.querySelector('.link-timelapse-today');
    const linkTimelapse = listItem.querySelector('.link-timelapse');
    const linkHistory = listItem.querySelector('.link-history');

    if (camera.description) {
        cameraDescription.textContent = camera.description;
        cameraDescription.style.display = 'block';
    } else {
        cameraDescription.textContent = '';
        cameraDescription.style.display = 'none';
    }

    fetch(camera.dynamic_metadata)
        .then(response => response.ok ? response.json() : Promise.reject('Network response was not ok.'))
        .then(metadata => {
            const lastPictureUrl = metadata.last_picture_url;
            if (!lastPictureUrl) {
                lastPictureTime.textContent = 'No picture available';
                status.className = 'status offline';
                return;
            }

            const basePath = camera.dynamic_metadata.substring(0, camera.dynamic_metadata.lastIndexOf('/'));
            const fullImageUrl = `/${basePath}/${lastPictureUrl}`;
            const filename = lastPictureUrl.substring(lastPictureUrl.lastIndexOf('/') + 1);

            thumbImg.src = fullImageUrl;
            detailsImg.src = fullImageUrl;
            filenameLink.textContent = `Download: ${filename}`;
            filenameLink.href = fullImageUrl;

            const imageDate = parseTimestampFromFilename(filename);
            if (imageDate) {
                lastPictureTime.textContent = `Last picture: ${imageDate.toLocaleString()}`;
                status.className = `status ${(new Date() - imageDate) < 180000 ? 'online' : 'offline'}`;
            }

            if (metadata.iso || metadata.shutter_speed) {
                cameraMetadata.textContent = `ISO ${metadata.iso || '?'} | ${metadata.shutter_speed || '?'}`;
            }

            const today = new Date();
            const yesterday = new Date(today);
            yesterday.setDate(today.getDate() - 1);

            const todayStr = formatDate(today);
            const yesterdayStr = formatDate(yesterday);
            const photo_dir = `/photos/${camera.title}`;

            const fullscreenUrl = `fullscreen.html?camera=${encodeURIComponent(camera.title)}`;
            linkFullscreen.href = fullscreenUrl;
            fullscreenImageLink.href = fullscreenUrl;
            linkToday.href = `${photo_dir}/${todayStr}/`;
            linkHistory.href = `${photo_dir}/daylight.html`;

            const timelapseEnabled = camera.timelapse_enabled !== false;

            if (!timelapseEnabled) {
                linkTimelapseToday.style.display = 'none';
                linkTimelapse.style.display = 'none';
            } else {
                const timelapseExtension = cameraData.global.timelapse_file_extension || 'webm';
                const frequentTimelapseExtension = cameraData.global.frequent_timelapse_file_extension || 'mp4';

                const startOfDay = new Date(today.getFullYear(), today.getMonth(), today.getDate());
                const minutesElapsed = (today - startOfDay) / 60000;
                const cacheBuster = Math.floor(minutesElapsed / 20);

                const frequentTimelapseUrl = `${photo_dir}/${todayStr}/${todayStr}.${frequentTimelapseExtension}?v=${cacheBuster}`;

                if (frequentTimelapseExtension === 'm3u8') {
                    linkTimelapseToday.href = buildTimelapsePlayerUrl(
                        frequentTimelapseUrl,
                        `${camera.title} ${todayStr} Frequent Timelapse`
                    );
                } else {
                    linkTimelapseToday.href = frequentTimelapseUrl;
                }

                linkTimelapseToday.style.display = 'inline-block';
                linkTimelapse.href = `${photo_dir}/${yesterdayStr}/${yesterdayStr}.${timelapseExtension}`;
                linkTimelapse.style.display = 'inline-block';
            }
        })
        .catch(error => {
            lastPictureTime.textContent = 'Error loading metadata';
            status.className = 'status offline';
            console.error(`Failed to load metadata for ${camera.title}:`, error);
        });
}

function updateAllCameras() {
    fetch('/cameras.json')
        .then(response => response.json())
        .then(data => {
            const deploymentName = data.global.deployment_name || 'AREDN805';
            document.querySelector('#list-header h1').textContent = `${deploymentName} Cameras`;

            const cameras = data.cameras || [];
            cameras.forEach(camera => updateCamera(camera, data));
        })
        .catch(error => {
            console.error('Error loading cameras:', error);
        });
}

updateAllCameras();
setInterval(updateAllCameras, 60000);
