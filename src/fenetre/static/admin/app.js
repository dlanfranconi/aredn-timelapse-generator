document.addEventListener('DOMContentLoaded', () => {
    // Define structures for postprocessing steps
    const postprocessingTypes = {
        crop: {
            fields: { area: { type: 'text', default: '0,0,1920,1080' } },
            order: ['area']
        },
        resize: {
            fields: {
                width: { type: 'number', default: 1280 },
                height: { type: 'number', default: 720 }
            },
            order: ['width', 'height']
        },
        awb: { // Auto White Balance
            fields: {}, // No specific fields, the type itself is the config
            order: []
        },
        timestamp: {
            fields: {
                enabled: { type: 'checkbox', default: true },
                position: { type: 'text', default: 'bottom_right' }, // Could be a select if predefined options
                size: { type: 'number', default: 24 },
                color: { type: 'text', default: 'white' }, // Could be a color picker or select
                format: { type: 'text', default: '%Y-%m-%d %H:%M:%S %Z' }
            },
            order: ['enabled', 'position', 'size', 'color', 'format']
        }
        // Add other postprocessing types here as needed
    };
    const availablePostprocessingTypes = Object.keys(postprocessingTypes);

    // Define structures for camera configurations
    const cameraSourceTypes = {
        url: {
            fields: { url: { type: 'text', default: 'http://example.com/image.jpg' } },
            order: ['url']
        },
        local_command: {
            fields: { local_command: { type: 'text', default: 'ffmpeg -i http://source/stream -vframes 1 -q:v 2 -f singlejpeg -' } },
            order: ['local_command']
        },
        gopro_ip: {
            fields: {
                gopro_model: { type: 'text', default: 'hero11' },
                gopro_ip: { type: 'text', default: '10.5.5.9' },
                gopro_ble_identifier: { type: 'text', default: 'XXXX' },
                gopro_root_ca: { type: 'textarea', default: '-----BEGIN CERTIFICATE-----\nPASTE_CA_HERE\n-----END CERTIFICATE-----' },
                gopro_utility_poll_interval_s: { type: 'number', default: 10 },
                gopro_bluetooth_retry_delay_s: { type: 'number', default: 180 }
            },
            order: ['gopro_model', 'gopro_ip', 'gopro_ble_identifier', 'gopro_root_ca', 'gopro_utility_poll_interval_s', 'gopro_bluetooth_retry_delay_s']
        }
    };
    const availableCameraSourceTypes = Object.keys(cameraSourceTypes);

    const commonCameraFields = {
        description: { type: 'text', default: 'A new camera' },
        snap_interval_s: { type: 'number', default: 60 },
        activity_interval_s: { type: 'number', default: 10 },
        timeout_s: { type: 'number', default: 60 },
        sky_area: { type: 'text', default: '0,0,1920,500' }, // Example, might need better default or placeholder
        ssim_area: { type: 'text', default: '0,0,1920,1080' },
        ssim_setpoint: { type: 'number', default: 0.90, step: 0.01 }, // For float input
        disabled: { type: 'checkbox', default: false },
        public: { type: 'checkbox', default: true },
        mozjpeg_optimize: { type: 'checkbox', default: false },
        postprocessing: { type: 'array', default: [] } // Special handling: this will use the postprocessing logic
    };
    // Order for common fields (can be refined)
    const commonCameraFieldsOrder = [
        'description', 'snap_interval_s', 'activity_interval_s', 'timeout_s', 'sky_area', 'ssim_area', 'ssim_setpoint',
        'disabled', 'public', 'mozjpeg_optimize', 'postprocessing'
    ];

    // NOTE: The duplicate declaration of commonCameraFieldsOrder that was here has been removed.

    const loadConfigBtn = document.getElementById('loadConfigBtn');
    const saveConfigBtn = document.getElementById('saveConfigBtn');
    const reloadAppBtn = document.getElementById('reloadAppBtn');
    const rebuildCamerasBtn = document.getElementById('rebuildCamerasBtn');
    const syncUiBtn = document.getElementById('syncUiBtn');
    const manageUsersBtn = document.getElementById('manageUsersBtn');
    const addCameraBtn = document.getElementById('addCameraBtn');
    const editCameraBtn = document.getElementById('editCameraBtn');
    const editCameraSelect = document.getElementById('editCameraSelect');
    const refreshStorageBtn = document.getElementById('refreshStorageBtn');
    const storageSummary = document.getElementById('storageSummary');
    const addCameraModal = document.getElementById('addCameraModal');
    const cameraModalTitle = document.getElementById('cameraModalTitle');
    const closeAddCameraModalBtn = document.getElementById('closeAddCameraModalBtn');
    const userModal = document.getElementById('userModal');
    const closeUserModalBtn = document.getElementById('closeUserModalBtn');
    const userList = document.getElementById('userList');
    const userForm = document.getElementById('userForm');
    const newUserBtn = document.getElementById('newUserBtn');
    const saveUserBtn = document.getElementById('saveUserBtn');
    const deleteUserBtn = document.getElementById('deleteUserBtn');
    const userUsername = document.getElementById('userUsername');
    const userPassword = document.getElementById('userPassword');
    const userRole = document.getElementById('userRole');
    const userPtzAccess = document.getElementById('userPtzAccess');
    const userPtzCameras = document.getElementById('userPtzCameras');
    const userDisabled = document.getElementById('userDisabled');
    const siteNameInput = document.getElementById('siteNameInput');
    const saveSiteNameBtn = document.getElementById('saveSiteNameBtn');
    const newCameraName = document.getElementById('newCameraName');
    const newCameraDescription = document.getElementById('newCameraDescription');
    const newCameraVendor = document.getElementById('newCameraVendor');
    const newCameraUrl = document.getElementById('newCameraUrl');
    const toggleNewCameraUrlBtn = document.getElementById('toggleNewCameraUrlBtn');
    const testNewCameraBtn = document.getElementById('testNewCameraBtn');
    const confirmNewCameraBtn = document.getElementById('confirmNewCameraBtn');
    const newCameraTestResult = document.getElementById('newCameraTestResult');
    const configFormContainer = document.getElementById('configFormContainer');
    const statusMessage = document.getElementById('statusMessage');
    let loadedConfigData = null;
    let newCameraLastTest = null;
    let usersPayload = { users: [], cameras: [] };
    let cameraFormMode = 'add';
    let editingCameraName = null;
    let editingOriginalCamera = null;

    loadConfigBtn.addEventListener('click', fetchAndDisplayConfig);
    saveConfigBtn.addEventListener('click', saveConfiguration);
    reloadAppBtn.addEventListener('click', reloadApplication);
    rebuildCamerasBtn.addEventListener('click', rebuildCamerasJson);
    syncUiBtn.addEventListener('click', syncUI);
    addCameraBtn.addEventListener('click', handleAddCamera);
    editCameraBtn.addEventListener('click', handleEditCamera);
    editCameraSelect.addEventListener('change', () => {
        editCameraBtn.disabled = !editCameraSelect.value;
    });
    closeAddCameraModalBtn.addEventListener('click', () => hideModal(addCameraModal));
    manageUsersBtn.addEventListener('click', openUserManager);
    closeUserModalBtn.addEventListener('click', () => hideModal(userModal));
    refreshStorageBtn.addEventListener('click', loadStorageSummary);
    newUserBtn.addEventListener('click', clearUserForm);
    userForm.addEventListener('submit', saveUser);
    deleteUserBtn.addEventListener('click', deleteUser);
    saveSiteNameBtn.addEventListener('click', saveSiteName);
    toggleNewCameraUrlBtn.addEventListener('click', toggleNewCameraUrl);
    newCameraVendor.addEventListener('change', applyNewCameraTemplate);
    newCameraUrl.addEventListener('input', resetNewCameraTest);
    newCameraName.addEventListener('input', resetNewCameraTest);
    testNewCameraBtn.addEventListener('click', testNewCameraSnapshot);
    confirmNewCameraBtn.addEventListener('click', confirmNewCameraAdd);
    [addCameraModal, userModal].forEach(modal => {
        modal.addEventListener('click', event => {
            if (event.target === modal) {
                hideModal(modal);
            }
        });
    });
    document.querySelectorAll('.option-toggle').forEach(toggle => {
        toggle.addEventListener('change', () => syncOptionGroup(toggle));
        syncOptionGroup(toggle);
    });

    function showModal(modal) {
        modal.hidden = false;
    }

    function hideModal(modal) {
        modal.hidden = true;
    }

    function escapeHtml(value) {
        return String(value || '').replace(/[&<>"']/g, char => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[char]));
    }

    function renderStorageSummary(data) {
        const limit = data.limit_GB ? `${data.limit_GB} GB` : 'not set';
        const status = data.enabled
            ? (data.dry_run ? 'enabled in dry run' : 'enabled')
            : 'disabled';
        const cameraRows = (data.cameras || []).slice(0, 8).map(camera => {
            const cameraLimit = camera.limit_GB ? `${camera.limit_GB} GB` : 'not set';
            return `<li><span>${escapeHtml(camera.name)}</span><strong>${escapeHtml(camera.display)}</strong><small>limit ${escapeHtml(cameraLimit)}</small></li>`;
        }).join('');
        storageSummary.innerHTML = `
            <div class="storage-total">
                <strong>${escapeHtml(data.display)}</strong>
                <span>of ${escapeHtml(limit)} global limit</span>
                <span class="storage-badge">${escapeHtml(status)}</span>
            </div>
            <div class="storage-path">${escapeHtml(data.work_dir || 'No work_dir configured')}</div>
            <ul class="storage-camera-list">${cameraRows}</ul>
        `;
    }

    async function loadStorageSummary() {
        storageSummary.textContent = 'Loading storage usage...';
        try {
            const response = await fetch('/api/storage/summary');
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || `HTTP error! status: ${response.status}`);
            }
            renderStorageSummary(data);
        } catch (error) {
            storageSummary.textContent = `Storage summary unavailable: ${error.message}`;
        }
    }

    function populateCameraEditOptions() {
        const cameras = (loadedConfigData && loadedConfigData.cameras) || {};
        const previousValue = editCameraSelect.value;
        editCameraSelect.innerHTML = '';
        const names = Object.keys(cameras).sort();
        if (!names.length) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = 'No cameras configured';
            editCameraSelect.appendChild(option);
            editCameraBtn.disabled = true;
            return;
        }
        names.forEach(cameraName => {
            const option = document.createElement('option');
            option.value = cameraName;
            option.textContent = cameraName;
            editCameraSelect.appendChild(option);
        });
        editCameraSelect.value = names.includes(previousValue) ? previousValue : names[0];
        editCameraBtn.disabled = !editCameraSelect.value;
    }

    function setInputValue(id, value) {
        const input = document.getElementById(id);
        if (input) input.value = value ?? '';
    }

    function setCheckboxValue(id, value) {
        const input = document.getElementById(id);
        if (input) input.checked = Boolean(value);
    }

    function setSelectValue(id, value) {
        const input = document.getElementById(id);
        if (input) input.value = value ?? input.value;
    }

    function syncAllOptionGroups() {
        document.querySelectorAll('.option-toggle').forEach(toggle => syncOptionGroup(toggle));
    }

    function resetCameraForm() {
        cameraFormMode = 'add';
        editingCameraName = null;
        editingOriginalCamera = null;
        cameraModalTitle.textContent = 'Add Camera';
        confirmNewCameraBtn.textContent = 'Add Camera to Config';
        newCameraName.disabled = false;
        setInputValue('newCameraName', '');
        setInputValue('newCameraDescription', '');
        setSelectValue('newCameraVendor', 'generic');
        setInputValue('newCameraUrl', '');
        newCameraUrl.type = 'password';
        toggleNewCameraUrlBtn.textContent = 'Show';
        setCheckboxValue('newCameraPublic', true);
        setCheckboxValue('newCameraCacheBust', true);
        setCheckboxValue('newCameraMozjpeg', true);
        setCheckboxValue('newCameraTimelapse', true);
        setInputValue('newCameraTimeout', 15);
        setCheckboxValue('newCameraFixedIntervalEnabled', true);
        setInputValue('newCameraSnapInterval', 60);
        setCheckboxValue('newCameraActivityEnabled', true);
        setInputValue('newCameraActivityInterval', 10);
        setInputValue('newCameraSsimSetpoint', 0.88);
        setInputValue('newCameraSsimArea', '0,0,1,1');
        setCheckboxValue('newCameraSunEnabled', true);
        setInputValue('newCameraSunInterval', 10);
        setInputValue('newCameraLat', 35.2828);
        setInputValue('newCameraLon', -120.6596);
        setInputValue('newCameraSunWindow', 45);
        setCheckboxValue('newCameraSkyEnabled', true);
        setInputValue('newCameraSkyArea', '0,0,1,0.35');
        setCheckboxValue('newCameraStorageEnabled', true);
        setInputValue('newCameraStorageGb', 5);
        setCheckboxValue('newCameraTimestampEnabled', true);
        setSelectValue('newCameraTimestampPosition', 'bottom_right');
        setCheckboxValue('newCameraPtzEnabled', false);
        setCheckboxValue('newCameraPtzPublic', false);
        setCheckboxValue('newCameraPtzAllowPresets', true);
        setCheckboxValue('newCameraPtzAllowManual', false);
        setSelectValue('newCameraPtzAccessLevel', 'presets');
        setInputValue('newCameraPtzHost', '');
        setInputValue('newCameraPtzPort', 80);
        setInputValue('newCameraPtzUsername', '');
        setInputValue('newCameraPtzPassword', '');
        setInputValue('newCameraPtzProfileToken', '');
        setInputValue('newCameraPtzPresets', '');
        resetNewCameraTest();
        syncAllOptionGroups();
    }

    function inferSunWindow(sunConfig = {}) {
        const values = [
            sunConfig.sunrise_offset_start_minutes,
            sunConfig.sunrise_offset_end_minutes,
            sunConfig.sunset_offset_start_minutes,
            sunConfig.sunset_offset_end_minutes,
        ].filter(value => Number.isFinite(Number(value)));
        return values.length ? Number(values[0]) : 45;
    }

    function fillCameraForm(cameraName) {
        const camera = ((loadedConfigData && loadedConfigData.cameras) || {})[cameraName];
        if (!camera) {
            throw new Error(`Camera '${cameraName}' was not found in the loaded config.`);
        }
        resetCameraForm();
        cameraFormMode = 'edit';
        editingCameraName = cameraName;
        editingOriginalCamera = camera;
        cameraModalTitle.textContent = `Edit ${cameraName}`;
        confirmNewCameraBtn.textContent = 'Save Camera Changes';
        newCameraName.disabled = true;

        setInputValue('newCameraName', cameraName);
        setInputValue('newCameraDescription', camera.description || '');
        setInputValue('newCameraUrl', camera.url || '');
        setInputValue('newCameraTimeout', camera.timeout_s ?? 15);
        setCheckboxValue('newCameraPublic', camera.public !== false);
        setCheckboxValue('newCameraCacheBust', camera.cache_bust !== false);
        setCheckboxValue('newCameraMozjpeg', camera.mozjpeg_optimize === true);
        setCheckboxValue('newCameraTimelapse', camera.timelapse_enabled !== false && camera.generate_timelapse !== false);

        setCheckboxValue('newCameraFixedIntervalEnabled', camera.snap_interval_s !== undefined);
        setInputValue('newCameraSnapInterval', camera.snap_interval_s ?? 60);
        setCheckboxValue('newCameraActivityEnabled', camera.activity_interval_s !== undefined || camera.ssim_setpoint !== undefined || camera.ssim_area !== undefined);
        setInputValue('newCameraActivityInterval', camera.activity_interval_s ?? 10);
        setInputValue('newCameraSsimSetpoint', camera.ssim_setpoint ?? 0.88);
        setInputValue('newCameraSsimArea', camera.ssim_area || '0,0,1,1');

        const sunConfig = camera.sunrise_sunset || {};
        setCheckboxValue('newCameraSunEnabled', sunConfig.enabled !== false && (camera.lat !== undefined || camera.lon !== undefined || camera.sunrise_sunset !== undefined));
        setInputValue('newCameraSunInterval', sunConfig.interval_s ?? 10);
        setInputValue('newCameraLat', camera.lat ?? 35.2828);
        setInputValue('newCameraLon', camera.lon ?? -120.6596);
        setInputValue('newCameraSunWindow', inferSunWindow(sunConfig));

        setCheckboxValue('newCameraSkyEnabled', camera.sky_area !== undefined);
        setInputValue('newCameraSkyArea', camera.sky_area || '0,0,1,0.35');
        setCheckboxValue('newCameraStorageEnabled', camera.work_dir_max_size_GB !== undefined);
        setInputValue('newCameraStorageGb', camera.work_dir_max_size_GB ?? 5);

        const timestampStep = (camera.postprocessing || []).find(step => step && step.type === 'timestamp');
        setCheckboxValue('newCameraTimestampEnabled', Boolean(timestampStep));
        setSelectValue('newCameraTimestampPosition', (timestampStep && timestampStep.position) || 'bottom_right');

        const ptz = camera.ptz || {};
        setCheckboxValue('newCameraPtzEnabled', ptz.enabled === true);
        setCheckboxValue('newCameraPtzPublic', ptz.public === true);
        setCheckboxValue('newCameraPtzAllowPresets', ptz.allow_presets !== false);
        setCheckboxValue('newCameraPtzAllowManual', ptz.allow_manual_control === true);
        setSelectValue('newCameraPtzAccessLevel', ptz.access_level || 'presets');
        setInputValue('newCameraPtzHost', ptz.host || ptz.ip || '');
        setInputValue('newCameraPtzPort', ptz.port ?? 80);
        setInputValue('newCameraPtzUsername', ptz.username || '');
        setInputValue('newCameraPtzPassword', '');
        setInputValue('newCameraPtzProfileToken', ptz.profile_token || '');
        setInputValue('newCameraPtzPresets', ptz.presets ? JSON.stringify(ptz.presets, null, 2) : '');
        syncAllOptionGroups();
        newCameraLastTest = { url: camera.url || '', name: cameraName };
        confirmNewCameraBtn.disabled = false;
    }

    function selectedPtzCameras() {
        return Array.from(userPtzCameras.selectedOptions).map(option => option.value);
    }

    function renderUserList() {
        userList.innerHTML = '';
        if (!usersPayload.users.length) {
            userList.textContent = 'No users configured.';
            return;
        }
        usersPayload.users.forEach(user => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'user-list-item';
            button.textContent = `${user.username}${user.disabled ? ' (disabled)' : ''}`;
            button.addEventListener('click', () => fillUserForm(user));
            userList.appendChild(button);
        });
    }

    function populateUserCameraOptions(selected = []) {
        const selectedSet = new Set(selected);
        userPtzCameras.innerHTML = '';
        (usersPayload.cameras || []).forEach(cameraName => {
            const option = document.createElement('option');
            option.value = cameraName;
            option.textContent = cameraName;
            option.selected = selectedSet.has(cameraName);
            userPtzCameras.appendChild(option);
        });
    }

    function clearUserForm() {
        userUsername.value = '';
        userPassword.value = '';
        userRole.value = 'viewer';
        userPtzAccess.value = 'presets';
        userDisabled.checked = false;
        populateUserCameraOptions([]);
        deleteUserBtn.disabled = true;
        userUsername.disabled = false;
        userUsername.focus();
    }

    function fillUserForm(user) {
        userUsername.value = user.username;
        userPassword.value = '';
        userRole.value = user.role || 'viewer';
        userPtzAccess.value = user.ptz_access || 'presets';
        userDisabled.checked = Boolean(user.disabled);
        populateUserCameraOptions(user.ptz_cameras || []);
        deleteUserBtn.disabled = false;
        userUsername.disabled = false;
    }

    async function loadUsers() {
        userList.textContent = 'Loading users...';
        const response = await fetch('/api/users');
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || `HTTP error! status: ${response.status}`);
        }
        usersPayload = data;
        renderUserList();
        populateUserCameraOptions([]);
    }

    async function openUserManager() {
        showModal(userModal);
        try {
            await loadUsers();
            clearUserForm();
        } catch (error) {
            setStatus(`Error loading users: ${error.message}`, 'error');
        }
    }

    async function saveUser(event) {
        event.preventDefault();
        const payload = {
            username: userUsername.value.trim(),
            password: userPassword.value,
            role: userRole.value,
            disabled: userDisabled.checked,
            ptz_access: userPtzAccess.value,
            ptz_cameras: selectedPtzCameras()
        };
        try {
            const response = await fetch('/api/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.error || `HTTP error! status: ${response.status}`);
            }
            userPassword.value = '';
            setStatus(result.message || 'User saved.', 'success');
            await loadUsers();
        } catch (error) {
            setStatus(`Error saving user: ${error.message}`, 'error');
        }
    }

    async function deleteUser() {
        const username = userUsername.value.trim();
        if (!username) {
            return;
        }
        if (!window.confirm(`Delete user '${username}'?`)) {
            return;
        }
        try {
            const response = await fetch(`/api/users/${encodeURIComponent(username)}`, { method: 'DELETE' });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.error || `HTTP error! status: ${response.status}`);
            }
            setStatus(result.message || 'User deleted.', 'success');
            await loadUsers();
            clearUserForm();
        } catch (error) {
            setStatus(`Error deleting user: ${error.message}`, 'error');
        }
    }

    async function fetchAndDisplayConfig() {
        setStatus('Loading configuration...', 'info');
        try {
            const response = await fetch('/config');
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ error: `HTTP error! status: ${response.status}` }));
                throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
            }
            const config = await response.json();
            loadedConfigData = config.config || config;
            siteNameInput.value = (loadedConfigData.global && loadedConfigData.global.deployment_name) || 'fenetre.cam';
            saveSiteNameBtn.disabled = false;
            populateCameraEditOptions();
            renderConfigForm(config, configFormContainer, '');
            setStatus('Configuration loaded successfully.', 'success');
            saveConfigBtn.disabled = false;
        } catch (error) {
            console.error('Error fetching config:', error);
            setStatus(`Error fetching configuration: ${error.message}`, 'error');
        }
    }

    function isSensitiveField(key) {
        const normalized = key.toLowerCase();
        return normalized === 'url'
            || normalized.endsWith('.url')
            || normalized.includes('password')
            || normalized.includes('token')
            || normalized.includes('secret')
            || normalized.includes('root_ca');
    }

    function appendRevealToggle(inputWrapper, input) {
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'reveal-field-btn';
        toggle.textContent = 'Show';
        toggle.addEventListener('click', () => {
            const showing = input.type !== 'password';
            input.type = showing ? 'password' : 'text';
            toggle.textContent = showing ? 'Show' : 'Hide';
        });
        inputWrapper.appendChild(toggle);
    }

    function syncOptionGroup(toggle) {
        const fieldset = toggle.closest('.option-group');
        if (!fieldset) return;
        fieldset.classList.toggle('enabled', toggle.checked);
        fieldset.querySelectorAll('.option-content input, .option-content select, .option-content textarea').forEach(input => {
            input.disabled = !toggle.checked;
        });
    }

    function toggleNewCameraUrl() {
        const showing = newCameraUrl.type !== 'password';
        newCameraUrl.type = showing ? 'password' : 'text';
        toggleNewCameraUrlBtn.textContent = showing ? 'Show' : 'Hide';
    }

    function applyNewCameraTemplate() {
        const templates = {
            generic: 'http://camera.example/snapshot.jpg',
            reolink: 'http://CAMERA_IP/cgi-bin/api.cgi?cmd=Snap&channel=0&rs=fenetre&user=USERNAME&password=PASSWORD',
            sunba: 'http://CAMERA_IP/cgi-bin/snapshot.cgi?chn=0&u=USERNAME&p=PASSWORD'
        };
        if (!newCameraUrl.value || Object.values(templates).includes(newCameraUrl.value)) {
            newCameraUrl.value = templates[newCameraVendor.value] || templates.generic;
        }
        resetNewCameraTest();
    }

    function resetNewCameraTest() {
        newCameraLastTest = null;
        confirmNewCameraBtn.disabled = true;
        newCameraTestResult.innerHTML = '';
    }

    function numberValue(id, fallback) {
        const value = parseFloat(document.getElementById(id).value);
        return Number.isFinite(value) ? value : fallback;
    }

    function intValue(id, fallback) {
        const value = parseInt(document.getElementById(id).value, 10);
        return Number.isFinite(value) ? value : fallback;
    }

    function checked(id) {
        const el = document.getElementById(id);
        return Boolean(el && el.checked);
    }

    function collectNewCameraPayload(requireTest = true) {
        const postprocessing = cameraFormMode === 'edit' && editingOriginalCamera
            ? (editingOriginalCamera.postprocessing || []).filter(step => step && step.type !== 'timestamp')
            : [];
        if (checked('newCameraTimestampEnabled')) {
            postprocessing.push({
                type: 'timestamp',
                enabled: true,
                position: document.getElementById('newCameraTimestampPosition').value || 'bottom_right',
                size: 24,
                color: 'white',
                format: '%Y-%m-%d %H:%M:%S %Z'
            });
        }

        const payload = {
            name: newCameraName.value.trim(),
            description: newCameraDescription.value.trim(),
            url: newCameraUrl.value.trim(),
            timeout_s: intValue('newCameraTimeout', 15),
            public: checked('newCameraPublic'),
            cache_bust: checked('newCameraCacheBust'),
            gather_metrics: true,
            mozjpeg_optimize: checked('newCameraMozjpeg'),
            timelapse_enabled: checked('newCameraTimelapse'),
            require_test: requireTest,
            postprocessing
        };

        if (checked('newCameraFixedIntervalEnabled')) {
            payload.snap_interval_enabled = true;
            payload.snap_interval_s = intValue('newCameraSnapInterval', 60);
        }
        if (checked('newCameraActivityEnabled')) {
            payload.activity_interval_enabled = true;
            payload.activity_interval_s = intValue('newCameraActivityInterval', 10);
            payload.ssim_enabled = true;
            payload.ssim_setpoint = numberValue('newCameraSsimSetpoint', 0.88);
            payload.ssim_area = document.getElementById('newCameraSsimArea').value.trim() || '0,0,1,1';
        }
        if (checked('newCameraSunEnabled')) {
            const windowMinutes = intValue('newCameraSunWindow', 45);
            payload.sunrise_sunset_enabled = true;
            payload.sunrise_sunset_interval_s = intValue('newCameraSunInterval', 10);
            payload.lat = numberValue('newCameraLat', 35.2828);
            payload.lon = numberValue('newCameraLon', -120.6596);
            payload.sunrise_offset_start_minutes = windowMinutes;
            payload.sunrise_offset_end_minutes = windowMinutes;
            payload.sunset_offset_start_minutes = windowMinutes;
            payload.sunset_offset_end_minutes = windowMinutes;
        }
        if (checked('newCameraSkyEnabled')) {
            payload.sky_area_enabled = true;
            payload.sky_area = document.getElementById('newCameraSkyArea').value.trim() || '0,0,1,0.35';
        }
        if (checked('newCameraStorageEnabled')) {
            payload.work_dir_max_size_GB = intValue('newCameraStorageGb', 5);
        }
        if (checked('newCameraPtzEnabled')) {
            const presetsText = document.getElementById('newCameraPtzPresets').value.trim();
            let presets = [];
            if (presetsText) {
                presets = JSON.parse(presetsText);
            }
            payload.ptz_enabled = true;
            payload.ptz_public = checked('newCameraPtzPublic');
            payload.ptz_allow_presets = checked('newCameraPtzAllowPresets');
            payload.ptz_allow_manual_control = checked('newCameraPtzAllowManual');
            payload.ptz_access_level = document.getElementById('newCameraPtzAccessLevel').value || 'presets';
            payload.ptz_host = document.getElementById('newCameraPtzHost').value.trim();
            payload.ptz_port = intValue('newCameraPtzPort', 80);
            payload.ptz_username = document.getElementById('newCameraPtzUsername').value.trim();
            payload.ptz_password = document.getElementById('newCameraPtzPassword').value;
            payload.ptz_profile_token = document.getElementById('newCameraPtzProfileToken').value.trim();
            payload.ptz_presets = presets;
        }
        return payload;
    }

    function validateNewCameraPayload(payload) {
        if (!payload.name) {
            throw new Error('Camera ID is required.');
        }
        if (!/^[A-Za-z0-9_-]+$/.test(payload.name)) {
            throw new Error('Camera ID can only use letters, numbers, underscores, and dashes.');
        }
        if (!payload.url) {
            throw new Error('Snapshot URL is required.');
        }
    }

    async function testNewCameraSnapshot() {
        try {
            const payload = collectNewCameraPayload(false);
            validateNewCameraPayload(payload);
            newCameraTestResult.textContent = 'Testing snapshot URL...';
            confirmNewCameraBtn.disabled = true;
            const response = await fetch('/api/camera/test_snapshot', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: payload.url, timeout_s: payload.timeout_s })
            });
            const result = await response.json();
            if (!response.ok || !result.ok) {
                throw new Error(result.error || `Snapshot test failed with HTTP ${response.status}`);
            }
            newCameraLastTest = { url: payload.url, name: payload.name };
            newCameraTestResult.innerHTML = '';
            const summary = document.createElement('div');
            summary.textContent = `Snapshot OK: ${result.width}x${result.height}, ${Math.round(result.bytes / 1024)} KB`;
            const preview = document.createElement('img');
            preview.alt = 'Snapshot preview';
            preview.src = result.preview_data_url;
            newCameraTestResult.appendChild(summary);
            newCameraTestResult.appendChild(preview);
            confirmNewCameraBtn.disabled = false;
        } catch (error) {
            newCameraLastTest = null;
            confirmNewCameraBtn.disabled = true;
            newCameraTestResult.textContent = error.message;
            setStatus(`Snapshot test failed: ${error.message}`, 'error');
        }
    }

    async function confirmNewCameraAdd() {
        try {
            const payload = collectNewCameraPayload(true);
            validateNewCameraPayload(payload);
            const urlChanged = cameraFormMode === 'edit'
                && editingOriginalCamera
                && payload.url !== (editingOriginalCamera.url || '');
            if (!newCameraLastTest || newCameraLastTest.url !== payload.url || newCameraLastTest.name !== payload.name) {
                throw new Error('Test the current camera ID and snapshot URL before saving it.');
            }
            const isEdit = cameraFormMode === 'edit' && editingCameraName;
            setStatus(`${isEdit ? 'Updating' : 'Adding'} camera '${payload.name}'...`, 'info');
            payload.require_test = !isEdit || urlChanged;
            const response = await fetch(isEdit ? `/api/camera/${encodeURIComponent(editingCameraName)}` : '/api/camera/add', {
                method: isEdit ? 'PUT' : 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.error || `Failed to add camera with HTTP ${response.status}`);
            }
            setStatus(result.message || `Camera '${payload.name}' saved. Reload the app to make it live.`, 'success');
            await fetchAndDisplayConfig();
            await loadStorageSummary();
            hideModal(addCameraModal);
            confirmNewCameraBtn.disabled = true;
        } catch (error) {
            setStatus(`Error adding camera: ${error.message}`, 'error');
        }
    }

    function renderConfigForm(data, parentElement, parentKey = '') {
        parentElement.innerHTML = ''; // Clear previous form

        for (const key in data) {
            if (!data.hasOwnProperty(key)) continue;

            const value = data[key];
            const currentKey = parentKey ? `${parentKey}.${key}` : key;

            const formRow = document.createElement('div');
            formRow.className = 'form-row';

            const label = document.createElement('label');
            label.textContent = key;
            label.htmlFor = currentKey;
            formRow.appendChild(label);

            const inputWrapper = document.createElement('div');
            inputWrapper.className = 'input-wrapper';

            if (typeof value === 'boolean') {
                const input = document.createElement('input');
                input.type = 'checkbox';
                input.id = currentKey;
                input.checked = value;
                input.dataset.key = currentKey;
                inputWrapper.appendChild(input);
                formRow.appendChild(inputWrapper);
                parentElement.appendChild(formRow);
            } else if (typeof value === 'number') {
                const input = document.createElement('input');
                input.type = 'number';
                input.id = currentKey;
                input.value = value;
                input.dataset.key = currentKey;
                inputWrapper.appendChild(input);
                formRow.appendChild(inputWrapper);
                parentElement.appendChild(formRow);
            } else if (typeof value === 'string') {
                // Use textarea for multi-line strings (e.g. gopro_root_ca)
                if (value.includes('\n')) {
                    const textarea = document.createElement('textarea');
                    textarea.id = currentKey;
                    textarea.value = value;
                    textarea.rows = value.split('\n').length + 1;
                    textarea.dataset.key = currentKey;
                    inputWrapper.appendChild(textarea);
                    formRow.appendChild(inputWrapper);
                    parentElement.appendChild(formRow);
                } else {
                    const input = document.createElement('input');
                    input.type = isSensitiveField(currentKey) ? 'password' : 'text';
                    input.id = currentKey;
                    input.value = value;
                    input.dataset.key = currentKey;
                    inputWrapper.appendChild(input);
                    if (isSensitiveField(currentKey)) {
                        appendRevealToggle(inputWrapper, input);
                    }
                    formRow.appendChild(inputWrapper);
                    parentElement.appendChild(formRow);
                }
            } else if (Array.isArray(value)) {
                const fieldset = document.createElement('fieldset');
                const legend = document.createElement('legend');
                legend.textContent = key + ' (List)';
                legend.classList.add('collapsible');
                fieldset.appendChild(legend);

                const content = document.createElement('div');
                content.className = 'collapsible-content';
                fieldset.dataset.key = currentKey;
                fieldset.dataset.type = 'array';

                value.forEach((item, index) => {
                    const itemContainer = createArrayItemContainer(item, `${currentKey}[${index}]`, index, content);
                    content.appendChild(itemContainer);
                });

                const addButton = document.createElement('button');
                addButton.textContent = 'Add Item';
                addButton.type = 'button';
                addButton.classList.add('add-item-btn');
                addButton.addEventListener('click', () => addArrayItem(content, `${currentKey}[${value.length}]`, value.length, (value.length > 0 && typeof value[0] === 'object' ? {} : '')));
                content.appendChild(addButton);
                fieldset.appendChild(content);
                parentElement.appendChild(fieldset);

            } else if (typeof value === 'object' && value !== null) {
                const fieldset = document.createElement('fieldset');
                const legend = document.createElement('legend');
                legend.textContent = key;
                legend.classList.add('collapsible');
                fieldset.appendChild(legend);

                const content = document.createElement('div');
                content.className = 'collapsible-content';
                fieldset.dataset.key = currentKey;
                fieldset.dataset.type = 'object';
                renderConfigForm(value, content, currentKey);
                fieldset.appendChild(content);
                parentElement.appendChild(fieldset);
            }
        }
    }

    async function saveSiteName() {
        if (!loadedConfigData) {
            setStatus('Load the configuration before saving the GUI name.', 'error');
            return;
        }
        const siteName = siteNameInput.value.trim();
        if (!siteName) {
            setStatus('GUI name cannot be empty.', 'error');
            return;
        }
        const updatedConfig = structuredClone(loadedConfigData);
        if (!updatedConfig.global) {
            updatedConfig.global = {};
        }
        updatedConfig.global.deployment_name = siteName;
        setStatus('Saving GUI name...', 'info');
        try {
            const response = await fetch('/api/global/deployment_name', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ deployment_name: siteName }),
            });
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ error: `HTTP error! status: ${response.status}` }));
                throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
            }
            loadedConfigData = updatedConfig;
            const deploymentInput = document.getElementById('config.global.deployment_name');
            if (deploymentInput) {
                deploymentInput.value = siteName;
            }
            const result = await response.json();
            setStatus(result.message || 'GUI name saved. Reload the app and sync UI to publish it.', 'success');
        } catch (error) {
            console.error('Error saving GUI name:', error);
            setStatus(`Error saving GUI name: ${error.message}`, 'error');
        }
    }

    function createArrayItemContainer(item, itemKey, index, parentFieldset) {
        const itemContainer = document.createElement('div');
        itemContainer.classList.add('array-item');
        itemContainer.dataset.index = index;
        itemContainer.dataset.key = itemKey; // e.g., cameras[0]

        if (typeof item === 'object' && item !== null) {
            renderConfigForm(item, itemContainer, itemKey);
        } else { // Primitive type (string, number, boolean)
            const input = document.createElement('input');
            input.type = (typeof item === 'number') ? 'number' : (typeof item === 'boolean') ? 'checkbox' : 'text';
            if (typeof item === 'boolean') input.checked = item; else input.value = item;
            input.id = itemKey;
            input.dataset.key = itemKey; // This input represents the item itself
            itemContainer.appendChild(input);
        }

        const removeButton = document.createElement('button');
        removeButton.textContent = 'Remove';
        removeButton.type = 'button';
        removeButton.classList.add('remove-item-btn');
        removeButton.addEventListener('click', () => {
            itemContainer.remove();
            // Re-index remaining items if necessary, or handle gaps in getFormDataAsJson
        });

        const controlsDiv = document.createElement('div');
        controlsDiv.classList.add('array-item-controls');
        controlsDiv.appendChild(removeButton);
        itemContainer.appendChild(controlsDiv);

        return itemContainer;
    }

    function addArrayItem(parentFieldset, baseKey, newIndex, templateItem) {
        const itemKeyPrefix = baseKey.substring(0, baseKey.lastIndexOf('[')); // e.g., cameras.mycam.postprocessing
        const actualKey = itemKeyPrefix.split('.').pop(); // e.g., postprocessing

        if (actualKey === 'postprocessing') {
            // Special handling for postprocessing items
            const typeSelectorContainer = document.createElement('div');
            typeSelectorContainer.classList.add('type-selector-container');

            const selectLabel = document.createElement('label');
            selectLabel.textContent = 'Select postprocessing type: ';
            typeSelectorContainer.appendChild(selectLabel);

            const typeSelect = document.createElement('select');
            availablePostprocessingTypes.forEach(type => {
                const option = document.createElement('option');
                option.value = type;
                option.textContent = type;
                typeSelect.appendChild(option);
            });
            typeSelectorContainer.appendChild(typeSelect);

            const confirmButton = document.createElement('button');
            confirmButton.textContent = 'Add Selected Type';
            confirmButton.type = 'button';
            confirmButton.addEventListener('click', () => {
                const selectedType = typeSelect.value;
                typeSelectorContainer.remove(); // Remove selector UI

                // Determine the new index for the actual item
                // This needs to be robust if items can be removed out of order.
                // For simplicity, assume newIndex is roughly correct for now, or re-calculate.
                const currentItemCount = parentFieldset.querySelectorAll(':scope > .array-item, :scope > .postprocessing-item').length;
                const itemKey = `${itemKeyPrefix}[${currentItemCount}]`;

                const newItemContainer = renderPostprocessingItem(selectedType, itemKey, parentFieldset);
                parentFieldset.insertBefore(newItemContainer, parentFieldset.querySelector('.add-item-btn'));
            });
            typeSelectorContainer.appendChild(confirmButton);

            const cancelButton = document.createElement('button');
            cancelButton.textContent = 'Cancel';
            cancelButton.type = 'button';
            cancelButton.addEventListener('click', () => {
                typeSelectorContainer.remove();
            });
            typeSelectorContainer.appendChild(cancelButton);

            // Insert the type selector before the 'Add Item' button
            parentFieldset.insertBefore(typeSelectorContainer, parentFieldset.querySelector('.add-item-btn'));

        } else {
            // Default behavior for other arrays
            const itemContainer = createArrayItemContainer(templateItem, `${itemKeyPrefix}[${newIndex}]`, newIndex, parentFieldset);
            parentFieldset.insertBefore(itemContainer, parentFieldset.querySelector('.add-item-btn'));
        }
    }

    function renderPostprocessingItem(type, itemBaseKey, parentFieldset) {
        const itemContainer = document.createElement('div');
        itemContainer.classList.add('postprocessing-item', 'array-item'); // Add array-item for consistent removal styling/logic
        itemContainer.dataset.key = itemBaseKey; // e.g., cameras.cam1.postprocessing[0]
        itemContainer.dataset.type = type; // Store the type

        const typeDisplay = document.createElement('h5'); // Or a div with styling
        typeDisplay.textContent = `Type: ${type}`;
        itemContainer.appendChild(typeDisplay);

        // Hidden input to store the type, will be picked up by getFormDataAsJson
        const typeInput = document.createElement('input');
        typeInput.type = 'hidden';
        typeInput.dataset.key = `${itemBaseKey}.type`; // Path for the type property
        typeInput.value = type;
        itemContainer.appendChild(typeInput);

        const typeDefinition = postprocessingTypes[type];
        if (typeDefinition && typeDefinition.fields) {
            typeDefinition.order.forEach(fieldName => {
                const field = typeDefinition.fields[fieldName];
                const fieldKey = `${itemBaseKey}.${fieldName}`; // e.g., cameras.cam1.postprocessing[0].area

                const label = document.createElement('label');
                label.textContent = fieldName;
                label.htmlFor = fieldKey;
                itemContainer.appendChild(label);

                let input;
                if (field.type === 'checkbox') {
                    input = document.createElement('input');
                    input.type = 'checkbox';
                    input.checked = field.default;
                } else if (field.type === 'number') {
                    input = document.createElement('input');
                    input.type = 'number';
                    input.value = field.default;
                } else { // 'text' or other
                    input = document.createElement('input');
                    input.type = 'text'; // Default to text
                    input.value = field.default;
                }
                input.id = fieldKey;
                input.dataset.key = fieldKey; // Crucial for getFormDataAsJson
                itemContainer.appendChild(input);
                itemContainer.appendChild(document.createElement('br'));
            });
        }

        const removeButton = document.createElement('button');
        removeButton.textContent = 'Remove This Step';
        removeButton.type = 'button';
        removeButton.classList.add('remove-item-btn');
        removeButton.addEventListener('click', () => {
            itemContainer.remove();
            // Note: Re-indexing siblings or handling gaps in getFormDataAsJson might be needed for arrays.
        });
        itemContainer.appendChild(removeButton);

        return itemContainer;
    }


    function getFormDataAsJson() {
        const data = {};
        function buildObject(element, obj) {
            if (element.dataset.key) {
                const keys = element.dataset.key.split('.');
                let current = obj;
                keys.forEach((k, index) => {
                    // Array handling: key[index]
                    const arrayMatch = k.match(/^([^\[]+)\[(\d+)\]$/);
                    if (arrayMatch) {
                        const arrayKey = arrayMatch[1];
                        const arrayIndex = parseInt(arrayMatch[2]);
                        if (!current[arrayKey]) current[arrayKey] = [];

                        if (index === keys.length - 1) { // Last part of the key path
                            if (element.type === 'checkbox') {
                                current[arrayKey][arrayIndex] = element.checked;
                            } else if (element.type === 'number') {
                                current[arrayKey][arrayIndex] = parseFloat(element.value) || 0;
                            } else {
                                current[arrayKey][arrayIndex] = element.value;
                            }
                        } else {
                            // This part of path is an array, but not the final value holder
                            if (!current[arrayKey][arrayIndex]) {
                                // Check next key part to see if it's another array index or an object key
                                const nextKeyPart = keys[index+1];
                                if (nextKeyPart.includes('[')) {
                                    current[arrayKey][arrayIndex] = [];
                                } else {
                                    current[arrayKey][arrayIndex] = {};
                                }
                            }
                            current = current[arrayKey][arrayIndex];
                        }
                    } else { // Object key
                        if (index === keys.length - 1) {
                            if (element.type === 'checkbox') {
                                current[k] = element.checked;
                            } else if (element.type === 'number') {
                                current[k] = parseFloat(element.value) || 0;
                            } else {
                                current[k] = element.value;
                            }
                        } else {
                            if (!current[k]) {
                                // Check next key part to see if it's an array index or an object key
                                const nextKeyPart = keys[index+1];
                                if (nextKeyPart.includes('[')) {
                                     current[k] = [];
                                } else {
                                     current[k] = {};
                                }
                            }
                            current = current[k];
                        }
                    }
                });
            }
        }

        function processChildren(parentElement, currentObject) {
            for (const child of parentElement.children) {
                if (child.tagName === 'FIELDSET') {
                    const key = child.dataset.key.split('.').pop().replace(/\[\d+\]$/, ''); // Get the actual key name
                    if (child.dataset.type === 'array') {
                        currentObject[key] = [];
                        // Iterate over array item containers
                        Array.from(child.querySelectorAll(':scope > .array-item')).forEach(itemDiv => {
                            let itemValue = {}; // Assume array items that are not primitive are objects
                            // Check if the itemDiv's direct children suggest it's a simple primitive
                            // This check might need to be more robust based on how renderConfigForm structures primitives in arrays
                            const directPrimitiveInput = itemDiv.querySelector(':scope > input, :scope > textarea');
                            let isPrimitiveArrayItem = false;
                            if (directPrimitiveInput) {
                                // If the direct input's key is exactly the itemKey (e.g. "myArray[0]")
                                // it means it's an array of primitives.
                                // An object item would have inputs like "myArray[0].property"
                                const isKeySimpleArrayIndex = !Object.values(directPrimitiveInput.dataset).some(val => val.includes('.'));
                                if (directPrimitiveInput.dataset.key && directPrimitiveInput.dataset.key.endsWith(`[${itemDiv.dataset.index}]` ) && isKeySimpleArrayIndex) {
                                     isPrimitiveArrayItem = true;
                                }
                            }


                            if (isPrimitiveArrayItem && directPrimitiveInput) {
                                if (directPrimitiveInput.type === 'checkbox') itemValue = directPrimitiveInput.checked;
                                else if (directPrimitiveInput.type === 'number') itemValue = parseFloat(directPrimitiveInput.value) || 0;
                                else itemValue = directPrimitiveInput.value;
                            } else {
                                // It's an object within an array. Process its children to build the object.
                                // itemDiv itself contains the fields of the object.
                                processChildren(itemDiv, itemValue);
                            }
                            currentObject[key].push(itemValue);
                        });
                    } else { // Object (not an array)
                        currentObject[key] = {};
                        processChildren(child, currentObject[key]);
                    }
                } else if (child.tagName === 'INPUT' || child.tagName === 'TEXTAREA' || child.tagName === 'SELECT') {
                    // This branch handles direct properties of an object that are input fields.
                    // These inputs should have a data-key.
                    if (child.dataset.key) {
                        const keyParts = child.dataset.key.split('.');
                        const actualKey = keyParts[keyParts.length -1]; // The last part is the actual property name

                        // Ensure we are not trying to process parts of an array item directly here
                        // if (actualKey.includes('[')) continue; // Skip if it looks like an array element part

                        if (child.type === 'checkbox') {
                            currentObject[actualKey] = child.checked;
                        } else if (child.type === 'number') {
                            currentObject[actualKey] = parseFloat(child.value) || 0;
                        } else {
                            currentObject[actualKey] = child.value;
                        }
                    }
                } else if (child.tagName === 'DIV') {
                    // If it's a DIV that's not an array item container (which are handled by fieldset[data-type="array"] logic)
                    // and doesn't have its own data-key (which would make it a field itself, not typical for DIVs here),
                    // recurse into it to find nested fields. This handles container DIVs like .camera-source-group.
                    if (!child.classList.contains('array-item') && !child.dataset.key) {
                        processChildren(child, currentObject);
                    }
                }
            }
        }

        processChildren(configFormContainer, data);
        return data;
    }


    async function saveConfiguration() {
        setStatus('Saving configuration...', 'info');
        let configData = getFormDataAsJson();
        console.log("Form data:", JSON.stringify(configData, null, 2));

        // If the entire form data is under a 'config' key, extract it.
        if (configData.hasOwnProperty('config') && Object.keys(configData).length === 1) {
            configData = configData.config;
        }

        console.log("Saving data:", JSON.stringify(configData, null, 2));

        try {
            const response = await fetch('/config', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(configData),
            });
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ error: `HTTP error! status: ${response.status}` }));
                throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
            }
            const result = await response.json();
            loadedConfigData = configData;
            if (configData.global && configData.global.deployment_name) {
                siteNameInput.value = configData.global.deployment_name;
            }
            setStatus(result.message || 'Configuration saved successfully!', 'success');
        } catch (error) {
            console.error('Error saving config:', error);
            setStatus(`Error saving configuration: ${error.message}`, 'error');
        }
    }

    async function reloadApplication() {
        setStatus('Sending reload signal to application...', 'info');
        try {
            const response = await fetch('/config/reload', {
                method: 'POST',
            });
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ error: `HTTP error! status: ${response.status}` }));
                throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
            }
            const result = await response.json();
            setStatus(result.message || 'Reload signal sent successfully!', 'success');
        } catch (error) {
            console.error('Error reloading application:', error);
            setStatus(`Error reloading application: ${error.message}`, 'error');
        }
    }

    async function syncUI() {
        setStatus('Sending UI sync signal...', 'info');
        try {
            const response = await fetch('/api/sync_ui', {
                method: 'POST',
            });
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ error: `HTTP error! status: ${response.status}` }));
                throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
            }
            const result = await response.json();
            setStatus(result.message || 'UI sync signal sent successfully!', 'success');
        } catch (error) {
            console.error('Error sending UI sync:', error);
            setStatus(`Error sending UI sync: ${error.message}`, 'error');
        }
    }

    async function rebuildCamerasJson() {
        setStatus('Rebuilding cameras.json...', 'info');
        try {
            const response = await fetch('/api/cameras_json/rebuild', {
                method: 'POST',
            });
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ error: `HTTP error! status: ${response.status}` }));
                throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
            }
            const result = await response.json();
            setStatus(result.message || 'cameras.json rebuilt successfully.', 'success');
        } catch (error) {
            console.error('Error rebuilding cameras.json:', error);
            setStatus(`Error rebuilding cameras.json: ${error.message}`, 'error');
        }
    }

    function setStatus(message, type = 'info') {
        statusMessage.textContent = message;
        statusMessage.className = ''; // Clear previous classes
        if (type === 'success') {
            statusMessage.classList.add('success');
        } else if (type === 'error') {
            statusMessage.classList.add('error');
        }
    }

    // Add event listener for collapsible sections
    configFormContainer.addEventListener('click', function(event) {
        if (event.target.classList.contains('collapsible')) {
            event.target.classList.toggle('collapsed');
            const content = event.target.nextElementSibling;
            if (content && content.classList.contains('collapsible-content')) {
                content.classList.toggle('collapsed');
            }
        }
    });

    // Automatically load the configuration when the page loads
    fetchAndDisplayConfig();
    loadStorageSummary();

    function handleAddCamera() {
        resetCameraForm();
        showModal(addCameraModal);
        newCameraName.focus();
        setStatus('Use the Add Camera form, test the snapshot URL, then confirm the add.', 'info');
    }

    function handleEditCamera() {
        const cameraName = editCameraSelect.value;
        if (!cameraName) {
            setStatus('Load the configuration and select a camera to edit.', 'error');
            return;
        }
        try {
            fillCameraForm(cameraName);
            showModal(addCameraModal);
            newCameraDescription.focus();
            setStatus(`Editing camera '${cameraName}'. Save changes when finished.`, 'info');
        } catch (error) {
            setStatus(`Error loading camera for edit: ${error.message}`, 'error');
        }
    }

    function renderCameraItem(cameraFieldset, cameraNameKey, selectedSourceType) {
        // cameraNameKey is the string name like "front-yard-cam"
        // cameraFieldset is the fieldset for this specific camera.
        // Its data-key should be `cameras.${cameraNameKey}`

        const basePath = `cameras.${cameraNameKey}`; // Base path for data-keys

        // Hidden input for the source type if needed for saving, though not directly part of fenetre config structure
        // For now, the presence of url/local_command/gopro_ip keys will define the type.

        // Render source-specific fields
        const sourceTypeDefinition = cameraSourceTypes[selectedSourceType];
        if (sourceTypeDefinition) {
            const sourceGroup = document.createElement('div');
            sourceGroup.classList.add('camera-source-group');
            const groupLegend = document.createElement('h4');
            groupLegend.textContent = `Source: ${selectedSourceType}`;
            sourceGroup.appendChild(groupLegend);

            sourceTypeDefinition.order.forEach(fieldName => {
                const field = sourceTypeDefinition.fields[fieldName];
                const fieldKey = `${basePath}.${fieldName}`;
                appendFieldToForm(sourceGroup, fieldName, field, fieldKey);
            });
            cameraFieldset.appendChild(sourceGroup);
        }

        // Render common camera fields
        const commonGroup = document.createElement('div');
        commonGroup.classList.add('camera-common-group');
        const commonLegend = document.createElement('h4');
        commonLegend.textContent = 'Common Settings';
        commonGroup.appendChild(commonLegend);

        commonCameraFieldsOrder.forEach(fieldName => {
            const field = commonCameraFields[fieldName];
            const fieldKey = `${basePath}.${fieldName}`;

            if (fieldName === 'postprocessing') {
                // Create an empty fieldset for postprocessing array
                const ppFieldset = document.createElement('fieldset');
                const ppLegend = document.createElement('legend');
                ppLegend.textContent = 'postprocessing (List)';
                ppFieldset.appendChild(ppLegend);
                ppFieldset.dataset.key = fieldKey; // e.g., cameras.mycam.postprocessing
                ppFieldset.dataset.type = 'array';

                const addButton = document.createElement('button');
                addButton.textContent = 'Add Postprocessing Step';
                addButton.type = 'button';
                addButton.classList.add('add-item-btn');
                // Ensure newIndex is calculated correctly based on items in this specific ppFieldset
                addButton.addEventListener('click', () => {
                     const currentPPItemCount = ppFieldset.querySelectorAll(':scope > .array-item, :scope > .postprocessing-item').length;
                     addArrayItem(ppFieldset, fieldKey, currentPPItemCount, {}); // Pass empty object as template for postproc
                });
                ppFieldset.appendChild(addButton);
                commonGroup.appendChild(ppFieldset);
            } else {
                appendFieldToForm(commonGroup, fieldName, field, fieldKey);
            }
        });
        cameraFieldset.appendChild(commonGroup);

        // Add a remove button for this camera
        const removeCameraButton = document.createElement('button');
        removeCameraButton.textContent = 'Remove This Camera';
        removeCameraButton.type = 'button';
        removeCameraButton.classList.add('remove-camera-btn');
        removeCameraButton.addEventListener('click', () => {
            cameraFieldset.remove();
            setStatus(`Camera '${cameraNameKey}' removed from UI. Save to confirm.`, 'info');
        });
        cameraFieldset.appendChild(removeCameraButton);

    }

    // Helper function to append a single field (label + input) to a parent element
    function appendFieldToForm(parentElement, fieldName, fieldConfig, fieldKey) {
        const label = document.createElement('label');
        label.textContent = fieldName;
        label.htmlFor = fieldKey;
        parentElement.appendChild(label);

        let input;
        if (fieldConfig.type === 'checkbox') {
            input = document.createElement('input');
            input.type = 'checkbox';
            input.checked = fieldConfig.default;
        } else if (fieldConfig.type === 'number') {
            input = document.createElement('input');
            input.type = 'number';
            input.value = fieldConfig.default;
            if (fieldConfig.step) input.step = fieldConfig.step;
        } else if (fieldConfig.type === 'textarea') {
            input = document.createElement('textarea');
            input.value = fieldConfig.default;
            input.rows = (fieldConfig.default.match(/\n/g) || []).length + 2;
        } else { // 'text' or other
            input = document.createElement('input');
            input.type = isSensitiveField(fieldKey) ? 'password' : 'text';
            input.value = fieldConfig.default;
        }
        input.id = fieldKey;
        input.dataset.key = fieldKey;
        parentElement.appendChild(input);
        if (isSensitiveField(fieldKey)) {
            appendRevealToggle(parentElement, input);
        }
        parentElement.appendChild(document.createElement('br'));
    }

});
