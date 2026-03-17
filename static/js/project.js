function getCurrentPositionOnce(options) {
    return new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, options);
    });
}

function getOrCreateDeviceId() {
    const key = "laas_device_id";
    let value = "";
    try {
        value = localStorage.getItem(key) || "";
    } catch (e) {
        value = "";
    }
    if (value) return value;
    const bytes = new Uint8Array(16);
    if (window.crypto && typeof window.crypto.getRandomValues === "function") {
        window.crypto.getRandomValues(bytes);
    } else {
        for (let i = 0; i < bytes.length; i += 1) {
            bytes[i] = Math.floor(Math.random() * 256);
        }
    }
    value = Array.from(bytes)
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
    try {
        localStorage.setItem(key, value);
    } catch (e) {
        // ignore storage failures (private mode, etc.)
    }
    return value;
}

function gpsFailureMessage(error) {
    if (error && typeof error.code === "number") {
        if (error.code === 1 || error.code === 2 || error.code === 3) {
            return "Please enable GPS and try again.";
        }
    }
    const msg = (error && error.message ? String(error.message) : "").toLowerCase();
    if (msg.includes("insecure") || msg.includes("https") || msg.includes("secure")) {
        return "Location is blocked on insecure pages. Open the app on HTTPS or localhost.";
    }
    return "Please enable GPS and try again.";
}

const TEST_MODE_DEFAULT_COORDS = { latitude: 28.6139, longitude: 77.209 };
const GPS_ACCURACY_THRESHOLD_M = 50;

function isLikelyMobileDevice() {
    const ua = (navigator.userAgent || "").toLowerCase();
    return /android|iphone|ipad|ipod|mobile|windows phone/.test(ua);
}

async function getStrictGpsLocation({
    retries = 2,
    timeoutMs = 5000,
    accuracyMax = GPS_ACCURACY_THRESHOLD_M,
} = {}) {
    // Strict: enableHighAccuracy + maximumAge=0 + reject if accuracy > accuracyMax.
    let lastError = null;
    for (let attempt = 0; attempt <= retries; attempt += 1) {
        try {
            // captureLocation uses watch as fallback, which is much more reliable than a single fast fix.
            const loc = await captureLocation({
                preferHighAccuracy: true,
                timeoutMs,
                maxAgeMs: 0,
                targetAccuracy: accuracyMax || 0,
            });
            const accuracy = Number(loc.accuracy) || 0;
            if (accuracyMax && Number.isFinite(accuracy) && accuracy > accuracyMax) {
                const err = new Error("GPS accuracy too low");
                err.kind = "accuracy";
                lastError = err;
            } else {
                return loc;
            }
        } catch (err) {
            lastError = err;
            if (err && err.code === 1) {
                // Permission denied: don't keep retrying silently.
                throw err;
            }
        }
        if (attempt < retries) {
            await waitMs(350);
        }
    }
    throw lastError || new Error("Unable to capture location");
}

async function getGpsLocationOrNull(options = {}) {
    try {
        return await getStrictGpsLocation(options);
    } catch (err) {
        return null;
    }
}

function initGmailUsernameFields() {
    const forms = document.querySelectorAll("form");
    forms.forEach((form) => {
        const usernameInput = form.querySelector("[data-email-username]");
        const hiddenEmailInput = form.querySelector("[data-email-hidden]");
        if (!usernameInput || !hiddenEmailInput) return;

        const syncEmail = () => {
            let username = (usernameInput.value || "").trim().toLowerCase();
            if (username.includes("@")) {
                username = username.split("@")[0];
            }
            username = username.replace(/\s+/g, "");
            usernameInput.value = username;
            hiddenEmailInput.value = username ? `${username}@gmail.com` : "";
        };

        // Keep @gmail.com fixed by storing final email only in hidden field.
        usernameInput.addEventListener("input", syncEmail);
        usernameInput.addEventListener("blur", syncEmail);
        form.addEventListener("submit", (e) => {
            syncEmail();
            if (!hiddenEmailInput.value) {
                e.preventDefault();
                alert("Please enter your email username.");
            }
        });
        syncEmail();
    });
}

function initAjaxActionForms() {
    const forms = document.querySelectorAll("form[data-ajax-action='1']");
    forms.forEach((form) => {
        if (form.dataset.ajaxBound === "1") return;
        form.dataset.ajaxBound = "1";

        form.addEventListener("submit", async (e) => {
            e.preventDefault();

            const submitButton = e.submitter || form.querySelector("button[type='submit']");
            if (submitButton) {
                submitButton.disabled = true;
            }

            try {
                const response = await fetch(form.action, {
                    method: (form.method || "POST").toUpperCase(),
                    body: new FormData(form),
                    headers: { "X-Requested-With": "fetch" },
                });

                if (!response.ok) {
                    alert("Action failed. Please try again.");
                    return;
                }

                const removeClosest = form.dataset.ajaxRemoveClosest || "";
                if (removeClosest) {
                    const target = form.closest(removeClosest);
                    if (target) target.remove();
                    return;
                }

                const replaceHtml = form.dataset.ajaxReplaceHtml || "";
                if (replaceHtml) {
                    form.outerHTML = replaceHtml;
                    return;
                }

                const toggleKind = form.dataset.ajaxToggle || "";
                if (toggleKind === "subject") {
                    const row = form.closest("tr");
                    const statusEl = row ? row.querySelector(".js-subject-status") : null;
                    const btn = form.querySelector(".js-toggle-subject-btn") || submitButton;
                    if (statusEl) {
                        const enabled = statusEl.textContent.trim().toLowerCase() === "enabled";
                        statusEl.textContent = enabled ? "Disabled" : "Enabled";
                        statusEl.classList.toggle("badge-danger", enabled);
                    }
                    if (btn) {
                        const label = (btn.textContent || "").trim().toLowerCase();
                        btn.textContent = label === "disable" ? "Enable" : "Disable";
                        btn.disabled = false;
                    }
                    return;
                }

                alert(form.dataset.ajaxSuccessMessage || "Action completed.");
            } catch (err) {
                alert("Could not connect to server. Please try again.");
            } finally {
                if (submitButton && !submitButton.disabled) {
                    // already re-enabled by toggles
                } else if (submitButton && !form.dataset.ajaxReplaceHtml && !form.dataset.ajaxRemoveClosest) {
                    submitButton.disabled = false;
                }
            }
        });
    });
}

function waitMs(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function withTimeout(promise, timeoutMs, fallbackValue) {
    return Promise.race([promise, waitMs(timeoutMs).then(() => fallbackValue)]);
}

function isFreshPosition(position, maxAgeMs) {
    if (!position || !Number.isFinite(maxAgeMs) || maxAgeMs <= 0) return true;
    const timestamp = Number(position.timestamp);
    if (!Number.isFinite(timestamp) || timestamp <= 0) return true;
    return Date.now() - timestamp <= maxAgeMs;
}

async function captureLocationWithWatch({
    preferHighAccuracy = true,
    targetAccuracy = 30,
    timeoutMs = 10000,
    maxAgeMs = 15000,
} = {}) {
    if (!navigator.geolocation) {
        throw new Error("Geolocation is not supported by this browser.");
    }
    if (!window.isSecureContext) {
        throw new Error(
            "Location is blocked on insecure pages. Open the app on localhost/127.0.0.1 or HTTPS."
        );
    }

    return new Promise((resolve, reject) => {
        let watchId = null;
        let timer = null;
        let bestLocation = null;
        let settled = false;

        const cleanup = () => {
            if (watchId !== null) {
                navigator.geolocation.clearWatch(watchId);
                watchId = null;
            }
            if (timer) {
                clearTimeout(timer);
                timer = null;
            }
        };

        const finish = (location) => {
            if (settled) return;
            settled = true;
            cleanup();
            resolve(location);
        };

        const fail = (error) => {
            if (settled) return;
            settled = true;
            cleanup();
            reject(error);
        };

        const success = (position) => {
            if (!isFreshPosition(position, maxAgeMs)) {
                return;
            }
            const accuracy = position.coords.accuracy || 0;
            const location = {
                latitude: position.coords.latitude,
                longitude: position.coords.longitude,
                accuracy,
            };
            if (!bestLocation || accuracy < bestLocation.accuracy) {
                bestLocation = location;
            }
            if (!targetAccuracy || (accuracy && accuracy <= targetAccuracy)) {
                finish(location);
            }
        };

        const failure = (error) => {
            fail(error);
        };

        try {
            watchId = navigator.geolocation.watchPosition(success, failure, {
                enableHighAccuracy: preferHighAccuracy,
                maximumAge: 0,
                timeout: timeoutMs,
            });
        } catch (error) {
            fail(error);
            return;
        }

        timer = setTimeout(() => {
            if (bestLocation) {
                finish(bestLocation);
            } else {
                fail(new Error("Unable to capture location. Please retry."));
            }
        }, timeoutMs);
    });
}

async function captureLocation(options = {}) {
    const preferHighAccuracy = options.preferHighAccuracy !== false;
    const targetAccuracy = options.targetAccuracy ?? 30;
    const maxAgeMs = options.maxAgeMs ?? 15000;
    const watchConfig = {
        ...options,
        preferHighAccuracy,
        targetAccuracy,
        maxAgeMs,
    };
    let fallbackLocation = null;

    try {
        const location = await getCurrentLocation({ preferHighAccuracy, maxAgeMs });
        fallbackLocation = location;
        if (!targetAccuracy || (Number.isFinite(location.accuracy) && location.accuracy <= targetAccuracy)) {
            return location;
        }
    } catch (error) {
        if (error && error.code === 1) {
            throw error;
        }
    }

    try {
        return await captureLocationWithWatch(watchConfig);
    } catch (watchError) {
        if (watchError && watchError.code === 1) {
            throw watchError;
        }
        if (fallbackLocation) {
            return fallbackLocation;
        }
        throw watchError;
    }
}

async function captureLocationFast(options = {}) {
    const preferHighAccuracy = options.preferHighAccuracy !== false;
    const maxAgeMs = options.maxAgeMs ?? 0;
    if (!navigator.geolocation) {
        throw new Error("Geolocation is not supported by this browser.");
    }
    if (!window.isSecureContext) {
        throw new Error(
            "Location is blocked on insecure pages. Open the app on localhost/127.0.0.1 or HTTPS."
        );
    }

    const position = await getCurrentPositionOnce({
        enableHighAccuracy: preferHighAccuracy,
        timeout: options.timeoutMs ?? 10000,
        maximumAge: 0,
    });
    if (!isFreshPosition(position, maxAgeMs)) {
        throw new Error("Stale location received.");
    }
    return {
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
        accuracy: position.coords.accuracy || 0,
    };
}

async function getCurrentLocation(options = {}) {
    if (!navigator.geolocation) {
        throw new Error("Geolocation is not supported by this browser.");
    }
    if (!window.isSecureContext) {
        throw new Error(
            "Location is blocked on insecure pages. Open the app on localhost/127.0.0.1 or HTTPS."
        );
    }

    const preferHighAccuracy = options.preferHighAccuracy !== false;
    const maxAgeMs = options.maxAgeMs ?? 15000;
    const attempts = preferHighAccuracy
        ? [
              { enableHighAccuracy: true, timeout: 8000, maximumAge: 0 },
              { enableHighAccuracy: false, timeout: 6000, maximumAge: 0 },
          ]
        : [{ enableHighAccuracy: false, timeout: 6000, maximumAge: 0 }];

    let lastError = null;
    for (const options of attempts) {
        for (let retry = 0; retry < 2; retry += 1) {
            try {
                const position = await getCurrentPositionOnce(options);
                if (!isFreshPosition(position, maxAgeMs)) {
                    throw new Error("Stale location received.");
                }
                return {
                    latitude: position.coords.latitude,
                    longitude: position.coords.longitude,
                    accuracy: position.coords.accuracy || 0,
                };
            } catch (error) {
                lastError = error;
                if (error && error.code === 1) {
                    throw error;
                }
                if (retry === 0) {
                    await waitMs(700);
                }
            }
        }
    }
    throw lastError || new Error("Unable to capture location.");
}

function describeLocationOrNetworkError(error) {
    if (!error) return "Unable to capture location or connect to server.";
    if (typeof error.code === "number") {
        if (error.code === 1) {
            return "Please enable GPS and grant location permission.";
        }
        if (error.code === 2) {
            return "Live location is unavailable. Turn on device Location Services (GPS), then retry.";
        }
        if (error.code === 3) {
            return "Please enable GPS and grant location permission.";
        }
    }
    if (error instanceof TypeError) {
        return "Could not connect to server. Check internet/VPN and try again.";
    }
    if (error.message) return error.message;
    return "Unable to capture location or connect to server.";
}

function haversineMeters(lat1, lon1, lat2, lon2) {
    const r = 6371000;
    const dLat = ((lat2 - lat1) * Math.PI) / 180;
    const dLon = ((lon2 - lon1) * Math.PI) / 180;
    const lat1Rad = (lat1 * Math.PI) / 180;
    const lat2Rad = (lat2 * Math.PI) / 180;
    const a =
        Math.sin(dLat / 2) * Math.sin(dLat / 2) +
        Math.cos(lat1Rad) * Math.cos(lat2Rad) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return r * (2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a)));
}

function showClientNotice(message, kind = "info", timeoutMs = 2600) {
    if (!message) return;
    let container = document.getElementById("client-notice-stack");
    if (!container) {
        container = document.createElement("div");
        container.id = "client-notice-stack";
        container.style.position = "fixed";
        container.style.top = "16px";
        container.style.right = "16px";
        container.style.zIndex = "9999";
        container.style.display = "flex";
        container.style.flexDirection = "column";
        container.style.gap = "8px";
        document.body.appendChild(container);
    }
    const notice = document.createElement("div");
    notice.textContent = message;
    notice.style.padding = "10px 12px";
    notice.style.borderRadius = "8px";
    notice.style.fontSize = "14px";
    notice.style.boxShadow = "0 6px 18px rgba(0,0,0,0.15)";
    notice.style.background = kind === "success" ? "#1f9d55" : kind === "error" ? "#c2410c" : "#1e293b";
    notice.style.color = "#fff";
    notice.style.maxWidth = "320px";
    notice.style.wordWrap = "break-word";
    container.appendChild(notice);
    window.setTimeout(() => {
        notice.style.opacity = "0";
        notice.style.transition = "opacity 0.25s ease";
        window.setTimeout(() => notice.remove(), 260);
    }, timeoutMs);
}

async function parseApiResponse(response) {
    if (response.redirected && response.url && response.url.includes("/login")) {
        return {
            success: false,
            message: "Your session has expired. Please login again and retry.",
            sessionExpired: true,
        };
    }
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
        return response.json();
    }
    const text = await response.text();
    if (text && /<title>\s*Login|name=\"email\"|name=\"password\"/i.test(text)) {
        return {
            success: false,
            message: "Your session has expired. Please login again and retry.",
            sessionExpired: true,
        };
    }
    return { success: false, message: text || "Unexpected server response." };
}

async function fetchWithRetry(url, options, retries = 2) {
    let lastError = null;
    for (let attempt = 0; attempt <= retries; attempt += 1) {
        try {
            return await fetch(url, options);
        } catch (error) {
            lastError = error;
            if (attempt < retries) {
                await waitMs(300);
            }
        }
    }
    throw lastError || new Error("Network error");
}

async function startTeacherSession(buttonEl) {
    const subjectSelect = document.getElementById("subject_id");
    const subjectId = subjectSelect ? subjectSelect.value : "";
    const startButton = buttonEl || null;
    if (startButton) {
        if (startButton.dataset.sessionStarting === "1") {
            return;
        }
        startButton.dataset.sessionStarting = "1";
        startButton.disabled = true;
        startButton.dataset.originalLabel = startButton.textContent;
        startButton.textContent = "Starting session...";
    }
    const reenableStartButton = () => {
        if (startButton) {
            delete startButton.dataset.sessionStarting;
            startButton.disabled = false;
            if (startButton.dataset.originalLabel) {
                startButton.textContent = startButton.dataset.originalLabel;
                delete startButton.dataset.originalLabel;
            }
        }
    };

    if (!subjectId) {
        alert("Please select a subject first.");
        reenableStartButton();
        return;
    }

    const payload = { subject_id: Number(subjectId), device_id: getOrCreateDeviceId() };
    // Mobile: start with real coords even if accuracy is poor (to avoid default test coords causing "out of range").
    // Desktop/laptop: start in test mode.
    const isMobile = isLikelyMobileDevice();
    let location = null;
    if (navigator.geolocation && window.isSecureContext && isMobile) {
        location = await withTimeout(
            getStrictGpsLocation({
                retries: 2,
                timeoutMs: 5000,
                accuracyMax: GPS_ACCURACY_THRESHOLD_M,
            }).catch(() => null),
            5000,
            null
        );
        if (!location) {
            // Best-effort GPS (may be >50m accuracy) so session still represents teacher's area.
            location = await withTimeout(
                captureLocation({
                    preferHighAccuracy: true,
                    timeoutMs: 5000,
                    maxAgeMs: 0,
                    targetAccuracy: 0,
                }).catch(() => null),
                5000,
                null
            );
        }
    }
    if (location) {
        if (
            !Number.isFinite(location.latitude) ||
            !Number.isFinite(location.longitude) ||
            location.latitude < -90 ||
            location.latitude > 90 ||
            location.longitude < -180 ||
            location.longitude > 180
        ) {
            payload.test_mode = true;
            payload.latitude = TEST_MODE_DEFAULT_COORDS.latitude;
            payload.longitude = TEST_MODE_DEFAULT_COORDS.longitude;
            payload.accuracy = 0;
        } else {
            payload.latitude = location.latitude;
            payload.longitude = location.longitude;
            payload.accuracy = location.accuracy;
        }
    } else {
        payload.test_mode = true;
        payload.latitude = TEST_MODE_DEFAULT_COORDS.latitude;
        payload.longitude = TEST_MODE_DEFAULT_COORDS.longitude;
        payload.accuracy = 0;
    }
    console.log("[attendance] teacher location", {
        latitude: payload.latitude,
        longitude: payload.longitude,
        accuracy: payload.accuracy,
        test_mode: !!payload.test_mode,
    });

    try {
        const response = await fetchWithRetry("/api/teacher/session/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        const data = await parseApiResponse(response);
        if (response.ok && data.success) {
            showClientNotice(
                data.message || "Attendance session started successfully.",
                "success"
            );
            if (Number.isFinite(data.session_id)) {
                const tableWrap = document.getElementById("teacher-active-sessions-wrap");
                const tbody = document.getElementById("teacher-active-sessions-body");
                const emptyState = document.getElementById("teacher-active-sessions-empty");
                const subjectSelect = document.getElementById("subject_id");
                const semesterSelect = document.getElementById("teacher_start_semester_id");
                if (tbody) {
                    const row = document.createElement("tr");
                    row.dataset.teacherSessionId = String(data.session_id);
                    const subjectCell = document.createElement("td");
                    const semesterCell = document.createElement("td");
                    const timeCell = document.createElement("td");
                    const actionCell = document.createElement("td");
                    subjectCell.textContent =
                        subjectSelect && subjectSelect.selectedOptions.length
                            ? subjectSelect.selectedOptions[0].textContent.trim()
                            : "Subject";
                    semesterCell.textContent =
                        semesterSelect && semesterSelect.selectedOptions.length
                            ? semesterSelect.selectedOptions[0].textContent.trim()
                            : "Semester";
                    timeCell.textContent = new Date().toLocaleString();
                    const stopBtn = document.createElement("button");
                    stopBtn.type = "button";
                    stopBtn.className = "btn-secondary";
                    stopBtn.textContent = "Stop";
                    stopBtn.addEventListener("click", () => stopTeacherSession(data.session_id));
                    actionCell.appendChild(stopBtn);
                    row.appendChild(subjectCell);
                    row.appendChild(semesterCell);
                    row.appendChild(timeCell);
                    row.appendChild(actionCell);
                    tbody.prepend(row);
                    if (tableWrap) tableWrap.style.display = "";
                    if (emptyState) emptyState.style.display = "none";
                }
                startTeacherLocationUpdates(data.session_id);
            }
            return;
        }
        alert(data.message);
    } catch (error) {
        alert("Could not connect to server. Check internet/VPN and try again.");
    } finally {
        reenableStartButton();
    }
}

async function stopTeacherSession(sessionId) {
    try {
        const response = await fetch(`/api/teacher/session/stop/${sessionId}`, {
            method: "POST",
        });
        const data = await parseApiResponse(response);
        alert(data.message);
        if (response.ok && data.success) {
            window.location.reload();
        }
    } catch (error) {
        alert("Could not stop session right now.");
    }
}

const teacherLocationIntervals = new Map();

function startTeacherLocationUpdates(sessionId) {
    const numericId = Number(sessionId);
    if (!Number.isFinite(numericId) || numericId <= 0) return;
    if (teacherLocationIntervals.has(numericId)) return;

    const updateOnce = async () => {
        try {
            const location = await getStrictGpsLocation({
                retries: 1,
                timeoutMs: 5000,
                accuracyMax: GPS_ACCURACY_THRESHOLD_M,
            });
            const payload = {
                session_id: numericId,
                latitude: location.latitude,
                longitude: location.longitude,
                accuracy: location.accuracy,
                device_id: getOrCreateDeviceId(),
            };
            await fetchWithRetry("/api/teacher/session/update-location", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
        } catch (error) {
            console.warn("[attendance] teacher location update failed", error);
        }
    };

    updateOnce();
    const intervalId = window.setInterval(updateOnce, 4000);
    teacherLocationIntervals.set(numericId, intervalId);
}

function setMarkedAttendanceButton(buttonEl) {
    if (!buttonEl) return;
    buttonEl.disabled = true;
    delete buttonEl.dataset.attendancePending;
    buttonEl.textContent = "Marked";
    buttonEl.classList.remove("btn-secondary");
    buttonEl.classList.add("btn-marked");
    buttonEl.removeAttribute("onclick");
}

async function markAttendance(sessionId, buttonEl) {
    if (buttonEl && buttonEl.dataset.attendancePending === "1") {
        return;
    }
    const payload = { session_id: Number(sessionId) };
    if (buttonEl) {
        buttonEl.dataset.attendancePending = "1";
        buttonEl.disabled = true;
        buttonEl.dataset.originalLabel = buttonEl.textContent;
        buttonEl.textContent = "Marking attendance...";
    }
    const releaseAttendanceButton = (keepDisabled = false) => {
        if (!buttonEl) return;
        delete buttonEl.dataset.attendancePending;
        if (!keepDisabled) {
            buttonEl.disabled = false;
            if (buttonEl.dataset.originalLabel) {
                buttonEl.textContent = buttonEl.dataset.originalLabel;
                delete buttonEl.dataset.originalLabel;
            }
        }
    };
    let teacherLocation = null;
    try {
        const response = await fetchWithRetry(
            `/api/active-session?session_id=${encodeURIComponent(sessionId)}&_ts=${Date.now()}`,
            {
            method: "GET",
            headers: { "Cache-Control": "no-store" },
            }
        );
        const data = await parseApiResponse(response);
        if (!response.ok || !data || !data.active) {
            showClientNotice((data && data.message) || "No active session.", "error");
            releaseAttendanceButton();
            return;
        }
        if (data && data.is_test_mode === false && data.gps_locked === false) {
            showClientNotice(
                "Teacher GPS is still stabilizing. Please wait 5–10 seconds and retry.",
                "error"
            );
            releaseAttendanceButton();
            return;
        }
        const teacherLat = Number.isFinite(data.teacher_lat) ? data.teacher_lat : data.lat;
        const teacherLng = Number.isFinite(data.teacher_lng) ? data.teacher_lng : data.lng;
        if (!Number.isFinite(teacherLat) || !Number.isFinite(teacherLng)) {
            showClientNotice("Session location unavailable.", "error");
            releaseAttendanceButton();
            return;
        }
        teacherLocation = {
            latitude: teacherLat,
            longitude: teacherLng,
            accuracy: Number.isFinite(data.accuracy) ? data.accuracy : 0,
        };
        payload.teacher_latitude = teacherLocation.latitude;
        payload.teacher_longitude = teacherLocation.longitude;
        console.log("[attendance] teacher location", {
            latitude: teacherLocation.latitude,
            longitude: teacherLocation.longitude,
            accuracy: teacherLocation.accuracy,
        });
    } catch (error) {
        showClientNotice("Could not connect to server. Please try again.", "error");
        releaseAttendanceButton();
        return;
    }

    let studentLocation = null;
    try {
        payload.device_id = getOrCreateDeviceId();
        const isMobile = isLikelyMobileDevice();
        let location = null;
        if (navigator.geolocation && window.isSecureContext && isMobile) {
            location = await withTimeout(
                getStrictGpsLocation({
                    retries: 2,
                    timeoutMs: 5000,
                    accuracyMax: GPS_ACCURACY_THRESHOLD_M,
                }).catch(() => null),
                5000,
                null
            );
            if (!location) {
                showClientNotice(
                    "Could not get an accurate GPS fix. Turn on Location Services + Wi-Fi, wait 5–10 seconds, then retry near a window/outdoor.",
                    "error"
                );
                releaseAttendanceButton();
                return;
            }
            studentLocation = {
                latitude: location.latitude,
                longitude: location.longitude,
                accuracy: location.accuracy,
            };
        } else {
            // Desktop/laptop testing mode: use a mock location close to teacher so strict 50m logic can be tested.
            payload.test_mode = true;
            studentLocation = {
                latitude: teacherLocation.latitude + 0.0003,
                longitude: teacherLocation.longitude,
                accuracy: 0,
            };
        }
        payload.latitude = studentLocation.latitude;
        payload.longitude = studentLocation.longitude;
        payload.accuracy = studentLocation.accuracy;
        console.log("[attendance] student location", {
            latitude: payload.latitude,
            longitude: payload.longitude,
            accuracy: payload.accuracy,
            test_mode: !!payload.test_mode,
        });
    } catch (error) {
        showClientNotice(gpsFailureMessage(error), "error");
        releaseAttendanceButton();
        return;
    }

    const distance = haversineMeters(
        studentLocation.latitude,
        studentLocation.longitude,
        teacherLocation.latitude,
        teacherLocation.longitude
    );
    const effectiveRadius = 50;
    console.log("[attendance] calculated distance (m)", distance);
    if (!Number.isFinite(distance)) {
        showClientNotice("Invalid distance calculation.", "error");
        releaseAttendanceButton();
        return;
    }
    // Don't block on client-side distance check; server is the source of truth and uses the latest teacher coords.

    try {
        const response = await fetchWithRetry("/api/student/attendance/mark", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        const data = await parseApiResponse(response);
        if (data && typeof data === "object") {
            if (
                Number.isFinite(data.teacher_latitude) &&
                Number.isFinite(data.teacher_longitude)
            ) {
                console.log("[attendance] teacher location", {
                    latitude: data.teacher_latitude,
                    longitude: data.teacher_longitude,
                    accuracy: data.teacher_accuracy,
                });
            }
            if (Number.isFinite(data.distance)) {
                console.log("[attendance] calculated distance (m)", data.distance);
            }
        }
        if (response.ok && data.success) {
            showClientNotice(
                data.already_marked ? "Attendance already marked." : "Attendance marked successfully.",
                "success"
            );
            setMarkedAttendanceButton(buttonEl);
            if (data.already_marked) {
                releaseAttendanceButton(true);
                return;
            }
            // Keep UI consistent if page is not reloaded immediately.
            const sessionButtons = document.querySelectorAll(`[data-session-id="${sessionId}"]`);
            sessionButtons.forEach((btn) => setMarkedAttendanceButton(btn));
            releaseAttendanceButton(true);
        } else {
            const message = data && data.message ? data.message : "Could not mark attendance.";
            const baseMessage =
                message && message.toLowerCase().includes("location is required")
                    ? "Please enable location to mark attendance."
                    : message;
            if (
                message &&
                message.toLowerCase().includes("accuracy") &&
                message.toLowerCase().includes("too low")
            ) {
                showClientNotice(message, "error");
                releaseAttendanceButton();
                return;
            }
            if (
                data &&
                data.message &&
                data.message.toLowerCase().includes("outside the allowed attendance range")
            ) {
                showClientNotice("You are outside the allowed attendance range", "error");
            } else {
                showClientNotice(baseMessage, "error");
            }
            releaseAttendanceButton();
        }
    } catch (error) {
        showClientNotice("Could not connect to server. Please try again.", "error");
        releaseAttendanceButton();
        return;
    }
}

function initTeacherLiveSessionUpdates() {
    const rows = document.querySelectorAll("[data-teacher-session-id]");
    if (!rows.length) return;
    rows.forEach((row) => {
        const sessionId = Number(row.dataset.teacherSessionId || "");
        if (Number.isFinite(sessionId) && sessionId > 0) {
            startTeacherLocationUpdates(sessionId);
        }
    });
}

document.addEventListener("DOMContentLoaded", () => {
    initGmailUsernameFields();
    initAjaxActionForms();
    initTeacherLiveSessionUpdates();
});
