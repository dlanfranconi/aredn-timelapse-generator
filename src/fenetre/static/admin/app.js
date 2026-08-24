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
        visibility: { type: 'text', default: 'public' },
        public: { type: 'checkbox', default: true },
        mozjpeg_optimize: { type: 'checkbox', default: false },
        postprocessing: { type: 'array', default: [] } // Special handling: this will use the postprocessing logic
    };
    // Order for common fields (can be refined)
    const commonCameraFieldsOrder = [
        'description', 'snap_interval_s', 'activity_interval_s', 'timeout_s', 'sky_area', 'ssim_area', 'ssim_setpoint',
        'disabled', 'visibility', 'public', 'mozjpeg_optimize', 'postprocessing'
    ];

    // NOTE: The duplicate declaration of commonCameraFieldsOrder that was here has been removed.

    const loadConfigBtn = document.getElementById('loadConfigBtn');
    const saveConfigBtn = document.getElementById('saveConfigBtn');
    const reloadAppBtn = document.getElementById('reloadAppBtn');
    const rebuildCamerasBtn = document.getElementById('rebuildCamerasBtn');
    const syncUiBtn = document.getElementById('syncUiBtn');
    const manageUsersBtn = document.getElementById('manageUsersBtn');
    const addCameraBtn = document.getElementById('addCameraBtn');
    const changeAdminPasswordBtn = document.getElementById('changeAdminPasswordBtn');
    const adminLogoutBtn = document.getElementById('adminLogoutBtn');
    const editCameraBtn = document.getElementById('editCameraBtn');
    const editCameraSelect = document.getElementById('editCameraSelect');
    const cameraOrderList = document.getElementById('cameraOrderList');
    const sortCameraOrderBtn = document.getElementById('sortCameraOrderBtn');
    const saveCameraOrderBtn = document.getElementById('saveCameraOrderBtn');
    const refreshStorageBtn = document.getElementById('refreshStorageBtn');
    const storageSummary = document.getElementById('storageSummary');
    const mediaDirInput = document.getElementById('mediaDirInput');
    const previewMediaDirBtn = document.getElementById('previewMediaDirBtn');
    const relocateMediaDirBtn = document.getElementById('relocateMediaDirBtn');
    const mediaLocationStatus = document.getElementById('mediaLocationStatus');
    let canManageMediaLocation = false;
    let mediaLocationBusy = false;
    const launchWorkflowEnabled = document.getElementById('launchWorkflowEnabled');
    const launchWorkflowDetails = document.getElementById('launchWorkflowDetails');
    const launchWorkflowDryRun = document.getElementById('launchWorkflowDryRun');
    const launchScheduleUrl = document.getElementById('launchScheduleUrl');
    const launchRefreshInterval = document.getElementById('launchRefreshInterval');
    const launchLookaheadHours = document.getElementById('launchLookaheadHours');
    const launchStateFile = document.getElementById('launchStateFile');
    const launchPlanId = document.getElementById('launchPlanId');
    const launchProviders = document.getElementById('launchProviders');
    const launchLocations = document.getElementById('launchLocations');
    const launchPads = document.getElementById('launchPads');
    const launchPreSeconds = document.getElementById('launchPreSeconds');
    const launchPostSeconds = document.getElementById('launchPostSeconds');
    const launchCameraPlans = document.getElementById('launchCameraPlans');
    const previewLaunchWorkflowBtn = document.getElementById('previewLaunchWorkflowBtn');
    const saveLaunchWorkflowBtn = document.getElementById('saveLaunchWorkflowBtn');
    const launchPreviewResult = document.getElementById('launchPreviewResult');
    const launchTestCamera = document.getElementById('launchTestCamera');
    const launchTestDuration = document.getElementById('launchTestDuration');
    const launchTestRecordBtn = document.getElementById('launchTestRecordBtn');
    const launchTestRecordResult = document.getElementById('launchTestRecordResult');
    const launchTestRecordingsList = document.getElementById('launchTestRecordingsList');
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
    const passwordModal = document.getElementById('passwordModal');
    const closePasswordModalBtn = document.getElementById('closePasswordModalBtn');
    const passwordForm = document.getElementById('passwordForm');
    const adminCurrentPassword = document.getElementById('adminCurrentPassword');
    const adminNewPassword = document.getElementById('adminNewPassword');
    const adminConfirmPassword = document.getElementById('adminConfirmPassword');
    const siteNameInput = document.getElementById('siteNameInput');
    const sitePublicInput = document.getElementById('sitePublicInput');
    const saveSiteNameBtn = document.getElementById('saveSiteNameBtn');
    const newCameraName = document.getElementById('newCameraName');
    const newCameraDisplayName = document.getElementById('newCameraDisplayName');
    const newCameraDescription = document.getElementById('newCameraDescription');
    const newCameraVendor = document.getElementById('newCameraVendor');
    const newCameraSnapshotTemplate = document.getElementById('newCameraSnapshotTemplate');
    const newCameraRtspTemplate = document.getElementById('newCameraRtspTemplate');
    const newCameraCaptureSource = document.getElementById('newCameraCaptureSource');
    const newCameraVisibility = document.getElementById('newCameraVisibility');
    const newCameraUrl = document.getElementById('newCameraUrl');
    const newCameraSnapshotUsername = document.getElementById('newCameraSnapshotUsername');
    const newCameraSnapshotPassword = document.getElementById('newCameraSnapshotPassword');
    const newCameraSnapshotAuthType = document.getElementById('newCameraSnapshotAuthType');
    const newCameraRtspUrl = document.getElementById('newCameraRtspUrl');
    const newCameraPtzRtspUrl = document.getElementById('newCameraPtzRtspUrl');
    const newCameraRtspRow = document.getElementById('newCameraRtspRow');
    const newCameraPtzRtspRow = document.getElementById('newCameraPtzRtspRow');
    const newCameraPtzCapabilityPan = document.getElementById('newCameraPtzCapabilityPan');
    const newCameraPtzCapabilityTilt = document.getElementById('newCameraPtzCapabilityTilt');
    const newCameraPtzCapabilityZoom = document.getElementById('newCameraPtzCapabilityZoom');
    const newCameraPtzCapabilityFocus = document.getElementById('newCameraPtzCapabilityFocus');
    const newCameraGo2rtcEnabled = document.getElementById('newCameraGo2rtcEnabled');
    const newCameraGo2rtcPreload = document.getElementById('newCameraGo2rtcPreload');
    const newCameraGo2rtcTransport = document.getElementById('newCameraGo2rtcTransport');
    const newCameraGo2rtcVideoMode = document.getElementById('newCameraGo2rtcVideoMode');
    const newCameraPtzTourEnabled = document.getElementById('newCameraPtzTourEnabled');
    const newCameraPtzTourAutoResume = document.getElementById('newCameraPtzTourAutoResume');
    const newCameraPtzPresetRows = document.getElementById('newCameraPtzPresetRows');
    const addNewCameraPtzPresetBtn = document.getElementById('addNewCameraPtzPresetBtn');
    const toggleNewCameraUrlBtn = document.getElementById('toggleNewCameraUrlBtn');
    const toggleNewCameraSnapshotPasswordBtn = document.getElementById('toggleNewCameraSnapshotPasswordBtn');
    const toggleNewCameraRtspUrlBtn = document.getElementById('toggleNewCameraRtspUrlBtn');
    const toggleNewCameraPtzRtspUrlBtn = document.getElementById('toggleNewCameraPtzRtspUrlBtn');
    const loadNewCameraPtzPresetsBtn = document.getElementById('loadNewCameraPtzPresetsBtn');
    const testNewCameraBtn = document.getElementById('testNewCameraBtn');
    const confirmNewCameraBtn = document.getElementById('confirmNewCameraBtn');
    const newCameraModalStatus = document.getElementById('newCameraModalStatus');
    const newCameraTestResult = document.getElementById('newCameraTestResult');
    const configFormContainer = document.getElementById('configFormContainer');
    const appFooter = document.getElementById('appFooter');
    const statusMessage = document.getElementById('statusMessage');
    let loadedConfigData = null;
    let loadedConfigVersion = null;
    let canEditFullConfig = true;
    let newCameraLastTest = null;
    let usersPayload = { users: [], cameras: [] };
    let cameraFormMode = 'add';
    let editingCameraName = null;
    let editingOriginalCamera = null;
    let currentCameraOrder = [];
    const DEFAULT_LAUNCH_SCHEDULE_URL = 'https://ll.thespacedevs.com/2.3.0/launches/upcoming/?format=json&limit=100&ordering=net';
    const DEFAULT_LAUNCH_STATE_FILE = '/srv/fenetre/data/launch_workflow_state.json';
    const CAMERA_TEMPLATE_GROUPS = {
        generic: {
            snapshots: [
                { id: 'generic-snapshot', label: 'Generic /snapshot.jpg', url: 'http://CAMERA_IP:HTTP_PORT/snapshot.jpg', auth: 'basic' },
                { id: 'generic-images-snapshot', label: 'Generic /images/snapshot.jpg', url: 'http://CAMERA_IP:HTTP_PORT/images/snapshot.jpg', auth: 'basic' }
            ],
            rtsp: [
                { id: 'generic-stream1', label: 'Generic stream1', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/stream1' },
                { id: 'generic-11', label: 'Generic /11', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/11' }
            ]
        },
        reolink: {
            snapshots: [
                { id: 'reolink-api', label: 'Reolink API Snap', url: 'http://CAMERA_IP:HTTP_PORT/cgi-bin/api.cgi?cmd=Snap&channel=0&rs=fenetre&user=USERNAME&password=PASSWORD', auth: 'basic' }
            ],
            rtsp: [
                { id: 'reolink-main', label: 'Main stream', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/h264Preview_01_main' },
                { id: 'reolink-sub', label: 'Sub stream', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/h264Preview_01_sub' }
            ]
        },
        sunba: {
            snapshots: [
                { id: 'sunba-images', label: 'Sunba /images/snapshot.jpg', url: 'http://CAMERA_IP:HTTP_PORT/images/snapshot.jpg', auth: 'basic' },
                { id: 'sunba-cgi-legacy', label: 'Sunba legacy CGI', url: 'http://CAMERA_IP:HTTP_PORT/cgi-bin/snapshot.cgi?chn=0&u=USERNAME&p=PASSWORD', auth: 'basic' }
            ],
            rtsp: [
                { id: 'sunba-video1', label: 'Main stream /media/video1', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/media/video1' },
                { id: 'sunba-video2', label: 'Sub stream /media/video2', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/media/video2' }
            ]
        },
        hikvision: {
            snapshots: [
                { id: 'hikvision-picture', label: 'Hikvision picture', url: 'http://CAMERA_IP:HTTP_PORT/ISAPI/Streaming/channels/101/picture', auth: 'digest' }
            ],
            rtsp: [
                { id: 'hikvision-main', label: 'Channel 101 main', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/Streaming/Channels/101' },
                { id: 'hikvision-sub', label: 'Channel 102 sub', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/Streaming/Channels/102' }
            ]
        },
        ubiquiti: {
            snapshots: [
                { id: 'ubiquiti-snap', label: 'Ubiquiti snap.jpeg', url: 'http://CAMERA_IP:HTTP_PORT/snap.jpeg', auth: 'basic' }
            ],
            rtsp: [
                { id: 'ubiquiti-s0', label: 'Stream s0', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/s0' },
                { id: 'ubiquiti-s1', label: 'Stream s1', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/s1' },
                { id: 'ubiquiti-s2', label: 'Stream s2', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/s2' }
            ]
        },
        dahua: {
            snapshots: [
                { id: 'dahua-snapshot', label: 'Dahua channel 1 snapshot', url: 'http://CAMERA_IP:HTTP_PORT/cgi-bin/snapshot.cgi?channel=1', auth: 'digest' }
            ],
            rtsp: [
                { id: 'dahua-main', label: 'Channel 1 main', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/cam/realmonitor?channel=1&subtype=0' },
                { id: 'dahua-sub', label: 'Channel 1 sub', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/cam/realmonitor?channel=1&subtype=1' }
            ]
        },
        amcrest: {
            snapshots: [
                { id: 'amcrest-snapshot', label: 'Amcrest channel 1 snapshot', url: 'http://CAMERA_IP:HTTP_PORT/cgi-bin/snapshot.cgi?channel=1', auth: 'digest' }
            ],
            rtsp: [
                { id: 'amcrest-main', label: 'Channel 1 main', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/cam/realmonitor?channel=1&subtype=0' },
                { id: 'amcrest-sub', label: 'Channel 1 sub', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/cam/realmonitor?channel=1&subtype=1' }
            ]
        },
        axis: {
            snapshots: [
                { id: 'axis-jpg', label: 'Axis jpg image', url: 'http://CAMERA_IP:HTTP_PORT/axis-cgi/jpg/image.cgi', auth: 'basic' }
            ],
            rtsp: [
                { id: 'axis-media', label: 'Axis media stream', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/axis-media/media.amp' }
            ]
        },
        tplink: {
            snapshots: [
                { id: 'tplink-snapshot', label: 'TP-Link snapshot.jpg', url: 'http://CAMERA_IP:HTTP_PORT/stream/snapshot.jpg', auth: 'basic' }
            ],
            rtsp: [
                { id: 'tplink-stream1', label: 'Stream 1', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/stream1' },
                { id: 'tplink-stream2', label: 'Stream 2', url: 'rtsp://USERNAME:PASSWORD@CAMERA_IP:554/stream2' }
            ]
        }
    };

    loadConfigBtn.addEventListener('click', fetchAndDisplayConfig);
    saveConfigBtn.addEventListener('click', saveConfiguration);
    reloadAppBtn.addEventListener('click', reloadApplication);
    rebuildCamerasBtn.addEventListener('click', rebuildCamerasJson);
    syncUiBtn.addEventListener('click', syncUI);
    changeAdminPasswordBtn.addEventListener('click', openPasswordModal);
    adminLogoutBtn.addEventListener('click', logoutAdmin);
    addCameraBtn.addEventListener('click', handleAddCamera);
    editCameraBtn.addEventListener('click', handleEditCamera);
    editCameraSelect.addEventListener('change', () => {
        editCameraBtn.disabled = !editCameraSelect.value;
    });
    sortCameraOrderBtn.addEventListener('click', sortCameraOrderByName);
    saveCameraOrderBtn.addEventListener('click', saveCameraOrder);
    cameraOrderList.addEventListener('click', event => {
        const button = event.target.closest('button[data-order-action]');
        if (!button) {
            return;
        }
        moveCameraOrder(button.dataset.camera, button.dataset.orderAction);
    });
    closeAddCameraModalBtn.addEventListener('click', () => hideModal(addCameraModal));
    manageUsersBtn.addEventListener('click', openUserManager);
    closeUserModalBtn.addEventListener('click', () => hideModal(userModal));
    closePasswordModalBtn.addEventListener('click', () => hideModal(passwordModal));
    refreshStorageBtn.addEventListener('click', loadStorageSummary);
    previewMediaDirBtn.addEventListener('click', () => submitMediaLocation(true));
    relocateMediaDirBtn.addEventListener('click', () => submitMediaLocation(false));
    previewLaunchWorkflowBtn.addEventListener('click', previewLaunchWorkflow);
    saveLaunchWorkflowBtn.addEventListener('click', saveLaunchWorkflow);
    launchWorkflowEnabled.addEventListener('change', syncLaunchWorkflowEnabledState);
    if (launchTestRecordBtn) {
        launchTestRecordBtn.addEventListener('click', startTestRecording);
    }
    if (launchTestRecordingsList) {
        launchTestRecordingsList.addEventListener('click', event => {
            const button = event.target.closest('.launch-test-recording-delete-btn');
            if (button) {
                deleteTestRecording(button.dataset.filename);
            }
        });
    }
    newUserBtn.addEventListener('click', clearUserForm);
    userForm.addEventListener('submit', saveUser);
    passwordForm.addEventListener('submit', changeAdminPassword);
    deleteUserBtn.addEventListener('click', deleteUser);
    saveSiteNameBtn.addEventListener('click', saveSiteName);
    toggleNewCameraUrlBtn.addEventListener('click', () => toggleSensitiveInput(newCameraUrl, toggleNewCameraUrlBtn));
    toggleNewCameraSnapshotPasswordBtn.addEventListener('click', () => toggleSensitiveInput(newCameraSnapshotPassword, toggleNewCameraSnapshotPasswordBtn));
    toggleNewCameraRtspUrlBtn.addEventListener('click', () => toggleSensitiveInput(newCameraRtspUrl, toggleNewCameraRtspUrlBtn));
    toggleNewCameraPtzRtspUrlBtn.addEventListener('click', () => toggleSensitiveInput(newCameraPtzRtspUrl, toggleNewCameraPtzRtspUrlBtn));
    loadNewCameraPtzPresetsBtn.addEventListener('click', loadNewCameraPtzPresets);
    addNewCameraPtzPresetBtn.addEventListener('click', () => appendPtzPresetRow({ name: '', token: '', id: '' }));
    newCameraVendor.addEventListener('change', applyNewCameraTemplate);
    newCameraSnapshotTemplate.addEventListener('change', () => applySelectedSnapshotTemplate({ force: true }));
    newCameraRtspTemplate.addEventListener('change', () => applySelectedRtspTemplate({ force: true }));
    newCameraCaptureSource.addEventListener('change', () => {
        syncCaptureStreamFields();
        resetNewCameraTest();
    });
    newCameraGo2rtcEnabled.addEventListener('change', () => {
        syncCaptureStreamFields();
        resetNewCameraTest();
    });
    newCameraUrl.addEventListener('input', resetNewCameraTest);
    newCameraSnapshotUsername.addEventListener('input', () => {
        applySelectedRtspTemplate();
        resetNewCameraTest();
    });
    newCameraSnapshotPassword.addEventListener('input', () => {
        applySelectedRtspTemplate();
        resetNewCameraTest();
    });
    newCameraSnapshotAuthType.addEventListener('change', resetNewCameraTest);
    newCameraRtspUrl.addEventListener('input', resetNewCameraTest);
    newCameraPtzRtspUrl.addEventListener('input', resetNewCameraTest);
    testNewCameraBtn.addEventListener('click', testNewCameraSnapshot);
    confirmNewCameraBtn.addEventListener('click', confirmNewCameraAdd);
    [addCameraModal, userModal, passwordModal].forEach(modal => {
        modal.addEventListener('click', event => {
            if (event.target === modal) {
                hideModal(modal);
            }
        });
    });
    document.querySelectorAll('.option-toggle').forEach(toggle => {
        toggle.addEventListener('change', () => {
            syncOptionGroup(toggle);
            if (toggle.id === 'newCameraPtzEnabled') {
                syncCaptureStreamFields();
            }
        });
        syncOptionGroup(toggle);
    });

    function showModal(modal) {
        modal.hidden = false;
    }

    function hideModal(modal) {
        modal.hidden = true;
    }

    function setCameraModalStatus(message = '', type = 'info') {
        if (!newCameraModalStatus) {
            return;
        }
        newCameraModalStatus.textContent = message;
        newCameraModalStatus.className = message ? `modal-status ${type}` : 'modal-status';
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
        const cameras = data.cameras || [];
        const cameraRows = cameras.map(camera => {
            const effectiveLimit = camera.effective_limit_GB ?? camera.limit_GB;
            const cameraLimit = effectiveLimit ? `${effectiveLimit} GB` : 'not set';
            const configuredLimit = camera.limit_GB && camera.limit_GB !== effectiveLimit
                ? `, configured ${camera.limit_GB} GB`
                : '';
            return `<li><span>${escapeHtml(camera.name)}</span><strong>${escapeHtml(camera.display)}</strong><small>limit ${escapeHtml(cameraLimit)}${escapeHtml(configuredLimit)}</small></li>`;
        }).join('');
        storageSummary.innerHTML = `
            <div class="storage-total">
                <strong>${escapeHtml(data.display)}</strong>
                <span>of ${escapeHtml(limit)} global limit</span>
                <span class="storage-badge">${escapeHtml(status)}</span>
                <span>${escapeHtml(cameras.length)} camera${cameras.length === 1 ? '' : 's'}</span>
            </div>
            <div class="storage-path">${escapeHtml(data.work_dir || 'No work_dir configured')}</div>
            <ul class="storage-camera-list">${cameraRows || '<li><span>No configured cameras</span></li>'}</ul>
        `;
    }

    function configWriteDetails(result) {
        if (!result || !result.config_path) {
            return '';
        }
        const details = [`Path: ${result.config_path}`];
        if (result.mtime) {
            details.push(`mtime: ${result.mtime}`);
        }
        if (result.size_bytes) {
            details.push(`${result.size_bytes} bytes`);
        }
        if (result.go2rtc) {
            if (result.go2rtc.api_synced) {
                details.push(`go2rtc synced ${result.go2rtc.streams.length} stream(s)`);
                if ((result.go2rtc.preload_streams || []).length) {
                    details.push(`preloading ${result.go2rtc.preload_streams.length} aiming stream(s) after restart`);
                }
            } else if (result.go2rtc.warning) {
                details.push(result.go2rtc.warning);
            } else if (result.go2rtc.enabled === false) {
                details.push('go2rtc disabled or no RTSP streams');
            }
        }
        if (result.user_camera_access_removed && Object.keys(result.user_camera_access_removed).length) {
            details.push('removed deleted cameras from user PTZ access');
        }
        if (result.user_camera_access_replaced && Object.keys(result.user_camera_access_replaced).length) {
            details.push('updated user PTZ camera assignments');
        }
        if (result.ui_sync) {
            details.push(result.ui_sync.ok ? 'UI synced' : `UI sync warning: ${result.ui_sync.warning || 'unknown'}`);
        }
        if (result.cameras_json) {
            details.push(result.cameras_json.ok ? 'cameras.json rebuilt' : `cameras.json warning: ${result.cameras_json.warning || 'unknown'}`);
        }
        if (result.reload) {
            details.push(result.reload.ok ? 'app reload signaled' : `reload warning: ${result.reload.warning || 'unknown'}`);
        }
        return ` (${details.join(', ')})`;
    }

    function sitePublicValue() {
        return sitePublicInput.value !== 'private';
    }

    function setSiteAccessValue(isPublic) {
        sitePublicInput.value = isPublic === false ? 'private' : 'public';
    }

    function ensureGlobalUi(configData) {
        if (!configData.global || typeof configData.global !== 'object') {
            configData.global = {};
        }
        if (!configData.global.ui || typeof configData.global.ui !== 'object') {
            configData.global.ui = {};
        }
    }

    function syncSiteInputsFromConfig(configData) {
        siteNameInput.value = (configData.global && configData.global.deployment_name) || 'fenetre.cam';
        setSiteAccessValue((((configData.global || {}).ui || {}).public_site !== false));
    }

    function applySiteInputsToConfig(configData) {
        ensureGlobalUi(configData);
        const siteName = siteNameInput.value.trim();
        if (siteName) {
            configData.global.deployment_name = siteName;
        }
        configData.global.ui.public_site = sitePublicValue();
    }

    function syncSiteInputsToRenderedConfig() {
        const deploymentInput = document.getElementById('config.global.deployment_name');
        if (deploymentInput) {
            deploymentInput.value = siteNameInput.value.trim();
        }
        const publicSiteInput = document.getElementById('config.global.ui.public_site');
        if (publicSiteInput) {
            publicSiteInput.checked = sitePublicValue();
        }
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

    function describeMediaAction(action) {
        const labels = {
            already_linked: 'already linked, nothing to do',
            moved: action.dry_run ? 'would move existing files here' : 'moved existing files here',
            relinked: action.dry_run ? 'would re-link to the new location' : 're-linked to the new location',
            created: action.dry_run ? 'would create an empty directory here' : 'created an empty directory here',
            error: `failed: ${action.error || 'unknown error'}`
        };
        const label = labels[action.status] || action.status || 'unknown';
        return `${action.subdir}: ${label} (${action.source} -> ${action.target})`;
    }

    function renderMediaLocationResult(payload, dryRun) {
        mediaLocationStatus.classList.remove('error', 'success');
        if (payload.error) {
            mediaLocationStatus.classList.add('error');
            mediaLocationStatus.textContent = payload.error;
            return;
        }
        const relocation = payload.relocation || payload;
        const lines = (relocation.actions || []).map(describeMediaAction);
        mediaLocationStatus.classList.add(relocation.ok === false ? 'error' : 'success');
        const heading = dryRun
            ? 'Preview only, nothing was moved yet:'
            : (payload.message || 'Media storage relocated.');
        mediaLocationStatus.textContent = [heading, ...lines].join('\n');
    }

    async function loadMediaLocation() {
        try {
            const response = await fetch('/api/storage/media_location');
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || `HTTP error! status: ${response.status}`);
            }
            canManageMediaLocation = data.can_manage !== false;
            mediaDirInput.value = data.media_dir || '';
            mediaDirInput.disabled = !canManageMediaLocation;
            previewMediaDirBtn.disabled = !canManageMediaLocation;
            relocateMediaDirBtn.disabled = !canManageMediaLocation;
            mediaLocationStatus.classList.remove('error', 'success');
            mediaLocationStatus.textContent = canManageMediaLocation
                ? ''
                : 'Only superadmins can change the media storage location.';
        } catch (error) {
            mediaLocationStatus.classList.add('error');
            mediaLocationStatus.textContent = `Media storage location unavailable: ${error.message}`;
        }
    }

    async function submitMediaLocation(dryRun) {
        if (mediaLocationBusy || !canManageMediaLocation) {
            return;
        }
        const mediaDir = mediaDirInput.value.trim();
        if (!mediaDir) {
            mediaLocationStatus.classList.add('error');
            mediaLocationStatus.textContent = 'Enter a directory path first (e.g. /srv/fenetre/media).';
            return;
        }
        if (!dryRun && !window.confirm(
            `This moves existing photos and launch recordings into ${mediaDir}. Make sure that ` +
            'path is mounted into the container and has enough space. Continue?'
        )) {
            return;
        }
        mediaLocationBusy = true;
        previewMediaDirBtn.disabled = true;
        relocateMediaDirBtn.disabled = true;
        mediaLocationStatus.classList.remove('error', 'success');
        mediaLocationStatus.textContent = dryRun ? 'Checking planned changes...' : 'Relocating media storage...';
        try {
            const response = await fetch('/api/storage/media_location', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ media_dir: mediaDir, dry_run: dryRun })
            });
            const data = await response.json();
            if (!response.ok && !data.relocation && !data.actions) {
                throw new Error(data.error || `HTTP error! status: ${response.status}`);
            }
            renderMediaLocationResult(data, dryRun);
            if (!dryRun && response.ok) {
                await loadStorageSummary();
            }
        } catch (error) {
            mediaLocationStatus.classList.add('error');
            mediaLocationStatus.textContent = `Media storage relocation failed: ${error.message}`;
        } finally {
            mediaLocationBusy = false;
            previewMediaDirBtn.disabled = !canManageMediaLocation;
            relocateMediaDirBtn.disabled = !canManageMediaLocation;
        }
    }

    function ensureGlobalLaunchWorkflow(configData) {
        if (!configData.global || typeof configData.global !== 'object') {
            configData.global = {};
        }
        if (!configData.global.launch_workflow || typeof configData.global.launch_workflow !== 'object') {
            configData.global.launch_workflow = {};
        }
        if (!configData.global.launch_workflow.plans || typeof configData.global.launch_workflow.plans !== 'object' || Array.isArray(configData.global.launch_workflow.plans)) {
            configData.global.launch_workflow.plans = {};
        }
        return configData.global.launch_workflow;
    }

    function csvFromList(value, fallback = '') {
        if (Array.isArray(value)) {
            return value.join(', ');
        }
        if (typeof value === 'string') {
            return value;
        }
        return fallback;
    }

    function csvToList(value) {
        return String(value || '')
            .split(',')
            .map(item => item.trim())
            .filter(Boolean);
    }

    function numberInputValue(input, fallback) {
        const value = parseFloat(input.value);
        return Number.isFinite(value) ? value : fallback;
    }

    function intInputValue(input, fallback) {
        const value = parseInt(input.value, 10);
        return Number.isFinite(value) ? value : fallback;
    }

    function syncLaunchWorkflowEnabledState() {
        const enabled = launchWorkflowEnabled.checked;
        launchWorkflowDetails.hidden = !enabled;
        previewLaunchWorkflowBtn.disabled = !enabled || !loadedConfigData;
        saveLaunchWorkflowBtn.disabled = !loadedConfigData || !canEditFullConfig;
        saveLaunchWorkflowBtn.title = canEditFullConfig
            ? ''
            : 'Only superadmins can save launch workflow settings.';
        if (!enabled) {
            launchPreviewResult.textContent = '';
        }
    }

    function selectedLaunchPlanId(workflow) {
        const plans = (workflow && workflow.plans && typeof workflow.plans === 'object' && !Array.isArray(workflow.plans))
            ? workflow.plans
            : {};
        const current = launchPlanId.value.trim();
        if (current && plans[current]) {
            return current;
        }
        if (plans['vandenberg-spacex']) {
            return 'vandenberg-spacex';
        }
        const ids = Object.keys(plans);
        return ids[0] || current || 'vandenberg-spacex';
    }

    function setLaunchCameraCardEnabled(card) {
        const enabled = card.querySelector('.launch-camera-use').checked;
        const recordEnabled = card.querySelector('.launch-camera-record').checked;
        card.classList.toggle('disabled', !enabled);
        card.querySelectorAll('input, select').forEach(input => {
            if (input.classList.contains('launch-camera-use')) {
                return;
            }
            const isRecordField = input.classList.contains('launch-record-field');
            input.disabled = !enabled || (isRecordField && !recordEnabled);
        });
    }

    function appendOption(select, value, label) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label;
        select.appendChild(option);
    }

    function presetOptionValue(preset) {
        return String(preset.token || preset.id || preset.name || '').trim();
    }

    function cameraImageProfileNames(camera) {
        const imageProfiles = camera.image_profiles || camera.image_settings_profiles || {};
        const profiles = imageProfiles.profiles || {};
        return profiles && typeof profiles === 'object' ? Object.keys(profiles).sort() : [];
    }

    function cameraHasPtz(camera) {
        const ptz = (camera && camera.ptz) || {};
        return ptz.enabled === true;
    }

    function cameraHasTour(camera) {
        const ptz = (camera && camera.ptz) || {};
        const tour = ptz.tour || {};
        return cameraHasPtz(camera) && (tour.enabled === true || ptz.tour_enabled === true);
    }

    function textForVendorDetection(cameraName, camera) {
        return [
            cameraName,
            camera.description,
            camera.url,
            camera.rtsp_url,
            camera.ptz_rtsp_url,
            camera.local_command,
            ((camera.image_profiles || {}).vendor || '')
        ].join(' ').toLowerCase();
    }

    function inferredLaunchRecordVendor(cameraName, camera, record = {}) {
        const explicit = String(record.vendor || record.recording_vendor || '').trim().toLowerCase();
        if (explicit) {
            return explicit;
        }
        const text = textForVendorDetection(cameraName, camera);
        if (text.includes('reolink') || text.includes('h264preview') || text.includes('rlc-811') || text.includes('rlc811')) {
            return 'reolink';
        }
        if (text.includes('sunba') || text.includes('p636')) {
            return 'local_rtsp';
        }
        return 'custom';
    }

    function defaultLaunchDownloadPath(configData, cameraName) {
        const workDir = (((configData || {}).global || {}).work_dir || '/srv/fenetre/data').replace(/\/+$/, '');
        return `${workDir}/launches/{launch_id}/{launch_id}-{camera}.mp4`;
    }

    function defaultLaunchRecordForCamera(configData, cameraName, camera, record = {}) {
        const vendor = inferredLaunchRecordVendor(cameraName, camera, record);
        const defaults = {
            vendor,
            download_path: defaultLaunchDownloadPath(configData, cameraName),
            scheme: 'http',
            http_port: 80,
            channel: 0,
            stream_type: 'main',
            manual_record: true,
            manual_record_duration_s: 1200,
            download_method: 'Download',
            skip_when_full_viewers: true
        };
        if (vendor === 'reolink') {
            return defaults;
        }
        if (vendor === 'local_rtsp') {
            return {
                vendor: 'local_rtsp',
                download_path: defaults.download_path,
                skip_when_full_viewers: false
            };
        }
        return { vendor: 'custom', download_path: defaults.download_path, skip_when_full_viewers: true };
    }

    function launchRecordNote(vendor, cameraName, camera) {
        if (vendor === 'reolink') {
            return 'Reolink uses SetManualRec for start/stop, then Search plus Download for MP4 retrieval.';
        }
        if (vendor === 'local_rtsp') {
            return 'Local RTSP records the camera full RTSP stream on the Fenetre server with ffmpeg. It uses rtsp_url, not the low-res PTZ aiming stream.';
        }
        const text = textForVendorDetection(cameraName, camera);
        if (text.includes('sunba') || text.includes('p636')) {
            return 'Sunba P636 V2 has no verified public local-recording HTTP API; use Local RTSP recording or custom tested hooks.';
        }
        return 'Custom hooks run only when URL or command fields are configured.';
    }

    function appendLaunchField(container, labelText, input, vendor = '') {
        const label = document.createElement('label');
        label.textContent = labelText;
        if (vendor) {
            label.dataset.recordVendor = vendor;
            input.dataset.recordVendor = vendor;
        }
        container.appendChild(label);
        container.appendChild(input);
        return input;
    }

    function syncLaunchRecordVendorState(card) {
        const vendorSelect = card.querySelector('.launch-camera-record-vendor');
        if (!vendorSelect) {
            return;
        }
        const vendor = vendorSelect.value || 'custom';
        card.querySelectorAll('[data-record-vendor]').forEach(element => {
            element.hidden = element.dataset.recordVendor !== vendor;
        });
        const note = card.querySelector('.launch-camera-record-note');
        if (note) {
            note.textContent = launchRecordNote(vendor, card.dataset.camera || '', card._cameraConfig || {});
        }
        const enabled = card.querySelector('.launch-camera-use')?.checked;
        const recordEnabled = card.querySelector('.launch-camera-record')?.checked;
        card.querySelectorAll('.launch-reolink-test-button').forEach(button => {
            button.disabled = !enabled || !recordEnabled || vendor !== 'reolink';
        });
    }

    function collectLaunchRecordFromCard(card) {
        const vendor = (card.querySelector('.launch-camera-record-vendor') || {}).value || 'custom';
        const record = {
            download_path: card.querySelector('.launch-camera-download-path').value.trim(),
            download_delay_seconds: intInputValue(card.querySelector('.launch-camera-download-delay'), 60),
            skip_when_full_viewers: card.querySelector('.launch-camera-skip-full-viewers').checked
        };
        if (vendor === 'custom') {
            record.start_url = card.querySelector('.launch-camera-start-url').value.trim();
            record.stop_url = card.querySelector('.launch-camera-stop-url').value.trim();
            record.download_url = card.querySelector('.launch-camera-download-url').value.trim();
        }
        if (vendor === 'reolink') {
            record.vendor = 'reolink';
            record.scheme = (card.querySelector('.launch-camera-api-scheme') || {}).value || 'http';
            record.http_port = intInputValue(card.querySelector('.launch-camera-http-port'), 80);
            record.channel = intInputValue(card.querySelector('.launch-camera-channel'), 0);
            record.stream_type = (card.querySelector('.launch-camera-stream-type') || {}).value || 'main';
            record.manual_record = (card.querySelector('.launch-camera-manual-record') || {}).checked !== false;
            record.manual_record_duration_s = intInputValue(card.querySelector('.launch-camera-manual-duration'), 1200);
            record.download_method = (card.querySelector('.launch-camera-download-method') || {}).value || 'Download';
        }
        if (vendor === 'local_rtsp') {
            record.vendor = 'local_rtsp';
        }
        Object.keys(record).forEach(key => {
            if (record[key] === '' || record[key] === null || record[key] === undefined) {
                delete record[key];
            }
        });
        return record;
    }

    function formatLaunchTestResult(result) {
        if (!result || !result.ok) {
            return (result && result.error) || 'Test failed.';
        }
        const parts = [
            `${result.command || result.kind || 'Reolink test'} ${result.dry_run ? 'dry run' : 'ok'}`
        ];
        if (result.url) {
            parts.push(result.url);
        }
        if (result.download_path) {
            parts.push(`path: ${result.download_path}`);
        }
        if (Array.isArray(result.downloads)) {
            parts.push(`downloads: ${result.downloads.length}`);
        }
        if (result.search && Array.isArray(result.search.files)) {
            parts.push(`files found: ${result.search.files.length}`);
        }
        return parts.join(' | ');
    }

    async function runReolinkRecordingTest(card, action, dryRun) {
        const status = card.querySelector('.launch-reolink-test-status');
        try {
            const record = collectLaunchRecordFromCard(card);
            if (action === 'record_start') {
                const durationInput = card.querySelector('.launch-camera-reolink-test-duration');
                record.manual_record_duration_s = intInputValue(durationInput, 15);
            }
            const windowInput = card.querySelector('.launch-camera-reolink-test-window');
            if (status) {
                status.textContent = 'Running Reolink test...';
            }
            const response = await fetch('/api/launches/reolink_test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    camera: card.dataset.camera,
                    action,
                    dry_run: dryRun,
                    window_seconds: intInputValue(windowInput, 900),
                    record
                })
            });
            const result = await response.json();
            if (!response.ok || !result.ok) {
                throw new Error(result.error || `Reolink test failed with HTTP ${response.status}`);
            }
            if (status) {
                status.textContent = formatLaunchTestResult(result);
            }
            setStatus('Reolink recording test completed.', 'success');
        } catch (error) {
            if (status) {
                status.textContent = error.message;
            }
            setStatus(`Reolink recording test failed: ${error.message}`, 'error');
        }
    }

    function renderLaunchCameraPlans(configData, plan = {}) {
        const cameras = (configData && configData.cameras) || {};
        const names = Object.keys(cameras).sort();
        launchCameraPlans.innerHTML = '';
        if (!names.length) {
            launchCameraPlans.textContent = 'No cameras configured.';
            return;
        }
        const planCameras = (plan.cameras && typeof plan.cameras === 'object') ? plan.cameras : {};
        names.forEach(cameraName => {
            const camera = cameras[cameraName] || {};
            const ptz = camera.ptz || {};
            const ptzEnabled = cameraHasPtz(camera);
            const tourEnabled = cameraHasTour(camera);
            const cameraPlan = planCameras[cameraName] || {};
            const record = (cameraPlan.record && typeof cameraPlan.record === 'object') ? cameraPlan.record : {};
            const recordDefaults = defaultLaunchRecordForCamera(configData, cameraName, camera, record);
            const recordView = { ...recordDefaults, ...record };
            const card = document.createElement('div');
            card.className = 'launch-camera-card';
            card.dataset.camera = cameraName;
            card._cameraConfig = camera;

            const header = document.createElement('div');
            header.className = 'launch-camera-header';
            const title = document.createElement('h4');
            title.textContent = `${cameraName}${ptzEnabled ? ' PTZ' : ''}`;
            const enabledLabel = document.createElement('label');
            enabledLabel.className = 'launch-camera-enabled';
            const enabledInput = document.createElement('input');
            enabledInput.type = 'checkbox';
            enabledInput.className = 'launch-camera-use';
            enabledInput.checked = Boolean(planCameras[cameraName]);
            enabledLabel.appendChild(enabledInput);
            enabledLabel.appendChild(document.createTextNode('Rocket launch camera'));
            header.appendChild(title);
            header.appendChild(enabledLabel);
            card.appendChild(header);

            const fields = document.createElement('div');
            fields.className = 'launch-camera-fields';

            if (ptzEnabled) {
                const presetLabel = document.createElement('label');
                presetLabel.textContent = 'PTZ preset';
                const presetSelect = document.createElement('select');
                presetSelect.className = 'launch-camera-preset';
                appendOption(presetSelect, '', 'No PTZ move');
                (ptz.presets || [])
                    .filter(presetHasUsableName)
                    .forEach(preset => appendOption(
                        presetSelect,
                        presetOptionValue(preset),
                        preset.name || presetOptionValue(preset)
                    ));
                if (cameraPlan.preset) {
                    const presetValue = String(cameraPlan.preset);
                    if (!Array.from(presetSelect.options).some(option => option.value === presetValue)) {
                        appendOption(presetSelect, presetValue, presetValue);
                    }
                    presetSelect.value = presetValue;
                }
                fields.appendChild(presetLabel);
                fields.appendChild(presetSelect);
            }

            const imageProfileLabel = document.createElement('label');
            imageProfileLabel.textContent = 'Image profile';
            const imageProfileSelect = document.createElement('select');
            imageProfileSelect.className = 'launch-camera-image-profile';
            appendOption(imageProfileSelect, '', 'Do not change image profile');
            cameraImageProfileNames(camera).forEach(profileName => appendOption(imageProfileSelect, profileName, profileName));
            if (cameraPlan.image_profile) {
                const imageProfile = String(cameraPlan.image_profile);
                if (!Array.from(imageProfileSelect.options).some(option => option.value === imageProfile)) {
                    appendOption(imageProfileSelect, imageProfile, imageProfile);
                }
                imageProfileSelect.value = imageProfile;
            }
            fields.appendChild(imageProfileLabel);
            fields.appendChild(imageProfileSelect);

            const optionsLabel = document.createElement('label');
            optionsLabel.textContent = 'Launch options';
            const options = document.createElement('div');
            options.className = 'launch-inline-options';
            const recordConfigured = Boolean(record.vendor || record.start_url || record.start_command || record.stop_url || record.stop_command || record.download_url || record.download_command);
            const launchOptions = [
                ['launch-camera-record', 'Record on camera', recordConfigured],
                ['launch-camera-skip-full-viewers', 'Keep HD stream if watched', record.skip_when_full_viewers !== false],
            ];
            if (tourEnabled) {
                launchOptions.unshift(['launch-camera-pause-tour', 'Pause/resume tour', Boolean(cameraPlan.pause_tour || cameraPlan.resume_tour)]);
            }
            launchOptions.forEach(([className, labelText, checkedValue]) => {
                const label = document.createElement('label');
                const input = document.createElement('input');
                input.type = 'checkbox';
                input.className = className;
                input.checked = checkedValue;
                label.appendChild(input);
                label.appendChild(document.createTextNode(labelText));
                options.appendChild(label);
            });
            fields.appendChild(optionsLabel);
            fields.appendChild(options);

            const vendorSelect = document.createElement('select');
            vendorSelect.className = 'launch-camera-record-vendor launch-record-field';
            appendOption(vendorSelect, 'custom', 'Custom hooks');
            appendOption(vendorSelect, 'reolink', 'Reolink camera API');
            appendOption(vendorSelect, 'local_rtsp', 'Local HD RTSP recording');
            vendorSelect.value = ['reolink', 'local_rtsp'].includes(recordView.vendor) ? recordView.vendor : 'custom';
            appendLaunchField(fields, 'Recording API', vendorSelect);

            const recordNote = document.createElement('div');
            recordNote.className = 'launch-camera-record-note';
            fields.appendChild(document.createElement('span'));
            fields.appendChild(recordNote);

            const schemeSelect = document.createElement('select');
            schemeSelect.className = 'launch-camera-api-scheme launch-record-field';
            appendOption(schemeSelect, 'http', 'HTTP');
            appendOption(schemeSelect, 'https', 'HTTPS');
            schemeSelect.value = recordView.scheme === 'https' || recordView.protocol === 'https' ? 'https' : 'http';
            appendLaunchField(fields, 'API protocol', schemeSelect, 'reolink');

            const manualRecordInput = document.createElement('input');
            manualRecordInput.type = 'checkbox';
            manualRecordInput.className = 'launch-camera-manual-record launch-record-field';
            manualRecordInput.checked = recordView.manual_record !== false && recordView.trigger_manual_record !== false;
            appendLaunchField(fields, 'Trigger manual recording', manualRecordInput, 'reolink');

            const httpPortInput = document.createElement('input');
            httpPortInput.type = 'number';
            httpPortInput.min = '1';
            httpPortInput.max = '65535';
            httpPortInput.className = 'launch-camera-http-port launch-record-field';
            httpPortInput.value = recordView.http_port ?? 80;
            appendLaunchField(fields, 'HTTP port', httpPortInput, 'reolink');

            const channelInput = document.createElement('input');
            channelInput.type = 'number';
            channelInput.min = '0';
            channelInput.className = 'launch-camera-channel launch-record-field';
            channelInput.value = recordView.channel ?? 0;
            appendLaunchField(fields, 'Channel', channelInput, 'reolink');

            const streamTypeSelect = document.createElement('select');
            streamTypeSelect.className = 'launch-camera-stream-type launch-record-field';
            appendOption(streamTypeSelect, 'main', 'Main stream');
            appendOption(streamTypeSelect, 'sub', 'Sub stream');
            streamTypeSelect.value = recordView.stream_type === 'sub' ? 'sub' : 'main';
            appendLaunchField(fields, 'Recording stream', streamTypeSelect, 'reolink');

            const durationInput = document.createElement('input');
            durationInput.type = 'number';
            durationInput.min = '1';
            durationInput.className = 'launch-camera-manual-duration launch-record-field';
            durationInput.value = recordView.manual_record_duration_s ?? 1200;
            appendLaunchField(fields, 'Manual duration seconds', durationInput, 'reolink');

            const downloadMethodSelect = document.createElement('select');
            downloadMethodSelect.className = 'launch-camera-download-method launch-record-field';
            appendOption(downloadMethodSelect, 'Download', 'Download');
            appendOption(downloadMethodSelect, 'Playback', 'Playback');
            downloadMethodSelect.value = recordView.download_method === 'Playback' ? 'Playback' : 'Download';
            appendLaunchField(fields, 'Download method', downloadMethodSelect, 'reolink');

            [
                ['Record start URL', 'launch-camera-start-url', record.start_url || '', 'custom'],
                ['Record stop URL', 'launch-camera-stop-url', record.stop_url || '', 'custom'],
                ['Download URL', 'launch-camera-download-url', record.download_url || '', 'custom'],
                ['Download path', 'launch-camera-download-path', recordView.download_path || '', ''],
            ].forEach(([labelText, className, value, vendor]) => {
                const input = document.createElement('input');
                input.type = 'text';
                input.autocomplete = 'off';
                input.className = `${className} launch-record-field`;
                input.value = value;
                appendLaunchField(fields, labelText, input, vendor);
            });

            const delayLabel = document.createElement('label');
            delayLabel.textContent = 'Download delay seconds';
            const delayInput = document.createElement('input');
            delayInput.type = 'number';
            delayInput.min = '0';
            delayInput.className = 'launch-camera-download-delay launch-record-field';
            delayInput.value = record.download_delay_seconds ?? 60;
            fields.appendChild(delayLabel);
            fields.appendChild(delayInput);

            const reolinkTools = document.createElement('div');
            reolinkTools.className = 'launch-reolink-tools';
            reolinkTools.dataset.recordVendor = 'reolink';
            reolinkTools.innerHTML = `
                <strong>Reolink test utility</strong>
                <label>Test duration seconds <input class="launch-camera-reolink-test-duration launch-record-field" type="number" min="1" max="300" value="15"></label>
                <label>Pull recent seconds <input class="launch-camera-reolink-test-window launch-record-field" type="number" min="60" max="86400" value="900"></label>
                <div class="launch-reolink-actions">
                    <button class="launch-reolink-test-button" data-reolink-action="record_start" data-dry-run="true" type="button">Dry run start</button>
                    <button class="launch-reolink-test-button" data-reolink-action="record_start" data-dry-run="false" type="button">Start test recording</button>
                    <button class="launch-reolink-test-button" data-reolink-action="record_stop" data-dry-run="false" type="button">Stop recording</button>
                    <button class="launch-reolink-test-button" data-reolink-action="download_recording" data-dry-run="false" type="button">Pull recent file</button>
                </div>
                <div class="launch-reolink-test-status" aria-live="polite"></div>
            `;
            fields.appendChild(document.createElement('span'));
            fields.appendChild(reolinkTools);

            card.appendChild(fields);
            card.querySelectorAll('input, select').forEach(input => {
                input.addEventListener('change', () => {
                    syncLaunchRecordVendorState(card);
                    setLaunchCameraCardEnabled(card);
                });
            });
            card.querySelectorAll('.launch-reolink-test-button').forEach(button => {
                button.addEventListener('click', () => {
                    runReolinkRecordingTest(
                        card,
                        button.dataset.reolinkAction,
                        button.dataset.dryRun !== 'false'
                    );
                });
            });
            launchCameraPlans.appendChild(card);
            syncLaunchRecordVendorState(card);
            setLaunchCameraCardEnabled(card);
        });
    }

    function syncLaunchInputsFromConfig(configData) {
        const workflow = ((configData.global || {}).launch_workflow || {});
        launchWorkflowEnabled.checked = workflow.enabled === true;
        launchWorkflowDryRun.checked = workflow.dry_run !== false;
        launchScheduleUrl.value = workflow.schedule_url || DEFAULT_LAUNCH_SCHEDULE_URL;
        launchRefreshInterval.value = workflow.refresh_interval_s ?? 900;
        launchLookaheadHours.value = workflow.lookahead_hours ?? 168;
        launchStateFile.value = workflow.state_file || DEFAULT_LAUNCH_STATE_FILE;

        const planId = selectedLaunchPlanId(workflow);
        const plans = workflow.plans && typeof workflow.plans === 'object' && !Array.isArray(workflow.plans)
            ? workflow.plans
            : {};
        const plan = plans[planId] || {};
        const match = plan.match || {};
        launchPlanId.value = planId;
        launchProviders.value = csvFromList(match.providers, 'SpaceX');
        launchLocations.value = csvFromList(match.locations, 'Vandenberg');
        launchPads.value = csvFromList(match.pads, '');
        launchPreSeconds.value = plan.pre_seconds ?? workflow.default_pre_seconds ?? 60;
        launchPostSeconds.value = plan.post_seconds ?? workflow.default_post_seconds ?? 900;
        renderLaunchCameraPlans(configData, plan);
        syncLaunchWorkflowEnabledState();
        populateLaunchTestCameraOptions(configData);
    }

    function populateLaunchTestCameraOptions(configData) {
        if (!launchTestCamera) {
            return;
        }
        const cameras = (configData && configData.cameras) || {};
        const previousValue = launchTestCamera.value;
        launchTestCamera.innerHTML = '';
        const names = orderedCameraNames(configData).filter(name => (cameras[name] || {}).rtsp_url);
        if (!names.length) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = 'No cameras have rtsp_url set';
            launchTestCamera.appendChild(option);
            launchTestCamera.disabled = true;
            return;
        }
        launchTestCamera.disabled = false;
        names.forEach(name => {
            const option = document.createElement('option');
            option.value = name;
            option.textContent = cameraDisplayName(name, cameras[name] || {});
            launchTestCamera.appendChild(option);
        });
        if (names.includes(previousValue)) {
            launchTestCamera.value = previousValue;
        }
    }

    function formatBytes(bytes) {
        const value = Number(bytes) || 0;
        if (value < 1024) {
            return `${value} B`;
        }
        const units = ['KB', 'MB', 'GB'];
        let scaled = value;
        let unitIndex = -1;
        while (scaled >= 1024 && unitIndex < units.length - 1) {
            scaled /= 1024;
            unitIndex += 1;
        }
        return `${scaled.toFixed(1)} ${units[Math.max(unitIndex, 0)]}`;
    }

    function renderTestRecordingsList(recordings) {
        if (!launchTestRecordingsList) {
            return;
        }
        if (!recordings.length) {
            launchTestRecordingsList.innerHTML = '<div class="launch-empty">No test recordings yet.</div>';
            return;
        }
        launchTestRecordingsList.innerHTML = recordings.map(recording => `
            <div class="launch-camera-card" data-filename="${escapeHtml(recording.filename)}">
                <strong>${escapeHtml(recording.camera || 'Camera')}</strong>
                <a href="${escapeHtml(recording.url)}" target="_blank" rel="noopener">${escapeHtml(recording.filename)}</a>
                <span>${escapeHtml(formatBytes(recording.bytes))}</span>
                <small>${escapeHtml(new Date(recording.modified_at).toLocaleString())}</small>
                <button type="button" class="launch-test-recording-delete-btn" data-filename="${escapeHtml(recording.filename)}">Delete</button>
            </div>
        `).join('');
    }

    async function loadTestRecordings() {
        if (!launchTestRecordingsList) {
            return;
        }
        try {
            const response = await fetch('/api/launches/local_rtsp_test');
            const result = await response.json();
            if (!response.ok || !result.ok) {
                throw new Error(result.error || `HTTP ${response.status}`);
            }
            renderTestRecordingsList(result.recordings || []);
        } catch (error) {
            launchTestRecordingsList.innerHTML = `<div class="launch-empty">Error loading test recordings: ${escapeHtml(error.message)}</div>`;
        }
    }

    async function startTestRecording() {
        const camera = launchTestCamera.value;
        if (!camera) {
            launchTestRecordResult.textContent = 'Select a camera with rtsp_url set first.';
            return;
        }
        const duration = intInputValue(launchTestDuration, 20);
        launchTestRecordBtn.disabled = true;
        launchTestRecordResult.textContent = `Recording ${duration}s from ${camera}...`;
        try {
            const response = await fetch('/api/launches/local_rtsp_test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ camera, duration_s: duration }),
            });
            const result = await response.json();
            if (!response.ok || !result.ok) {
                throw new Error(result.error || `HTTP ${response.status}`);
            }
            launchTestRecordResult.textContent = `Recorded ${formatBytes(result.bytes)} from ${camera} in ${result.elapsed_s}s.`;
            await loadTestRecordings();
        } catch (error) {
            launchTestRecordResult.textContent = `Error: ${error.message}`;
        } finally {
            launchTestRecordBtn.disabled = false;
        }
    }

    async function deleteTestRecording(filename) {
        try {
            const response = await fetch(`/api/launches/local_rtsp_test/file/${encodeURIComponent(filename)}`, {
                method: 'DELETE',
            });
            const result = await response.json();
            if (!response.ok || !result.ok) {
                throw new Error(result.error || `HTTP ${response.status}`);
            }
            await loadTestRecordings();
        } catch (error) {
            launchTestRecordResult.textContent = `Error deleting recording: ${error.message}`;
        }
    }

    function collectLaunchCameraPlans() {
        const cameras = {};
        launchCameraPlans.querySelectorAll('.launch-camera-card').forEach(card => {
            if (!card.querySelector('.launch-camera-use').checked) {
                return;
            }
            const cameraName = card.dataset.camera;
            const cameraPlan = {};
            const presetInput = card.querySelector('.launch-camera-preset');
            const preset = presetInput ? presetInput.value.trim() : '';
            const imageProfile = card.querySelector('.launch-camera-image-profile').value.trim();
            if (preset) {
                cameraPlan.preset = preset;
            }
            if (imageProfile) {
                cameraPlan.image_profile = imageProfile;
            }
            const pauseTourInput = card.querySelector('.launch-camera-pause-tour');
            if (pauseTourInput && pauseTourInput.checked) {
                cameraPlan.pause_tour = true;
                cameraPlan.resume_tour = true;
            }
            if (card.querySelector('.launch-camera-record').checked) {
                cameraPlan.record = collectLaunchRecordFromCard(card);
            }
            cameras[cameraName] = cameraPlan;
        });
        return cameras;
    }

    function applyLaunchInputsToConfig(configData) {
        const existingWorkflow = configData.global
            && typeof configData.global === 'object'
            && configData.global.launch_workflow
            && typeof configData.global.launch_workflow === 'object';
        if (!launchWorkflowEnabled.checked && !existingWorkflow) {
            return;
        }
        const workflow = ensureGlobalLaunchWorkflow(configData);
        if (!launchWorkflowEnabled.checked) {
            workflow.enabled = false;
            workflow.dry_run = launchWorkflowDryRun.checked;
            return;
        }
        const planId = launchPlanId.value.trim() || 'vandenberg-spacex';
        workflow.enabled = launchWorkflowEnabled.checked;
        workflow.dry_run = launchWorkflowDryRun.checked;
        workflow.schedule_url = launchScheduleUrl.value.trim() || DEFAULT_LAUNCH_SCHEDULE_URL;
        workflow.schedule_timeout_s = workflow.schedule_timeout_s ?? 10;
        workflow.refresh_interval_s = intInputValue(launchRefreshInterval, 900);
        workflow.lookahead_hours = numberInputValue(launchLookaheadHours, 168);
        workflow.default_pre_seconds = intInputValue(launchPreSeconds, 60);
        workflow.default_post_seconds = intInputValue(launchPostSeconds, 900);
        workflow.state_file = launchStateFile.value.trim() || DEFAULT_LAUNCH_STATE_FILE;
        const previousPlan = workflow.plans[planId] && typeof workflow.plans[planId] === 'object'
            ? workflow.plans[planId]
            : {};
        workflow.plans[planId] = {
            ...previousPlan,
            enabled: true,
            match: {
                providers: csvToList(launchProviders.value),
                locations: csvToList(launchLocations.value),
                pads: csvToList(launchPads.value),
            },
            pre_seconds: intInputValue(launchPreSeconds, 60),
            post_seconds: intInputValue(launchPostSeconds, 900),
            cameras: collectLaunchCameraPlans()
        };
    }

    function configWithLaunchInputs() {
        if (!loadedConfigData) {
            throw new Error('Load the configuration before using launch automation.');
        }
        const configData = structuredClone(loadedConfigData);
        applySiteInputsToConfig(configData);
        applyLaunchInputsToConfig(configData);
        return configData;
    }

    function renderLaunchPreview(data) {
        launchPreviewResult.innerHTML = '';
        if (!data || !data.ok) {
            launchPreviewResult.textContent = (data && data.error) || 'Launch preview failed.';
            return;
        }
        const events = data.events || [];
        const summary = document.createElement('div');
        summary.textContent = events.length
            ? `Found ${events.length} upcoming matched launch event(s).`
            : 'No upcoming launches matched this plan.';
        launchPreviewResult.appendChild(summary);
        if (!events.length) {
            return;
        }
        const list = document.createElement('ul');
        list.className = 'launch-preview-list';
        events.forEach(event => {
            const item = document.createElement('li');
            const launchDate = event.launch_time_utc
                ? new Date(event.launch_time_utc).toLocaleString()
                : 'Unknown time';
            const planText = (event.plans || []).map(plan => {
                const cameras = (plan.cameras || []).join(', ') || 'no cameras';
                return `${plan.id}: ${plan.phase}, ${cameras}`;
            }).join(' | ');
            item.innerHTML = `
                <strong>${escapeHtml(event.name)}</strong>
                <span>${escapeHtml(launchDate)}</span>
                <small>${escapeHtml([event.provider, event.location, event.pad].filter(Boolean).join(' - '))}</small>
                <span class="launch-preview-badge">${escapeHtml(planText || 'no matching plan')}</span>
            `;
            list.appendChild(item);
        });
        launchPreviewResult.appendChild(list);
    }

    async function previewLaunchWorkflow() {
        try {
            if (!launchWorkflowEnabled.checked) {
                throw new Error('Launch automation is disabled.');
            }
            setStatus('Loading launch preview...', 'info');
            const configData = configWithLaunchInputs();
            const response = await fetch('/api/launches/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ config: configData })
            });
            const result = await response.json();
            if (!response.ok || !result.ok) {
                throw new Error(result.error || `Launch preview failed with HTTP ${response.status}`);
            }
            renderLaunchPreview(result);
            setStatus('Launch preview loaded.', 'success');
        } catch (error) {
            launchPreviewResult.textContent = error.message;
            setStatus(`Launch preview failed: ${error.message}`, 'error');
        }
    }

    async function saveLaunchWorkflow() {
        try {
            const configData = configWithLaunchInputs();
            setStatus('Saving launch workflow...', 'info');
            const response = await fetch('/config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ config_version: loadedConfigVersion, config: configData }),
            });
            const result = await response.json();
            if (response.status === 409) {
                setStatus(result.error || 'Configuration changed elsewhere; reloading the latest version.', 'error');
                await fetchAndDisplayConfig();
                return;
            }
            if (!response.ok) {
                throw new Error(result.error || `HTTP error! status: ${response.status}`);
            }
            try {
                const reloadResult = await requestApplicationReload();
                result.reload = { ok: true, message: reloadResult.message || 'Reload signal sent successfully.' };
            } catch (reloadError) {
                result.reload = { ok: false, warning: reloadError.message };
            }
            loadedConfigData = configData;
            setStatus((result.message || 'Launch workflow saved.') + configWriteDetails(result), 'success');
            await fetchAndDisplayConfig();
        } catch (error) {
            setStatus(`Error saving launch workflow: ${error.message}`, 'error');
        }
    }

    function cameraDisplayName(cameraName, camera = {}) {
        return camera.display_name || cameraName;
    }

    function cameraNamesSortedByDisplayName(names, cameras) {
        return [...names].sort((left, right) => {
            const leftName = cameraDisplayName(left, cameras[left] || {}).toLocaleLowerCase();
            const rightName = cameraDisplayName(right, cameras[right] || {}).toLocaleLowerCase();
            return leftName.localeCompare(rightName) || left.localeCompare(right);
        });
    }

    function orderedCameraNames(configData = loadedConfigData) {
        const cameras = (configData && configData.cameras) || {};
        const names = Object.keys(cameras);
        const configuredOrder = (((configData.global || {}).ui || {}).camera_order || [])
            .filter(name => names.includes(name));
        const ordered = [];
        configuredOrder.forEach(name => {
            if (!ordered.includes(name)) {
                ordered.push(name);
            }
        });
        cameraNamesSortedByDisplayName(
            names.filter(name => !ordered.includes(name)),
            cameras
        ).forEach(name => ordered.push(name));
        return ordered;
    }

    function renderCameraOrderList(markDirty = false) {
        const cameras = (loadedConfigData && loadedConfigData.cameras) || {};
        currentCameraOrder = currentCameraOrder.filter(name => cameras[name]);
        cameraNamesSortedByDisplayName(
            Object.keys(cameras).filter(name => !currentCameraOrder.includes(name)),
            cameras
        ).forEach(name => currentCameraOrder.push(name));
        cameraOrderList.innerHTML = '';
        if (!currentCameraOrder.length) {
            cameraOrderList.innerHTML = '<div class="camera-order-empty">Load config to reorder cameras.</div>';
            sortCameraOrderBtn.disabled = true;
            saveCameraOrderBtn.disabled = true;
            return;
        }
        currentCameraOrder.forEach((cameraName, index) => {
            const camera = cameras[cameraName] || {};
            const row = document.createElement('div');
            row.className = 'camera-order-row';
            row.dataset.camera = cameraName;
            const displayName = cameraDisplayName(cameraName, camera);
            const nameWrap = document.createElement('div');
            nameWrap.className = 'camera-order-name';
            const strong = document.createElement('strong');
            strong.textContent = displayName;
            nameWrap.appendChild(strong);
            if (displayName !== cameraName) {
                const small = document.createElement('small');
                small.textContent = cameraName;
                nameWrap.appendChild(small);
            }
            const upButton = document.createElement('button');
            upButton.type = 'button';
            upButton.dataset.orderAction = 'up';
            upButton.dataset.camera = cameraName;
            upButton.disabled = index === 0;
            upButton.textContent = 'Up';
            const downButton = document.createElement('button');
            downButton.type = 'button';
            downButton.dataset.orderAction = 'down';
            downButton.dataset.camera = cameraName;
            downButton.disabled = index === currentCameraOrder.length - 1;
            downButton.textContent = 'Down';
            row.append(nameWrap, upButton, downButton);
            cameraOrderList.appendChild(row);
        });
        sortCameraOrderBtn.disabled = false;
        saveCameraOrderBtn.disabled = !markDirty;
    }

    function moveCameraOrder(cameraName, action) {
        const index = currentCameraOrder.indexOf(cameraName);
        if (index < 0) {
            return;
        }
        const delta = action === 'up' ? -1 : action === 'down' ? 1 : 0;
        const nextIndex = index + delta;
        if (nextIndex < 0 || nextIndex >= currentCameraOrder.length) {
            return;
        }
        [currentCameraOrder[index], currentCameraOrder[nextIndex]] = [currentCameraOrder[nextIndex], currentCameraOrder[index]];
        renderCameraOrderList(true);
    }

    function sortCameraOrderByName() {
        const cameras = (loadedConfigData && loadedConfigData.cameras) || {};
        currentCameraOrder = cameraNamesSortedByDisplayName(Object.keys(cameras), cameras);
        renderCameraOrderList(true);
    }

    async function saveCameraOrder() {
        if (!loadedConfigData) {
            setStatus('Load the configuration before saving camera order.', 'error');
            return;
        }
        setStatus('Saving camera order...', 'info');
        try {
            const response = await fetch('/api/global/camera_order', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ camera_order: currentCameraOrder })
            });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.error || `HTTP error! status: ${response.status}`);
            }
            try {
                const reloadResult = await requestApplicationReload();
                result.reload = { ok: true, message: reloadResult.message || 'Reload signal sent successfully.' };
            } catch (reloadError) {
                result.reload = { ok: false, warning: reloadError.message };
            }
            ensureGlobalUi(loadedConfigData);
            loadedConfigData.global.ui.camera_order = result.camera_order || currentCameraOrder;
            saveCameraOrderBtn.disabled = true;
            setStatus((result.message || 'Camera order saved.') + configWriteDetails(result), 'success');
            await fetchAndDisplayConfig();
        } catch (error) {
            setStatus(`Error saving camera order: ${error.message}`, 'error');
        }
    }

    function populateCameraEditOptions() {
        const cameras = (loadedConfigData && loadedConfigData.cameras) || {};
        const previousValue = editCameraSelect.value;
        editCameraSelect.innerHTML = '';
        const names = orderedCameraNames();
        if (!names.length) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = 'No cameras configured';
            editCameraSelect.appendChild(option);
            editCameraBtn.disabled = true;
            renderCameraOrderList(false);
            return;
        }
        names.forEach(cameraName => {
            const camera = cameras[cameraName] || {};
            const displayName = cameraDisplayName(cameraName, camera);
            const option = document.createElement('option');
            option.value = cameraName;
            option.textContent = displayName === cameraName ? cameraName : `${displayName} (${cameraName})`;
            editCameraSelect.appendChild(option);
        });
        editCameraSelect.value = names.includes(previousValue) ? previousValue : names[0];
        editCameraBtn.disabled = !editCameraSelect.value;
        currentCameraOrder = names;
        renderCameraOrderList(false);
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

    function presetHasUsableName(preset) {
        const id = String((preset && preset.id) || '').trim();
        const token = String((preset && preset.token) || '').trim();
        const name = String((preset && preset.name) || '').trim();
        if (!name) {
            return false;
        }
        return !(name === id && /^\d+$/.test(id) && (!token || token === id));
    }

    function appendPtzPresetRow(preset = {}) {
        const row = document.createElement('div');
        row.className = 'preset-row';

        const nameInput = document.createElement('input');
        nameInput.type = 'text';
        nameInput.className = 'preset-name';
        nameInput.placeholder = 'Display name';
        nameInput.setAttribute('aria-label', 'Preset display name');
        nameInput.value = preset.name || '';

        const tokenInput = document.createElement('input');
        tokenInput.type = 'text';
        tokenInput.className = 'preset-token';
        tokenInput.placeholder = 'Token';
        tokenInput.setAttribute('aria-label', 'Preset token');
        tokenInput.value = preset.token || preset.id || '';

        const idInput = document.createElement('input');
        idInput.type = 'text';
        idInput.className = 'preset-id';
        idInput.placeholder = 'ID';
        idInput.setAttribute('aria-label', 'Preset ID');
        idInput.value = preset.id || preset.token || '';

        const enabledLabel = document.createElement('label');
        enabledLabel.className = 'preset-enabled';
        const enabledInput = document.createElement('input');
        enabledInput.type = 'checkbox';
        enabledInput.className = 'preset-enabled-input';
        enabledInput.checked = preset.enabled !== false;
        enabledLabel.appendChild(enabledInput);
        enabledLabel.appendChild(document.createTextNode('Show'));

        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.textContent = 'Remove';
        removeButton.addEventListener('click', () => row.remove());

        row.appendChild(nameInput);
        row.appendChild(tokenInput);
        row.appendChild(idInput);
        row.appendChild(enabledLabel);
        row.appendChild(removeButton);
        newCameraPtzPresetRows.appendChild(row);
    }

    function renderPtzPresetEditor(presets = []) {
        newCameraPtzPresetRows.innerHTML = '';
        const presetList = Array.isArray(presets)
            ? presets
            : Object.entries(presets || {}).map(([presetId, preset]) => ({
                id: presetId,
                ...(preset && typeof preset === 'object' ? preset : {})
            }));
        presetList
            .filter(preset => preset && typeof preset === 'object')
            .map(preset => ({
                id: String(preset.id || preset.token || '').trim(),
                name: String(preset.name || '').trim(),
                token: String(preset.token || preset.id || '').trim(),
                enabled: preset.enabled !== false
            }))
            .filter(presetHasUsableName)
            .forEach(appendPtzPresetRow);
    }

    function collectPtzPresets() {
        return Array.from(newCameraPtzPresetRows.querySelectorAll('.preset-row'))
            .map(row => {
                const name = row.querySelector('.preset-name').value.trim();
                const token = row.querySelector('.preset-token').value.trim();
                const id = row.querySelector('.preset-id').value.trim();
                const enabledInput = row.querySelector('.preset-enabled-input');
                const preset = {
                    id: id || token || name,
                    name,
                    token: token || id || name,
                    enabled: !enabledInput || enabledInput.checked
                };
                return presetHasUsableName(preset) ? preset : null;
            })
            .filter(Boolean);
    }

    function presetMergeKeys(preset) {
        return [
            preset && preset.id,
            preset && preset.token
        ]
            .map(value => String(value || '').trim())
            .filter(Boolean);
    }

    function mergeDiscoveredPtzPresets(existingPresets, discoveredPresets) {
        const existingByKey = new Map();
        (existingPresets || []).forEach(preset => {
            presetMergeKeys(preset).forEach(key => existingByKey.set(key, preset));
        });
        const merged = [];
        const seen = new Set();
        (discoveredPresets || []).forEach(discovered => {
            const normalized = {
                id: String(discovered.id || discovered.token || '').trim(),
                name: String(discovered.name || '').trim(),
                token: String(discovered.token || discovered.id || '').trim(),
                enabled: true
            };
            const existing = presetMergeKeys(normalized)
                .map(key => existingByKey.get(key))
                .find(Boolean);
            if (existing) {
                normalized.id = String(existing.id || normalized.id).trim();
                normalized.name = String(existing.name || normalized.name).trim();
                normalized.token = String(existing.token || normalized.token).trim();
                normalized.enabled = existing.enabled !== false;
            }
            const identity = presetMergeKeys(normalized)[0];
            if (!identity || seen.has(identity)) {
                return;
            }
            seen.add(identity);
            merged.push(normalized);
        });
        (existingPresets || []).forEach(existing => {
            const identity = presetMergeKeys(existing)[0];
            if (identity && !seen.has(identity)) {
                seen.add(identity);
                merged.push(existing);
            }
        });
        return merged;
    }

    function syncAllOptionGroups() {
        document.querySelectorAll('.option-toggle').forEach(toggle => syncOptionGroup(toggle));
        syncCaptureStreamFields();
    }

    function syncCaptureStreamFields() {
        const captureSource = newCameraCaptureSource.value || 'snapshot';
        const ptzEnabled = checked('newCameraPtzEnabled');
        const liveViewEnabled = checked('newCameraGo2rtcEnabled');
        const showRtspUrl = captureSource === 'rtsp' || liveViewEnabled || ptzEnabled;
        const showPtzRtsp = ptzEnabled && liveViewEnabled;
        const rtspLabel = document.querySelector('label[for="newCameraRtspUrl"]');
        const ptzRtspLabel = document.querySelector('label[for="newCameraPtzRtspUrl"]');
        if (rtspLabel) {
            rtspLabel.textContent = captureSource === 'rtsp'
                ? 'RTSP capture/full live URL'
                : 'RTSP live URL';
        }
        if (ptzRtspLabel) {
            ptzRtspLabel.textContent = 'Low-res PTZ aiming RTSP URL (optional substream)';
        }
        newCameraRtspRow.hidden = !showRtspUrl;
        newCameraPtzRtspRow.hidden = !showPtzRtsp;
        newCameraRtspUrl.disabled = !showRtspUrl;
        newCameraPtzRtspUrl.disabled = !showPtzRtsp;
        newCameraGo2rtcTransport.disabled = !liveViewEnabled;
        newCameraGo2rtcVideoMode.disabled = !liveViewEnabled;
        newCameraGo2rtcPreload.disabled = !(ptzEnabled && liveViewEnabled);
    }

    function resetCameraForm() {
        cameraFormMode = 'add';
        editingCameraName = null;
        editingOriginalCamera = null;
        cameraModalTitle.textContent = 'Add Camera';
        confirmNewCameraBtn.textContent = 'Add Camera to Config';
        newCameraName.disabled = false;
        setInputValue('newCameraName', '');
        setInputValue('newCameraDisplayName', '');
        setInputValue('newCameraDescription', '');
        setCameraModalStatus('');
        setSelectValue('newCameraVendor', 'generic');
        setSelectValue('newCameraCaptureSource', 'snapshot');
        setInputValue('newCameraUrl', '');
        setInputValue('newCameraSnapshotUsername', '');
        setInputValue('newCameraSnapshotPassword', '');
        setSelectValue('newCameraSnapshotAuthType', 'basic');
        setInputValue('newCameraRtspUrl', '');
        setInputValue('newCameraPtzRtspUrl', '');
        resetSensitiveInput(newCameraUrl, toggleNewCameraUrlBtn);
        resetSensitiveInput(newCameraSnapshotPassword, toggleNewCameraSnapshotPasswordBtn);
        resetSensitiveInput(newCameraRtspUrl, toggleNewCameraRtspUrlBtn);
        resetSensitiveInput(newCameraPtzRtspUrl, toggleNewCameraPtzRtspUrlBtn);
        setSelectValue('newCameraVisibility', 'public');
        setCheckboxValue('newCameraCacheBust', true);
        setCheckboxValue('newCameraMozjpeg', true);
        setCheckboxValue('newCameraTimelapse', true);
        setCheckboxValue('newCameraGo2rtcEnabled', true);
        setCheckboxValue('newCameraGo2rtcPreload', true);
        setSelectValue('newCameraGo2rtcTransport', 'tcp');
        setSelectValue('newCameraGo2rtcVideoMode', 'copy');
        setInputValue('newCameraFailureRetry', 60);
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
        setCheckboxValue('newCameraPrivacyEnabled', true);
        setInputValue('newCameraPrivacyRadius', 1200);
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
        setCheckboxValue('newCameraPtzCapabilityPan', true);
        setCheckboxValue('newCameraPtzCapabilityTilt', true);
        setCheckboxValue('newCameraPtzCapabilityZoom', true);
        setCheckboxValue('newCameraPtzCapabilityFocus', false);
        setCheckboxValue('newCameraPtzTourEnabled', false);
        setInputValue('newCameraPtzTourAutoResume', 1800);
        setInputValue('newCameraPtzHost', '');
        setInputValue('newCameraPtzPort', 80);
        setInputValue('newCameraPtzUsername', '');
        setInputValue('newCameraPtzPassword', '');
        setInputValue('newCameraPtzProfileToken', '');
        renderPtzPresetEditor([]);
        applyNewCameraTemplate();
        resetNewCameraTest();
        syncAllOptionGroups();
    }

    function visibilityForCamera(camera) {
        if (['public', 'authenticated', 'hidden'].includes(camera.visibility)) {
            return camera.visibility;
        }
        if (camera.hidden === true) {
            return 'hidden';
        }
        if (camera.public === false) {
            return 'authenticated';
        }
        return 'public';
    }

    function globalPtzPreloadDefault() {
        const go2rtc = (((loadedConfigData || {}).global || {}).go2rtc || {});
        if (Object.prototype.hasOwnProperty.call(go2rtc, 'preload_ptz_streams')) {
            return go2rtc.preload_ptz_streams === true;
        }
        return true;
    }

    function cameraGo2rtcPreloadValue(camera) {
        if (Object.prototype.hasOwnProperty.call(camera, 'go2rtc_preload')) {
            return camera.go2rtc_preload === true;
        }
        return globalPtzPreloadDefault();
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
        newCameraName.disabled = false;

        setInputValue('newCameraName', cameraName);
        setInputValue('newCameraDisplayName', camera.display_name || cameraName);
        setInputValue('newCameraDescription', camera.description || '');
        setSelectValue('newCameraCaptureSource', camera.local_command ? 'rtsp' : 'snapshot');
        setInputValue('newCameraUrl', camera.url || '');
        setInputValue('newCameraSnapshotUsername', (camera.http_auth && camera.http_auth.username) || '');
        setInputValue('newCameraSnapshotPassword', '');
        setSelectValue('newCameraSnapshotAuthType', (camera.http_auth && camera.http_auth.type) || 'basic');
        setInputValue('newCameraRtspUrl', camera.rtsp_url || '');
        setInputValue('newCameraPtzRtspUrl', camera.ptz_rtsp_url || '');
        applyCameraTemplateSelection(camera);
        setInputValue('newCameraTimeout', camera.timeout_s ?? 15);
        setInputValue('newCameraFailureRetry', camera.capture_failure_interval_s ?? 60);
        setSelectValue('newCameraVisibility', visibilityForCamera(camera));
        setCheckboxValue('newCameraCacheBust', camera.cache_bust !== false);
        setCheckboxValue('newCameraMozjpeg', camera.mozjpeg_optimize === true);
        setCheckboxValue('newCameraTimelapse', camera.timelapse_enabled !== false && camera.generate_timelapse !== false);
        setCheckboxValue('newCameraGo2rtcEnabled', camera.go2rtc_enabled !== false);
        setCheckboxValue('newCameraGo2rtcPreload', cameraGo2rtcPreloadValue(camera));
        setSelectValue('newCameraGo2rtcTransport', camera.go2rtc_rtsp_transport || 'tcp');
        setSelectValue('newCameraGo2rtcVideoMode', camera.go2rtc_video_mode || 'copy');

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

        setCheckboxValue('newCameraPrivacyEnabled', (camera.map_privacy_radius_m ?? 1200) > 0);
        setInputValue('newCameraPrivacyRadius', camera.map_privacy_radius_m || 1200);

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
        const capabilities = ptz.capabilities || {};
        const zoomOnly = ptz.zoom_only === true;
        setCheckboxValue('newCameraPtzCapabilityPan', capabilities.pan ?? ptz.supports_pan ?? !zoomOnly);
        setCheckboxValue('newCameraPtzCapabilityTilt', capabilities.tilt ?? ptz.supports_tilt ?? !zoomOnly);
        setCheckboxValue('newCameraPtzCapabilityZoom', capabilities.zoom ?? ptz.supports_zoom ?? true);
        setCheckboxValue('newCameraPtzCapabilityFocus', capabilities.focus ?? ptz.supports_focus ?? false);
        const tour = ptz.tour || {};
        setCheckboxValue('newCameraPtzTourEnabled', tour.enabled === true || ptz.tour_enabled === true);
        setInputValue('newCameraPtzTourAutoResume', tour.auto_resume_s ?? 1800);
        setInputValue('newCameraPtzHost', ptz.host || ptz.ip || '');
        setInputValue('newCameraPtzPort', ptz.port ?? 80);
        setInputValue('newCameraPtzUsername', ptz.username || '');
        setInputValue('newCameraPtzPassword', '');
        setInputValue('newCameraPtzProfileToken', ptz.profile_token || '');
        renderPtzPresetEditor(ptz.presets || []);
        syncAllOptionGroups();
        newCameraLastTest = {
            key: cameraTestKey({
                name: cameraName,
                capture_source: camera.local_command ? 'rtsp' : 'snapshot',
                url: camera.url || '',
                snapshot_username: (camera.http_auth && camera.http_auth.username) || '',
                snapshot_password: '',
                snapshot_auth_type: (camera.http_auth && camera.http_auth.type) || 'basic',
                rtsp_url: camera.rtsp_url || '',
                ptz_rtsp_url: camera.ptz_rtsp_url || ''
            })
        };
        confirmNewCameraBtn.disabled = false;
    }

    function selectedPtzCameras() {
        return Array.from(userPtzCameras.querySelectorAll('input[type="checkbox"]:checked'))
            .map(input => input.value);
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
        if (!(usersPayload.cameras || []).length) {
            const note = document.createElement('div');
            note.className = 'camera-access-note';
            note.textContent = 'No cameras configured.';
            userPtzCameras.appendChild(note);
            return;
        }
        (usersPayload.cameras || []).forEach(cameraName => {
            const label = document.createElement('label');
            label.className = 'camera-access-row';
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.value = cameraName;
            checkbox.checked = selectedSet.has(cameraName);
            checkbox.disabled = usersPayload.can_manage_users === false;
            const name = document.createElement('span');
            name.textContent = cameraName;
            label.appendChild(checkbox);
            label.appendChild(name);
            userPtzCameras.appendChild(label);
        });
    }

    function syncUserManagerPermissions() {
        const canManage = usersPayload.can_manage_users !== false;
        userForm.querySelectorAll('input, select, button').forEach(control => {
            control.disabled = !canManage;
        });
        if (usersPayload.can_set_user_passwords === false) {
            userPassword.disabled = true;
            userPassword.placeholder = 'Only superadmins can set user passwords';
        } else {
            userPassword.disabled = !canManage;
            userPassword.placeholder = 'Leave blank to keep current password';
        }
        userPtzCameras.querySelectorAll('input').forEach(control => {
            control.disabled = !canManage;
        });
        newUserBtn.disabled = !canManage;
        saveUserBtn.disabled = !canManage;
        deleteUserBtn.disabled = !canManage || !userUsername.value.trim();
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
        syncUserManagerPermissions();
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
        syncUserManagerPermissions();
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
        syncUserManagerPermissions();
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
            setStatus((result.message || 'User saved.') + configWriteDetails(result), 'success');
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
            setStatus((result.message || 'User deleted.') + configWriteDetails(result), 'success');
            await loadUsers();
            clearUserForm();
        } catch (error) {
            setStatus(`Error deleting user: ${error.message}`, 'error');
        }
    }

    function openPasswordModal() {
        adminCurrentPassword.value = '';
        adminNewPassword.value = '';
        adminConfirmPassword.value = '';
        showModal(passwordModal);
        adminCurrentPassword.focus();
    }

    async function changeAdminPassword(event) {
        event.preventDefault();
        if (adminNewPassword.value !== adminConfirmPassword.value) {
            setStatus('New passwords do not match.', 'error');
            return;
        }
        try {
            const response = await fetch('/api/users/password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    current_password: adminCurrentPassword.value,
                    new_password: adminNewPassword.value
                })
            });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.error || `HTTP error! status: ${response.status}`);
            }
            hideModal(passwordModal);
            setStatus((result.message || 'Password changed.') + configWriteDetails(result), 'success');
            window.setTimeout(() => {
                forgetCachedAdminCredentials();
                window.location.href = '/logout';
            }, 900);
        } catch (error) {
            setStatus(`Error changing password: ${error.message}`, 'error');
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
            loadedConfigVersion = config.config_version || null;
            canEditFullConfig = config.can_edit_full_config !== false;
            if (appFooter && config.fenetre_version) {
                appFooter.textContent = `aredn-timelapse-server v${config.fenetre_version}`;
            }
            syncSiteInputsFromConfig(loadedConfigData);
            syncLaunchInputsFromConfig(loadedConfigData);
            saveSiteNameBtn.disabled = false;
            populateCameraEditOptions();
            renderConfigForm(loadedConfigData, configFormContainer, '');
            saveConfigBtn.disabled = !canEditFullConfig;
            saveConfigBtn.title = canEditFullConfig
                ? ''
                : 'Only superadmins can save the full site configuration. Edit your assigned cameras from the camera list instead.';
            syncLaunchWorkflowEnabledState();
            setStatus(
                canEditFullConfig
                    ? 'Configuration loaded successfully.'
                    : 'Configuration loaded (read-only outside your assigned cameras; only superadmins can save the full configuration).',
                'success'
            );
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

    function resetSensitiveInput(input, toggle) {
        input.type = 'password';
        toggle.textContent = 'Show';
    }

    function toggleSensitiveInput(input, toggle) {
        const showing = input.type !== 'password';
        input.type = showing ? 'password' : 'text';
        toggle.textContent = showing ? 'Show' : 'Hide';
    }

    function allTemplateUrls(kind) {
        return Object.values(CAMERA_TEMPLATE_GROUPS).flatMap(group => (
            (group[kind] || []).flatMap(template => [template.url, renderCameraTemplateUrl(template.url)])
        ));
    }

    function renderCameraTemplateUrl(templateUrl) {
        return templateUrl
            .replaceAll('USERNAME', encodeURIComponent(newCameraSnapshotUsername.value.trim() || 'USERNAME'))
            .replaceAll('PASSWORD', encodeURIComponent(newCameraSnapshotPassword.value || 'PASSWORD'));
    }

    function selectedTemplate(select, kind) {
        const group = CAMERA_TEMPLATE_GROUPS[newCameraVendor.value] || CAMERA_TEMPLATE_GROUPS.generic;
        const templates = group[kind] || [];
        return templates.find(template => template.id === select.value) || templates[0] || null;
    }

    function knownTemplateVendor(value) {
        return Object.prototype.hasOwnProperty.call(CAMERA_TEMPLATE_GROUPS, value)
            ? value
            : 'generic';
    }

    function firstUrlInText(value) {
        const match = String(value || '').match(/(?:rtsp|https?):\/\/[^\s'"]+/i);
        return match ? match[0] : String(value || '');
    }

    function comparableTemplateUrl(value) {
        const candidate = firstUrlInText(value);
        if (!candidate) {
            return '';
        }
        try {
            const rendered = candidate
                .replaceAll('USERNAME', 'user')
                .replaceAll('PASSWORD', 'pass')
                .replaceAll('CAMERA_IP', 'camera.local')
                .replaceAll('HTTP_PORT', '80');
            const url = new URL(rendered);
            ['user', 'password', 'u', 'p'].forEach(key => url.searchParams.delete(key));
            const query = Array.from(url.searchParams.entries())
                .sort(([left], [right]) => left.localeCompare(right))
                .map(([key, value]) => `${key}=${value}`)
                .join('&');
            return `${url.protocol}//${url.pathname}${query ? `?${query}` : ''}`.toLowerCase();
        } catch (error) {
            return candidate
                .toLowerCase()
                .replace(/\/\/[^/@\s]+@/g, '//')
                .replace(/\/\/[^/:/?#'\s]+(?::\d+)?/g, '//host')
                .replace(/([?&](?:user|password|u|p)=)[^&'"\s]*/g, '$1');
        }
    }

    function templateMatchesValue(templateUrl, value) {
        const templateComparable = comparableTemplateUrl(templateUrl);
        const valueComparable = comparableTemplateUrl(value);
        return Boolean(templateComparable && valueComparable && templateComparable === valueComparable);
    }

    function inferTemplateId(camera, kind, vendor) {
        const explicitKey = kind === 'snapshots' ? 'snapshot_template' : 'rtsp_template';
        const explicit = camera[explicitKey];
        const group = CAMERA_TEMPLATE_GROUPS[vendor] || CAMERA_TEMPLATE_GROUPS.generic;
        const templates = group[kind] || [];
        if (templates.some(template => template.id === explicit)) {
            return explicit;
        }
        const value = kind === 'snapshots'
            ? camera.url
            : (camera.rtsp_url || camera.local_command || '');
        const match = templates.find(template => templateMatchesValue(template.url, value));
        return (match || templates[0] || {}).id || '';
    }

    function inferTemplateVendor(camera) {
        const explicit = knownTemplateVendor(camera.template_vendor || '');
        if (camera.template_vendor) {
            return explicit;
        }
        for (const vendor of Object.keys(CAMERA_TEMPLATE_GROUPS).filter(item => item !== 'generic')) {
            const group = CAMERA_TEMPLATE_GROUPS[vendor];
            const snapshotMatch = (group.snapshots || []).some(template => templateMatchesValue(template.url, camera.url || ''));
            const rtspMatch = (group.rtsp || []).some(template => templateMatchesValue(template.url, camera.rtsp_url || camera.local_command || ''));
            if (snapshotMatch || rtspMatch) {
                return vendor;
            }
        }
        return explicit;
    }

    function populateTemplateSelect(select, templates, selectedValue = '') {
        select.innerHTML = '';
        templates.forEach(template => {
            const option = document.createElement('option');
            option.value = template.id;
            option.textContent = template.label;
            select.appendChild(option);
        });
        if (selectedValue && templates.some(template => template.id === selectedValue)) {
            select.value = selectedValue;
        }
    }

    function syncTemplateSelects(selectedSnapshot = '', selectedRtsp = '') {
        const group = CAMERA_TEMPLATE_GROUPS[newCameraVendor.value] || CAMERA_TEMPLATE_GROUPS.generic;
        populateTemplateSelect(newCameraSnapshotTemplate, group.snapshots || [], selectedSnapshot);
        populateTemplateSelect(newCameraRtspTemplate, group.rtsp || [], selectedRtsp);
    }

    function applyCameraTemplateSelection(camera) {
        const vendor = inferTemplateVendor(camera);
        newCameraVendor.value = vendor;
        syncTemplateSelects(
            inferTemplateId(camera, 'snapshots', vendor),
            inferTemplateId(camera, 'rtsp', vendor)
        );
    }

    function applySelectedSnapshotTemplate({ force = false } = {}) {
        const template = selectedTemplate(newCameraSnapshotTemplate, 'snapshots');
        if (!template) return;
        const knownSnapshotUrls = allTemplateUrls('snapshots');
        if (force || !newCameraUrl.value || knownSnapshotUrls.includes(newCameraUrl.value)) {
            newCameraUrl.value = renderCameraTemplateUrl(template.url);
            if (template.auth) {
                newCameraSnapshotAuthType.value = template.auth;
            }
            resetSensitiveInput(newCameraUrl, toggleNewCameraUrlBtn);
            resetNewCameraTest();
        }
    }

    function applySelectedRtspTemplate({ force = false } = {}) {
        const template = selectedTemplate(newCameraRtspTemplate, 'rtsp');
        if (!template) return;
        const knownRtspUrls = allTemplateUrls('rtsp');
        if (force || !newCameraRtspUrl.value || knownRtspUrls.includes(newCameraRtspUrl.value)) {
            newCameraRtspUrl.value = renderCameraTemplateUrl(template.url);
            resetSensitiveInput(newCameraRtspUrl, toggleNewCameraRtspUrlBtn);
            resetNewCameraTest();
        }
    }

    function applyNewCameraTemplate() {
        syncTemplateSelects();
        applySelectedSnapshotTemplate();
        applySelectedRtspTemplate();
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
        const visibility = newCameraVisibility.value || 'public';

        const captureSource = newCameraCaptureSource.value || 'snapshot';
        const useRtspUrl = captureSource === 'rtsp'
            || checked('newCameraGo2rtcEnabled')
            || checked('newCameraPtzEnabled');
        const usePtzRtspUrl = checked('newCameraPtzEnabled')
            && checked('newCameraGo2rtcEnabled');
        const payload = {
            name: newCameraName.value.trim(),
            display_name: newCameraDisplayName.value.trim() || newCameraName.value.trim(),
            template_vendor: newCameraVendor.value || 'generic',
            snapshot_template: newCameraSnapshotTemplate.value || '',
            rtsp_template: newCameraRtspTemplate.value || '',
            description: newCameraDescription.value.trim(),
            capture_source: captureSource,
            url: captureSource === 'snapshot' ? newCameraUrl.value.trim() : '',
            snapshot_username: captureSource === 'snapshot' ? newCameraSnapshotUsername.value.trim() : '',
            snapshot_password: captureSource === 'snapshot' ? newCameraSnapshotPassword.value : '',
            snapshot_auth_type: newCameraSnapshotAuthType.value || 'basic',
            rtsp_url: useRtspUrl ? newCameraRtspUrl.value.trim() : '',
            ptz_rtsp_url: usePtzRtspUrl ? newCameraPtzRtspUrl.value.trim() : '',
            timeout_s: intValue('newCameraTimeout', 15),
            capture_failure_interval_s: intValue('newCameraFailureRetry', 60),
            public: visibility === 'public',
            visibility,
            hidden: visibility === 'hidden',
            cache_bust: checked('newCameraCacheBust'),
            gather_metrics: true,
            mozjpeg_optimize: checked('newCameraMozjpeg'),
            timelapse_enabled: checked('newCameraTimelapse'),
            go2rtc_enabled: checked('newCameraGo2rtcEnabled'),
            go2rtc_preload: checked('newCameraGo2rtcPreload') && usePtzRtspUrl,
            go2rtc_rtsp_transport: newCameraGo2rtcTransport.value || 'tcp',
            go2rtc_video_mode: newCameraGo2rtcVideoMode.value || 'copy',
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
        payload.map_privacy_enabled = checked('newCameraPrivacyEnabled');
        payload.map_privacy_radius_m = numberValue('newCameraPrivacyRadius', 1200);
        if (checked('newCameraSkyEnabled')) {
            payload.sky_area_enabled = true;
            payload.sky_area = document.getElementById('newCameraSkyArea').value.trim() || '0,0,1,0.35';
        }
        if (checked('newCameraStorageEnabled')) {
            payload.work_dir_max_size_GB = intValue('newCameraStorageGb', 5);
        }
        if (checked('newCameraPtzEnabled')) {
            const presets = collectPtzPresets();
            payload.ptz_enabled = true;
            payload.ptz_public = checked('newCameraPtzPublic');
            payload.ptz_allow_presets = checked('newCameraPtzAllowPresets');
            payload.ptz_allow_manual_control = checked('newCameraPtzAllowManual');
            payload.ptz_access_level = document.getElementById('newCameraPtzAccessLevel').value || 'presets';
            payload.ptz_capabilities = {
                pan: newCameraPtzCapabilityPan.checked,
                tilt: newCameraPtzCapabilityTilt.checked,
                zoom: newCameraPtzCapabilityZoom.checked,
                focus: newCameraPtzCapabilityFocus.checked
            };
            payload.ptz_tour_enabled = newCameraPtzTourEnabled.checked;
            payload.ptz_tour_auto_resume_s = intValue('newCameraPtzTourAutoResume', 1800);
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
        if (payload.capture_source === 'snapshot' && !payload.url) {
            throw new Error('Snapshot URL is required.');
        }
        if (payload.capture_source === 'rtsp' && !payload.rtsp_url) {
            throw new Error('RTSP URL is required.');
        }
    }

    function cameraTestKey(payload) {
        return [
            payload.capture_source,
            payload.url,
            payload.snapshot_username,
            payload.snapshot_password,
            payload.snapshot_auth_type,
            payload.rtsp_url,
            payload.ptz_rtsp_url
        ].join('|');
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
                body: JSON.stringify({
                    url: payload.url,
                    timeout_s: payload.timeout_s,
                    capture_source: payload.capture_source,
                    cache_bust: payload.cache_bust,
                    snapshot_username: payload.snapshot_username,
                    snapshot_password: payload.snapshot_password,
                    snapshot_auth_type: payload.snapshot_auth_type,
                    rtsp_url: payload.rtsp_url,
                    ptz_rtsp_url: payload.ptz_rtsp_url
                })
            });
            const result = await response.json();
            if (!response.ok || !result.ok) {
                throw new Error(result.error || `Snapshot test failed with HTTP ${response.status}`);
            }
            newCameraLastTest = { key: cameraTestKey(payload) };
            newCameraTestResult.innerHTML = '';
            const summary = document.createElement('div');
            summary.textContent = `Snapshot OK: ${result.width}x${result.height}, ${Math.round(result.bytes / 1024)} KB`;
            const preview = document.createElement('img');
            preview.alt = 'Snapshot preview';
            preview.src = result.preview_data_url;
            newCameraTestResult.appendChild(summary);
            (result.stream_tests || []).forEach(streamTest => {
                const streamSummary = document.createElement('div');
                streamSummary.textContent = `${streamTest.name} OK: ${streamTest.width}x${streamTest.height}, ${Math.round(streamTest.bytes / 1024)} KB`;
                newCameraTestResult.appendChild(streamSummary);
            });
            newCameraTestResult.appendChild(preview);
            confirmNewCameraBtn.disabled = false;
        } catch (error) {
            newCameraLastTest = null;
            confirmNewCameraBtn.disabled = true;
            newCameraTestResult.textContent = error.message;
            setStatus(`Snapshot test failed: ${error.message}`, 'error');
        }
    }

    async function loadNewCameraPtzPresets() {
        const previousPresets = collectPtzPresets();
        try {
            if (!checked('newCameraPtzEnabled')) {
                throw new Error('Enable PTZ controls before loading presets.');
            }
            loadNewCameraPtzPresetsBtn.disabled = true;
            setStatus('Loading ONVIF presets...', 'info');
            const response = await fetch('/api/camera/ptz_presets', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    camera_name: editingCameraName || newCameraName.value.trim(),
                    name: newCameraName.value.trim(),
                    ptz_host: document.getElementById('newCameraPtzHost').value.trim(),
                    ptz_port: intValue('newCameraPtzPort', 80),
                    ptz_username: document.getElementById('newCameraPtzUsername').value.trim(),
                    ptz_password: document.getElementById('newCameraPtzPassword').value,
                    ptz_profile_token: document.getElementById('newCameraPtzProfileToken').value.trim()
                })
            });
            const result = await response.json();
            if (!response.ok || !result.ok) {
                throw new Error(result.error || `Preset load failed with HTTP ${response.status}`);
            }
            const discoveredPresets = (result.presets || []).map(preset => ({
                id: preset.id,
                name: preset.name,
                token: preset.token || preset.id,
                enabled: true
            }));
            const presets = mergeDiscoveredPtzPresets(previousPresets, discoveredPresets);
            renderPtzPresetEditor(presets);
            setStatus(
                presets.length
                    ? `Loaded ${presets.length} named ONVIF preset(s). Rename or remove rows before saving.`
                    : 'No named ONVIF presets were found. Unnamed presets are treated as untaught.',
                presets.length ? 'success' : 'info'
            );
        } catch (error) {
            renderPtzPresetEditor(previousPresets);
            setStatus(`Could not load ONVIF presets: ${error.message}`, 'error');
        } finally {
            loadNewCameraPtzPresetsBtn.disabled = false;
        }
    }

    async function confirmNewCameraAdd() {
        try {
            const payload = collectNewCameraPayload(true);
            validateNewCameraPayload(payload);
            const captureChanged = cameraFormMode === 'edit'
                && editingOriginalCamera
                && (
                    payload.url !== (editingOriginalCamera.url || '')
                    || payload.rtsp_url !== (editingOriginalCamera.rtsp_url || '')
                    || payload.ptz_rtsp_url !== (editingOriginalCamera.ptz_rtsp_url || '')
                    || payload.capture_source !== (editingOriginalCamera.local_command ? 'rtsp' : 'snapshot')
                );
            if (!newCameraLastTest || newCameraLastTest.key !== cameraTestKey(payload)) {
                throw new Error('Test the current capture source before saving it.');
            }
            const isEdit = cameraFormMode === 'edit' && editingCameraName;
            const actionLabel = `${isEdit ? 'Updating' : 'Adding'} camera '${payload.display_name || payload.name}'...`;
            setCameraModalStatus(actionLabel, 'info');
            setStatus(actionLabel, 'info');
            payload.require_test = !isEdit || captureChanged;
            const response = await fetch(isEdit ? `/api/camera/${encodeURIComponent(editingCameraName)}` : '/api/camera/add', {
                method: isEdit ? 'PUT' : 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.error || `Failed to add camera with HTTP ${response.status}`);
            }
            try {
                const reloadResult = await requestApplicationReload();
                result.reload = { ok: true, message: reloadResult.message || 'Reload signal sent successfully.' };
            } catch (reloadError) {
                result.reload = { ok: false, warning: reloadError.message };
            }
            setStatus(
                (result.message || `Camera '${payload.name}' saved.`) + configWriteDetails(result),
                'success'
            );
            await fetchAndDisplayConfig();
            await loadStorageSummary();
            const savedName = result.camera_name || payload.name;
            if (!isEdit) {
                cameraFormMode = 'edit';
                editingCameraName = savedName;
                newCameraName.disabled = false;
                cameraModalTitle.textContent = `Edit ${savedName}`;
                confirmNewCameraBtn.textContent = 'Save Camera Changes';
            } else {
                editingCameraName = savedName;
                cameraModalTitle.textContent = `Edit ${savedName}`;
            }
            editingOriginalCamera = ((loadedConfigData && loadedConfigData.cameras) || {})[savedName] || editingOriginalCamera;
            setCameraModalStatus(
                (result.message || `Camera '${payload.display_name || savedName}' saved.`) + configWriteDetails(result),
                'success'
            );
            confirmNewCameraBtn.disabled = false;
        } catch (error) {
            setCameraModalStatus(`Camera save failed: ${error.message}`, 'error');
            setStatus(`Error saving camera: ${error.message}`, 'error');
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
                input.dataset.name = key;
                inputWrapper.appendChild(input);
                formRow.appendChild(inputWrapper);
                parentElement.appendChild(formRow);
            } else if (typeof value === 'number') {
                const input = document.createElement('input');
                input.type = 'number';
                input.id = currentKey;
                input.value = value;
                input.dataset.key = currentKey;
                input.dataset.name = key;
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
                    textarea.dataset.name = key;
                    inputWrapper.appendChild(textarea);
                    formRow.appendChild(inputWrapper);
                    parentElement.appendChild(formRow);
                } else {
                    const input = document.createElement('input');
                    const sensitive = isSensitiveField(currentKey);
                    input.type = sensitive ? 'password' : 'text';
                    input.autocomplete = sensitive ? 'new-password' : 'off';
                    input.id = currentKey;
                    input.value = value;
                    input.dataset.key = currentKey;
                    input.dataset.name = key;
                    inputWrapper.appendChild(input);
                    if (sensitive) {
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
                fieldset.dataset.name = key;

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
                fieldset.dataset.name = key;
                renderConfigForm(value, content, currentKey);
                fieldset.appendChild(content);
                parentElement.appendChild(fieldset);
            }
        }
    }

    async function saveSiteName() {
        if (!loadedConfigData) {
            setStatus('Load the configuration before saving site settings.', 'error');
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
        if (!updatedConfig.global.ui || typeof updatedConfig.global.ui !== 'object') {
            updatedConfig.global.ui = {};
        }
        updatedConfig.global.deployment_name = siteName;
        updatedConfig.global.ui.public_site = sitePublicValue();
        setStatus('Saving site settings...', 'info');
        try {
            const response = await fetch('/api/global/deployment_name', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    deployment_name: siteName,
                    public_site: sitePublicValue(),
                }),
            });
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ error: `HTTP error! status: ${response.status}` }));
                throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
            }
            const result = await response.json();
            try {
                const reloadResult = await requestApplicationReload();
                result.reload = { ok: true, message: reloadResult.message || 'Reload signal sent successfully.' };
            } catch (reloadError) {
                result.reload = { ok: false, warning: reloadError.message };
            }
            loadedConfigData = updatedConfig;
            syncSiteInputsToRenderedConfig();
            setStatus(
                (result.message || 'Site settings saved.') + configWriteDetails(result),
                'success'
            );
            await fetchAndDisplayConfig();
        } catch (error) {
            console.error('Error saving site settings:', error);
            setStatus(`Error saving site settings: ${error.message}`, 'error');
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
        function formFieldName(element) {
            if (element.dataset.name) {
                return element.dataset.name;
            }
            if (!element.dataset.key) {
                return '';
            }
            return element.dataset.key.split('.').pop().replace(/\[\d+\]$/, '');
        }

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
                    const key = formFieldName(child);
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
                        const actualKey = formFieldName(child);

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

        applySiteInputsToConfig(configData);
        applyLaunchInputsToConfig(configData);

        console.log("Saving data:", JSON.stringify(configData, null, 2));

        try {
            const response = await fetch('/config', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ config_version: loadedConfigVersion, config: configData }),
            });
            if (response.status === 409) {
                const conflictData = await response.json().catch(() => ({}));
                setStatus(conflictData.error || 'Configuration changed elsewhere; reloading the latest version.', 'error');
                await fetchAndDisplayConfig();
                return;
            }
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ error: `HTTP error! status: ${response.status}` }));
                throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
            }
            const result = await response.json();
            try {
                const reloadResult = await requestApplicationReload();
                result.reload = { ok: true, message: reloadResult.message || 'Reload signal sent successfully.' };
            } catch (reloadError) {
                result.reload = { ok: false, warning: reloadError.message };
            }
            loadedConfigData = configData;
            syncSiteInputsFromConfig(configData);
            syncSiteInputsToRenderedConfig();
            setStatus((result.message || 'Configuration saved successfully!') + configWriteDetails(result), 'success');
            await fetchAndDisplayConfig();
        } catch (error) {
            console.error('Error saving config:', error);
            setStatus(`Error saving configuration: ${error.message}`, 'error');
        }
    }

    async function requestApplicationReload() {
        const response = await fetch('/config/reload', {
            method: 'POST',
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(result.error || `HTTP error! status: ${response.status}`);
        }
        return result;
    }

    async function reloadApplication() {
        setStatus('Sending reload signal to application...', 'info');
        try {
            const result = await requestApplicationReload();
            setStatus(result.message || 'Reload signal sent successfully!', 'success');
            await fetchAndDisplayConfig();
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

    function forgetCachedAdminCredentials() {
        // HTTP Basic Auth has no real logout: the browser itself caches the
        // credential and keeps silently resending it on the next request no
        // matter what /logout responds with -- Clear-Site-Data does not
        // clear that cache. Overwriting it with bogus credentials via a
        // synchronous XHR (its own username/password params, not the URL,
        // so it's actually applied to the browser's credential cache) is
        // the standard workaround: the browser then has nothing valid left
        // to resend, so the native login prompt reappears.
        try {
            const xhr = new XMLHttpRequest();
            xhr.open('GET', '/', false, 'logged-out', 'logged-out');
            xhr.send();
        } catch (e) {
            // Expected: the bogus credentials fail with 401.
        }
    }

    function logoutAdmin() {
        forgetCachedAdminCredentials();
        window.location.href = '/logout';
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
    loadMediaLocation();
    loadTestRecordings();

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
            const sensitive = isSensitiveField(fieldKey);
            input.type = sensitive ? 'password' : 'text';
            input.autocomplete = sensitive ? 'new-password' : 'off';
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
