document.addEventListener('DOMContentLoaded', () => {
    const refreshBtn = document.getElementById('refreshLaunchDashboardBtn');
    const status = document.getElementById('launchDashboardStatus');
    const dashboard = document.getElementById('launchDashboard');

    refreshBtn.addEventListener('click', loadLaunchDashboard);
    loadLaunchDashboard();

    function escapeHtml(value) {
        return String(value || '').replace(/[&<>"']/g, char => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[char]));
    }

    function setStatus(message, type = '') {
        status.textContent = message;
        status.className = type;
    }

    function formatLaunchTime(value) {
        if (!value) {
            return 'Unknown time';
        }
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) {
            return value;
        }
        return date.toLocaleString([], {
            weekday: 'short',
            month: 'short',
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit',
            timeZoneName: 'short'
        });
    }

    function renderCamera(camera) {
        const parts = [];
        if (camera.preset) {
            parts.push(`preset ${camera.preset}`);
        }
        if (camera.image_profile) {
            parts.push(`image ${camera.image_profile}`);
        }
        if (camera.pause_tour) {
            parts.push('tour pause');
        }
        if (camera.record) {
            parts.push('record');
        }
        if (camera.skip_when_full_viewers) {
            parts.push('keep HD viewers');
        }
        const detail = parts.length ? parts.join(' · ') : 'listed only';
        const path = camera.download_path
            ? `<small>Recording path: ${escapeHtml(camera.download_path)}</small>`
            : '';
        return `
            <div class="launch-camera-pill">
                <strong>${escapeHtml(camera.name)}</strong>
                <span>${escapeHtml(detail)}</span>
                ${path}
            </div>
        `;
    }

    function renderDashboard(data) {
        dashboard.innerHTML = '';
        if (!data.enabled) {
            dashboard.innerHTML = '<div class="launch-empty">Launch automation is disabled for this deployment.</div>';
            return;
        }
        const events = data.events || [];
        if (!events.length) {
            dashboard.innerHTML = '<div class="launch-empty">No upcoming launches matched the configured plans.</div>';
            return;
        }
        const cards = events.map(event => {
            const meta = [event.provider, event.location, event.pad].filter(Boolean).join(' - ');
            const plans = (event.plans || []).map(plan => {
                const cameras = (plan.camera_details || []).map(renderCamera).join('')
                    || '<div class="launch-empty">No launch cameras selected.</div>';
                return `
                    <div class="launch-dashboard-plan">
                        <div class="launch-plan-title">
                            <strong>${escapeHtml(plan.id)}</strong>
                            <span class="launch-preview-badge">${escapeHtml(plan.phase)}</span>
                        </div>
                        <div class="launch-dashboard-cameras">${cameras}</div>
                    </div>
                `;
            }).join('');
            return `
                <article class="launch-event-card">
                    <div class="launch-event-header">
                        <h2>${escapeHtml(event.name)}</h2>
                        <span>${escapeHtml(formatLaunchTime(event.launch_time_utc))}</span>
                    </div>
                    <small>${escapeHtml(meta)}</small>
                    ${plans || '<div class="launch-empty">No matching plans.</div>'}
                </article>
            `;
        }).join('');
        dashboard.innerHTML = cards;
    }

    async function loadLaunchDashboard() {
        refreshBtn.disabled = true;
        setStatus('Loading launch dashboard...', 'info');
        try {
            const response = await fetch('/api/launches/preview');
            const data = await response.json();
            if (!response.ok || !data.ok) {
                throw new Error(data.error || `HTTP error! status: ${response.status}`);
            }
            renderDashboard(data);
            setStatus(`Updated ${formatLaunchTime(data.now)}.`, 'success');
        } catch (error) {
            dashboard.textContent = error.message;
            setStatus(`Launch dashboard unavailable: ${error.message}`, 'error');
        } finally {
            refreshBtn.disabled = false;
        }
    }
});
