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

async function fetchCameraTimelapses(cameraName) {
    const response = await fetch(`/api/timelapses?camera=${encodeURIComponent(cameraName)}`);
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
    const photoDir = `/photos/${camera.title}`;
    const startOfDay = new Date(new Date().getFullYear(), new Date().getMonth(), new Date().getDate());
    const minutesElapsed = (new Date() - startOfDay) / 60000;
    const cacheBuster = Math.floor(minutesElapsed / 20);
    const url = `${photoDir}/${dateString}/${dateString}.${frequentTimelapseExtension}?v=${cacheBuster}`;

    if (frequentTimelapseExtension === 'm3u8') {
        link.href = buildTimelapsePlayerUrl(
            url,
            `${camera.title} ${dateString} Frequent Timelapse`
        );
    } else {
        link.href = url;
    }
    link.style.display = 'inline-block';
}

function populateTimelapseArchive(select, timelapses, todayStr) {
    const archiveItems = timelapses
        .filter(item => item.date !== todayStr && item.format !== 'm3u8')
        .filter(item => item.type === 'daily' || item.type === 'timelapse');
    select.innerHTML = '';

    if (archiveItems.length === 0) {
        select.style.display = 'none';
        return;
    }

    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Timelapse archive';
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
                <select class="select-timelapse-archive" aria-label="Timelapse archive"></select>
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
    const timelapseArchiveSelect = listItem.querySelector('.select-timelapse-archive');
    const linkHistory = listItem.querySelector('.link-history');

    if (camera.description) {
        cameraDescription.textContent = camera.description;
        cameraDescription.style.display = 'block';
    } else {
        cameraDescription.textContent = '';
        cameraDescription.style.display = 'none';
    }
    timelapseArchiveSelect.onchange = () => {
        const selectedOption = timelapseArchiveSelect.selectedOptions[0];
        if (!selectedOption || !selectedOption.value) {
            return;
        }
        const title = `${camera.title} ${selectedOption.dataset.date} Timelapse`;
        const destination = selectedOption.dataset.format === 'm3u8'
            ? buildTimelapsePlayerUrl(selectedOption.value, title)
            : selectedOption.value;
        window.open(destination, '_blank');
        timelapseArchiveSelect.value = '';
    };

    fetch(camera.dynamic_metadata)
        .then(response => response.ok ? response.json() : Promise.reject('Network response was not ok.'))
        .then(async metadata => {
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

            const todayStr = formatDate(today);
            const photo_dir = `/photos/${camera.title}`;

            const fullscreenUrl = `fullscreen.html?camera=${encodeURIComponent(camera.title)}`;
            linkFullscreen.href = fullscreenUrl;
            fullscreenImageLink.href = fullscreenUrl;
            linkToday.href = `${photo_dir}/${todayStr}/`;
            linkHistory.href = `${photo_dir}/daylight.html`;

            const timelapseEnabled = camera.timelapse_enabled !== false;

            if (!timelapseEnabled) {
                linkTimelapseToday.style.display = 'none';
                timelapseArchiveSelect.style.display = 'none';
            } else {
                configureTodayTimelapseLink(linkTimelapseToday, camera, todayStr, cameraData);
                try {
                    const timelapseData = await fetchCameraTimelapses(camera.title);
                    const timelapses = timelapseData.timelapses || [];
                    populateTimelapseArchive(timelapseArchiveSelect, timelapses, todayStr);
                } catch (error) {
                    timelapseArchiveSelect.style.display = 'none';
                    console.error(`Failed to load timelapse archive for ${camera.title}:`, error);
                }
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
