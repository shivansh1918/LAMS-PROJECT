function getCurrentPositionOnce(options) {
    return new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, options);
    });
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

    const payload = { subject_id: Number(subjectId) };
    try {
        const location = await captureLocationFast({
            preferHighAccuracy: true,
            timeoutMs: 10000,
            maxAgeMs: 0,
        });
        if (
            !Number.isFinite(location.latitude) ||
            !Number.isFinite(location.longitude) ||
            location.latitude < -90 ||
            location.latitude > 90 ||
            location.longitude < -180 ||
            location.longitude > 180
        ) {
            alert("Could not get valid GPS coordinates. Please enable location and try again.");
            reenableStartButton();
            return;
        }
        if (Number.isFinite(location.accuracy) && location.accuracy > 50) {
            console.warn("Low GPS accuracy, but starting session");
        }
        payload.latitude = location.latitude;
        payload.longitude = location.longitude;
        payload.accuracy = location.accuracy;
        console.log("[attendance] teacher location", {
            latitude: payload.latitude,
            longitude: payload.longitude,
            accuracy: payload.accuracy,
        });
    } catch (error) {
        alert(describeLocationOrNetworkError(error));
        reenableStartButton();
        return;
    }

    try {
        const response = await fetch("/api/teacher/session/start", {
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
            window.location.reload();
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
    try {
        const location = await captureLocationFast({
            preferHighAccuracy: true,
            timeoutMs: 10000,
            maxAgeMs: 0,
        });
        payload.latitude = location.latitude;
        payload.longitude = location.longitude;
        payload.accuracy = location.accuracy;
        console.log("[attendance] student location", {
            latitude: payload.latitude,
            longitude: payload.longitude,
            accuracy: payload.accuracy,
        });
    } catch (error) {
        showClientNotice(describeLocationOrNetworkError(error), "error");
        releaseAttendanceButton();
        return;
    }

    try {
        const response = await fetch("/api/student/attendance/mark", {
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
                data.message.toLowerCase().includes("outside the allowed attendance range") &&
                Number.isFinite(data.distance) &&
                Number.isFinite(data.allowed_radius)
            ) {
                const roundedDistance = Number(data.distance).toFixed(1);
                const roundedRadius = Number(data.allowed_radius).toFixed(0);
                showClientNotice(
                    `${data.message} (current ${roundedDistance}m of ${roundedRadius}m). Move closer and try again.`,
                    "error"
                );
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

document.addEventListener("DOMContentLoaded", () => {
    initGmailUsernameFields();
    initAjaxActionForms();
});
