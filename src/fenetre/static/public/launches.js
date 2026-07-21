(function () {
    const body = document.body;
    const pageTitle = document.getElementById('page-title');
    const pageSubtitle = document.getElementById('page-subtitle');
    const refreshButton = document.getElementById('refresh-button');
    const loginToggle = document.getElementById('login-toggle');
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
    const dashboardPanel = document.getElementById('dashboard-panel');
    const dashboard = document.getElementById('dashboard');
    const dashboardStatus = document.getElementById('dashboard-status');

    let authToken = localStorage.getItem('fenetreAuthToken') || '';
    let authUser = null;
    let siteIsPublic = true;
    let deploymentName = 'Fenetre';

    if (localStorage.getItem('theme') === 'dark') {
        body.classList.add('dark-mode');
    }

    function authHeaders() {
        return authToken ? { Authorization: `Bearer ${authToken}` } : {};
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

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, char => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[char]));
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

    function formatDuration(seconds) {
        const value = Math.max(0, Number(seconds) || 0);
        if (value >= 3600 && value % 3600 === 0) {
            return `${value / 3600}h`;
        }
        if (value >= 60 && value % 60 === 0) {
            return `${value / 60}m`;
        }
        return `${value}s`;
    }

    function setDeploymentName(name) {
        deploymentName = name || deploymentName;
        pageTitle.textContent = `${deploymentName} Launches`;
        privateLandingName.textContent = deploymentName;
        document.title = `${deploymentName} Launches`;
    }

    function updatePrivateLanding() {
        const locked = !siteIsPublic && !authUser;
        privateLanding.hidden = !locked;
        dashboardPanel.hidden = locked;
        refreshButton.disabled = locked;
        if (locked) {
            dashboard.innerHTML = '';
            dashboardStatus.textContent = 'Login required';
        }
    }

    function syncLoginUi() {
        loginToggle.textContent = authUser ? authUser.username : 'Login';
        loginSubmit.hidden = Boolean(authUser);
        changePasswordToggle.hidden = !authUser;
        logoutSubmit.hidden = !authUser;
        loginUsername.hidden = Boolean(authUser);
        loginPassword.hidden = Boolean(authUser);
        loginStatus.textContent = authUser ? `${authUser.role || 'viewer'} access` : '';
        if (!authUser) {
            passwordPanel.hidden = true;
        }
        updatePrivateLanding();
    }

    async function loadAuthStatus() {
        try {
            const response = await fetch('/api/auth/status', { headers: authHeaders() });
            const data = await response.json();
            siteIsPublic = data.public_site !== false;
            setDeploymentName(data.deployment_name || deploymentName);
            authUser = data.authenticated ? data.user : null;
            if (!authUser) {
                storeAuthToken('');
            }
        } catch (error) {
            authUser = null;
        }
        syncLoginUi();
    }

    function openLoginPanel() {
        if (authUser) {
            loginPanel.hidden = !loginPanel.hidden;
            return;
        }
        loginPanel.hidden = false;
        loginStatus.textContent = '';
        window.setTimeout(() => loginUsername.focus(), 0);
    }

    async function loginWithJsonCredentials() {
        loginStatus.textContent = 'Signing in...';
        try {
            const response = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username: loginUsername.value.trim(),
                    password: loginPassword.value
                })
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || `Login failed: ${response.status}`);
            }
            storeAuthToken(data.token);
            authUser = data.user;
            loginPassword.value = '';
            syncLoginUi();
            await loadLaunchDashboard();
        } catch (error) {
            loginStatus.textContent = error.message;
        }
    }

    async function logout() {
        if (authToken) {
            await fetch('/api/auth/logout', { method: 'POST', headers: authHeaders() }).catch(() => {});
        }
        storeAuthToken('');
        authUser = null;
        syncLoginUi();
        await loadLaunchDashboard();
    }

    function clearPasswordForm() {
        currentPassword.value = '';
        newPassword.value = '';
        confirmPassword.value = '';
        passwordStatus.textContent = '';
    }

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
            clearPasswordForm();
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
            parts.push('keep active HD viewers');
        }
        const detail = parts.length ? parts.join(' | ') : 'listed only';
        const path = camera.download_path
            ? `<small>Recording path: ${escapeHtml(camera.download_path)}</small>`
            : '';
        return `
            <div class="launch-camera-card">
                <strong>${escapeHtml(camera.name)}</strong>
                <span>${escapeHtml(detail)}</span>
                ${path}
            </div>
        `;
    }

    function renderPlan(plan) {
        const cameraDetails = Array.isArray(plan.camera_details) ? plan.camera_details : [];
        const cameras = cameraDetails.map(renderCamera).join('')
            || '<div class="launch-empty">No visible launch cameras for this plan.</div>';
        return `
            <section class="launch-plan">
                <div class="launch-plan-title">
                    <strong>${escapeHtml(plan.id)}</strong>
                    <span class="phase-badge">${escapeHtml(plan.phase || 'pending')}</span>
                </div>
                <div class="launch-plan-timing">Start ${escapeHtml(formatDuration(plan.pre_seconds))} before launch, stop ${escapeHtml(formatDuration(plan.post_seconds))} after launch.</div>
                <div class="launch-camera-grid">${cameras}</div>
            </section>
        `;
    }

    function renderLaunchEvent(event) {
        const meta = [event.provider, event.location, event.pad].filter(Boolean).join(' - ');
        const status = event.status ? `<span>${escapeHtml(event.status)}</span>` : '';
        const plans = (event.plans || []).map(renderPlan).join('')
            || '<div class="launch-empty">No matching plans for this launch.</div>';
        return `
            <article class="launch-event-card">
                <div class="launch-event-header">
                    <h3>${escapeHtml(event.name)}</h3>
                    <div class="launch-time">${escapeHtml(formatLaunchTime(event.launch_time_utc))}</div>
                </div>
                <div class="launch-meta">${escapeHtml(meta || 'Location not specified')} ${status}</div>
                ${plans}
            </article>
        `;
    }

    function renderDashboard(data) {
        if (!data.enabled) {
            pageSubtitle.textContent = 'Launch automation is disabled for this deployment';
            dashboard.innerHTML = '<div class="launch-empty">Launch automation is disabled for this deployment.</div>';
            return;
        }
        pageSubtitle.textContent = 'Upcoming launches and configured cameras';
        const events = Array.isArray(data.events) ? data.events : [];
        if (!events.length) {
            dashboard.innerHTML = '<div class="launch-empty">No upcoming launches matched the configured plans.</div>';
            return;
        }
        dashboard.innerHTML = events.map(renderLaunchEvent).join('');
    }

    async function loadLaunchDashboard() {
        if (!siteIsPublic && !authUser) {
            updatePrivateLanding();
            return;
        }
        refreshButton.disabled = true;
        dashboardStatus.textContent = 'Loading...';
        try {
            const response = await fetch('/api/launches/preview', { headers: authHeaders() });
            const data = await response.json();
            if (response.status === 401) {
                siteIsPublic = false;
                authUser = null;
                storeAuthToken('');
                syncLoginUi();
                return;
            }
            if (!response.ok || !data.ok) {
                throw new Error(data.error || `Launch dashboard failed: ${response.status}`);
            }
            renderDashboard(data);
            dashboardStatus.textContent = `Updated ${formatLaunchTime(data.now)}`;
        } catch (error) {
            dashboard.innerHTML = `<div class="launch-empty">${escapeHtml(error.message)}</div>`;
            dashboardStatus.textContent = 'Unavailable';
        } finally {
            refreshButton.disabled = !siteIsPublic && !authUser;
        }
    }

    loginToggle.addEventListener('click', openLoginPanel);
    privateLoginButton.addEventListener('click', openLoginPanel);
    loginSubmit.addEventListener('click', loginWithJsonCredentials);
    logoutSubmit.addEventListener('click', logout);
    changePasswordToggle.addEventListener('click', () => {
        passwordPanel.hidden = !passwordPanel.hidden;
        passwordStatus.textContent = '';
        if (!passwordPanel.hidden) {
            currentPassword.focus();
        }
    });
    passwordCancel.addEventListener('click', () => {
        clearPasswordForm();
        passwordPanel.hidden = true;
    });
    passwordSubmit.addEventListener('click', changeOwnPassword);
    refreshButton.addEventListener('click', loadLaunchDashboard);
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
    [currentPassword, newPassword, confirmPassword].forEach(input => {
        input.addEventListener('keydown', event => {
            if (event.key === 'Enter') {
                changeOwnPassword();
            }
        });
    });

    loadAuthStatus()
        .then(loadLaunchDashboard)
        .catch(error => {
            dashboard.innerHTML = `<div class="launch-empty">${escapeHtml(error.message)}</div>`;
            dashboardStatus.textContent = 'Unavailable';
        });
})();
