/**
 * MEDOPROS - Intelligent Physician Assistant
 * Client-Side Logic
 */

const API_BASE = "";
const MAX_RECORDING_SECONDS = 600; // 10 минут (600 секунд)
const UI = {
    main: document.getElementById('main-screen'),
    result: document.getElementById('result-screen'),
    patient: document.getElementById('patient-screen'),
    message: document.getElementById('message-screen'),

    // UI Elements for Doctor Dashboard
    historyItems: document.getElementById('history-items'),

    // UI Elements for Results
    summary: document.getElementById('summary-text'),
    transcription: document.getElementById('transcription-text'),
    resultDate: document.getElementById('result-date'),
    backBtn: document.getElementById('back-btn'),
    deleteBtn: document.getElementById('delete-btn'),
    patientBanner: document.getElementById('patient-success-banner'),
    patientFinish: document.getElementById('patient-finish-container'),
    finishBtn: document.getElementById('finish-btn'),

    player: {
        playPauseBtn: document.getElementById('play-pause-btn'),
        playIcon: document.getElementById('play-icon'),
        pauseIcon: document.getElementById('pause-icon'),
        progress: document.getElementById('player-progress'),
        current: document.getElementById('current-time'),
        total: document.getElementById('total-duration')
    },

    // UI Elements for Patient Screen
    pElements: {
        welcome: document.getElementById('patient-welcome'),
        doctorTitle: document.getElementById('patient-doctor-title'),
        questions: document.getElementById('dynamic-questions'),
        recordBtn: document.getElementById('p-record-btn'),
        recordingControls: document.querySelector('.recording-controls'),
        statusText: document.getElementById('p-status-text'),
        statusDot: document.getElementById('p-status-dot'),
        timer: document.getElementById('p-timer'),
        postRecording: document.getElementById('p-post-recording'),
        previewPlayBtn: document.getElementById('p-preview-play-btn'),
        previewPlayIcon: document.getElementById('p-preview-play-icon'),
        previewPauseIcon: document.getElementById('p-preview-pause-icon'),
        previewTimer: document.getElementById('p-preview-timer'),
        previewProgress: document.getElementById('p-preview-progress'),
        rerecordBtn: document.getElementById('p-rerecord-btn'),
        sendBtn: document.getElementById('p-send-btn')
    }
};

const DOCTOR_MAP = {
    'therapist': 'Терапевт',
    'cardiologist': 'Кардиолог',
    'neurologist': 'Невролог',
    'default': 'Общий осмотр'
};

let mediaRecorder;
let audioChunks = [];
let startTime;
let timerInterval;
let currentAudio = null;
let currentRecordId = null;
let currentAudioBlob = null;
let previewAudio = null;
let isScrubbing = false;
let sessionToken = new URLSearchParams(window.location.search).get('token');

// --- Auth Utils ---
function getAuthHeader() {
    const token = localStorage.getItem('token');
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}

function initApp() {
    if (sessionToken) {
        initPatientMode();
    } else {
        showScreen('main');
        loadHistory();
    }
}

async function initPatientMode() {
    console.log("Initializing Patient Mode for token:", sessionToken);
    try {
        const res = await fetch(`${API_BASE}/api/sessions/${sessionToken}`);
        if (!res.ok) {
            const errorData = await res.json().catch(() => ({ detail: "Unknown error" }));
            const msg = errorData.detail === "Session link already used" ?
                "Эта ссылка уже была использована. Пожалуйста, обратитесь к врачу за новой ссылкой." :
                "Срок действия этой ссылки истек или она неверна.";

            showMessage("Доступ ограничен", msg);
            return;
        }

        const session = await res.json();
        console.log("Session loaded:", session);

        if (!session.config || !session.config.questions) {
            throw new Error("Ошибка конфигурации сессии сервера");
        }

        UI.pElements.welcome.textContent = session.patient_name ? `Здравствуйте, ${session.patient_name}` : "Здравствуйте";
        UI.pElements.doctorTitle.textContent = session.config.title || "Врач";

        UI.pElements.questions.innerHTML = session.config.questions.map(q => `<div class="question-item">${q}</div>`).join('');

        showScreen('patient');
    } catch (err) {
        console.error("Patient mode error:", err);
        showMessage("Ошибка", err.message);
    }
}

function showMessage(title, text) {
    document.getElementById('message-title').textContent = title;
    document.getElementById('message-text').textContent = text;
    showScreen('message');
}

function showScreen(screenId) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    const el = UI[screenId];
    if (el) {
        el.classList.add('active');
        // Hide global loader once any screen is shown
        const loader = document.getElementById('app-loading');
        if (loader && loader.style.display !== 'none') {
            loader.style.opacity = '0';
            setTimeout(() => loader.style.display = 'none', 500);
        }
    } else {
        console.warn(`Screen with ID "${screenId}" not found in UI object.`);
    }
}

// --- API Calls ---


async function loadHistory() {
    try {
        const res = await fetch(`${API_BASE}/api/history`, { headers: getAuthHeader() });
        const items = await res.json();
        renderHistory(items);
    } catch (err) {
        console.error('History load failed', err);
    }
}

// --- UI Rendering ---
function renderHistory(items) {
    UI.historyItems.innerHTML = items.length ? "" : '<p class="empty-state" style="text-align: center; color: var(--text-secondary); opacity: 0.7; padding: 1rem;">Результатов пока нет.<br><span style="font-size: 0.8rem;">Сгенерируйте ссылку выше и пройдите тестовый опрос.</span></p>';
    items.forEach(item => {
        const div = document.createElement('div');
        div.className = 'history-item';
        const dType = DOCTOR_MAP[item.doctor_type] || item.doctor_type || "Общий осмотр";
        const doctorTag = `<span class="item-tag">${dType}</span>`;
        const itext = item.text || "Запись без текста";

        div.innerHTML = `
            <div class="item-info">
                <div class="item-meta">${item.timestamp} ${doctorTag}</div>
                <div class="item-user"><b>${item.username || 'Пациент'}</b></div>
                <div class="item-text text-truncate">${itext.substring(0, 60)}${itext.length > 60 ? '...' : ''}</div>
            </div>
            <div class="item-arrow"><i data-lucide="chevron-right"></i></div>
        `;
        div.onclick = () => showResult(item);
        UI.historyItems.appendChild(div);
    });
    // Re-initialize icons for dynamic content
    if (window.lucide) lucide.createIcons();
}

function showResult(item, isPatient = false) {
    currentRecordId = item.id;
    UI.resultDate.textContent = item.timestamp || new Date().toLocaleString('ru-RU');
    UI.transcription.textContent = item.text || "Текст не распознан";
    UI.summary.innerHTML = item.summary || "Суммаризация недоступна";

    // Player Setup
    initPlayer(`/audio/${item.filename}`);

    // UI Toggles for Patient vs Doctor
    if (isPatient) {
        UI.backBtn.classList.add('hidden');
        UI.deleteBtn.classList.add('hidden');
        UI.patientBanner.classList.remove('hidden');
        UI.patientFinish.classList.remove('hidden');
    } else {
        UI.backBtn.classList.remove('hidden');
        UI.deleteBtn.classList.remove('hidden');
        UI.patientBanner.classList.add('hidden');
        UI.patientFinish.classList.add('hidden');
    }

    showScreen('result');
}

// --- Recording Logic ---
async function startRecording(isPatient = true) {
    try {
        const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') 
                         ? 'audio/webm;codecs=opus' 
                         : 'audio/ogg;codecs=opus';
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream, { mimeType });
        audioChunks = [];

        mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
        mediaRecorder.onstop = () => preparePreview(isPatient);

        mediaRecorder.start();
        startTimer(isPatient);

        UI.pElements.statusText.textContent = "Идет запись. Нажмите еще раз для остановки";
        UI.pElements.statusDot.classList.add('recording');
        UI.pElements.recordBtn.classList.add('active');
        UI.pElements.postRecording.classList.add('hidden');
    } catch (err) {
        alert("Доступ к микрофону запрещен или не поддерживается");
    }
}

function stopRecording(isPatient = true) {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
        mediaRecorder.stream.getTracks().forEach(t => t.stop());
        stopTimer(isPatient);

        UI.pElements.statusText.textContent = "Подождите, аудио обрабатывается...";
        UI.pElements.statusDot.classList.remove('recording');
        UI.pElements.recordBtn.classList.remove('active');
    }
}

function preparePreview(isPatient) {
    const mimeType = mediaRecorder.mimeType;
    currentAudioBlob = new Blob(audioChunks, { type: mimeType });
    const audioUrl = URL.createObjectURL(currentAudioBlob);

    UI.pElements.statusText.textContent = "Запись завершена. Проверьте её и отправьте врачу";
    UI.pElements.postRecording.classList.remove('hidden');
    UI.pElements.recordingControls.classList.add('hidden');

    initPreviewPlayer(audioUrl);
}

function initPreviewPlayer(url) {
    if (previewAudio) {
        previewAudio.pause();
        previewAudio = null;
    }
    previewAudio = new Audio(url);
    UI.pElements.previewTimer.textContent = "0:00";
    UI.pElements.previewProgress.value = 0;

    previewAudio.onloadedmetadata = () => {
        if (previewAudio.duration === Infinity) {
            previewAudio.currentTime = 1e101;
            const fixDuration = () => {
                previewAudio.removeEventListener('timeupdate', fixDuration);
                previewAudio.currentTime = 0;
                UI.pElements.previewTimer.textContent = formatTime(previewAudio.duration);
            };
            previewAudio.addEventListener('timeupdate', fixDuration);
        } else {
            UI.pElements.previewTimer.textContent = formatTime(previewAudio.duration);
        }
    };

    previewAudio.ontimeupdate = () => {
        if (isScrubbing || !previewAudio.duration || previewAudio.duration === Infinity) return;
        UI.pElements.previewTimer.textContent = formatTime(previewAudio.currentTime);
        UI.pElements.previewProgress.value = (previewAudio.currentTime / previewAudio.duration) * 100 || 0;
    };

    previewAudio.onended = () => {
        UI.pElements.previewPlayIcon.classList.remove('hidden');
        UI.pElements.previewPauseIcon.classList.add('hidden');
        UI.pElements.previewTimer.textContent = formatTime(previewAudio.duration);
        UI.pElements.previewProgress.value = 0;
    };

    UI.pElements.previewProgress.oninput = e => {
        if (!previewAudio || !previewAudio.duration || previewAudio.duration === Infinity) return;
        isScrubbing = true;
        UI.pElements.previewTimer.textContent = formatTime((e.target.value / 100) * previewAudio.duration);
    };

    UI.pElements.previewProgress.onchange = e => {
        if (previewAudio && previewAudio.duration && previewAudio.duration !== Infinity) {
            previewAudio.currentTime = (e.target.value / 100) * previewAudio.duration;
        }
        isScrubbing = false;
    };
}

function togglePreviewPlayback() {
    if (!previewAudio) return;
    if (previewAudio.paused) {
        previewAudio.play();
        UI.pElements.previewPlayIcon.classList.add('hidden');
        UI.pElements.previewPauseIcon.classList.remove('hidden');
    } else {
        previewAudio.pause();
        UI.pElements.previewPlayIcon.classList.remove('hidden');
        UI.pElements.previewPauseIcon.classList.add('hidden');
    }
}

async function uploadRecording(isPatient = true) {
    if (!currentAudioBlob) return;

    UI.pElements.statusText.textContent = "Идет отправка и анализ ИИ...";
    UI.pElements.sendBtn.textContent = "Отправляем...";
    UI.pElements.sendBtn.disabled = true;
    UI.pElements.rerecordBtn.disabled = true;

    const formData = new FormData();
    formData.append('file', currentAudioBlob, 'recording.wav');

    const headers = isPatient ? { 'token': sessionToken } : getAuthHeader();

    try {
        const res = await fetch(`${API_BASE}/transcribe`, {
            method: 'POST',
            headers: headers,
            body: formData
        });
        if (!res.ok) throw new Error('Ошибка сервера');
        const data = await res.json();

        // New behavior: show result screen to patient
        if (previewAudio) {
            previewAudio.pause();
            previewAudio.removeAttribute('src'); // Stop loading immediately
            previewAudio = null;
        }
        showResult(data, true);
        
        // Clear blob so we don't trigger the unsaved changes warning on exit
        currentAudioBlob = null;

    } catch (err) {
        alert("Не удалось отправить запись: " + err.message);
        UI.pElements.statusText.textContent = "Запись завершена. Проверьте её и отправьте врачу";
        UI.pElements.sendBtn.textContent = "Отправить врачу";
        UI.pElements.sendBtn.disabled = false;
        UI.pElements.rerecordBtn.disabled = false;
    }
}

function resetRecording() {
    if (previewAudio) {
        previewAudio.pause();
        previewAudio = null;
    }
    currentAudioBlob = null;
    audioChunks = [];
    UI.pElements.postRecording.classList.add('hidden');
    UI.pElements.recordingControls.classList.remove('hidden');
    UI.pElements.recordBtn.classList.remove('disabled');
    UI.pElements.statusText.textContent = "Нажмите красную кнопку, чтобы начать";
    UI.pElements.sendBtn.textContent = "Отправить врачу";
    UI.pElements.timer.textContent = "00:00";
}

// --- Player Logic ---
async function initPlayer(audioUrl) {
    if (currentAudio) {
        currentAudio.pause();
        currentAudio = null;
    }

    // Fetch as Blob to bypass server Range request limitations (fixes seeking)
    try {
        const res = await fetch(audioUrl, { headers: getAuthHeader() });
        if (res.ok) {
            const blob = await res.blob();
            audioUrl = URL.createObjectURL(blob);
        }
    } catch (e) {
        console.warn("Could not fetch audio as blob, falling back to direct URL", e);
    }

    currentAudio = new Audio(audioUrl);
    UI.player.progress.value = 0;
    UI.player.current.textContent = "0:00";

    currentAudio.onloadedmetadata = () => {
        if (currentAudio.duration === Infinity) {
            currentAudio.currentTime = 1e101;
            const fixDuration = () => {
                currentAudio.removeEventListener('timeupdate', fixDuration);
                currentAudio.currentTime = 0;
                UI.player.total.textContent = formatTime(currentAudio.duration);
            };
            currentAudio.addEventListener('timeupdate', fixDuration);
        } else {
            UI.player.total.textContent = formatTime(currentAudio.duration);
        }
    };

    currentAudio.ontimeupdate = () => {
        if (isScrubbing || !currentAudio.duration || currentAudio.duration === Infinity) return;
        UI.player.current.textContent = formatTime(currentAudio.currentTime);
        UI.player.progress.value = (currentAudio.currentTime / currentAudio.duration) * 100 || 0;
    };

    UI.player.progress.oninput = e => {
        if (!currentAudio || !currentAudio.duration || currentAudio.duration === Infinity) return;
        isScrubbing = true;
        UI.player.current.textContent = formatTime((e.target.value / 100) * currentAudio.duration);
    };

    UI.player.progress.onchange = e => {
        if (currentAudio && currentAudio.duration && currentAudio.duration !== Infinity) {
            currentAudio.currentTime = (e.target.value / 100) * currentAudio.duration;
        }
        isScrubbing = false;
    };

    currentAudio.onended = () => {
        UI.player.playIcon.classList.remove('hidden');
        UI.player.pauseIcon.classList.add('hidden');
    };
}

function togglePlayback() {
    if (!currentAudio) return;
    if (currentAudio.paused) {
        currentAudio.play();
        UI.player.playIcon.classList.add('hidden');
        UI.player.pauseIcon.classList.remove('hidden');
    } else {
        currentAudio.pause();
        UI.player.playIcon.classList.remove('hidden');
        UI.player.pauseIcon.classList.add('hidden');
    }
}

async function deleteCurrentRecord() {
    console.log("Delete button clicked. Current Record ID:", currentRecordId);
    if (!currentRecordId) {
        console.warn("Delete aborted: No currentRecordId found.");
        return;
    }

    if (!confirm('Вы уверены, что хотите удалить эту запись и файл?')) return;

    try {
        const res = await fetch(`${API_BASE}/api/history/${currentRecordId}`, {
            method: 'DELETE',
            headers: getAuthHeader()
        });
        if (res.ok) {
            showScreen('main');
            loadHistory();
        } else {
            alert('Ошибка при удалении');
        }
    } catch (err) {
        console.error('Delete failed', err);
    }
}

// --- Timer & Utils ---
function startTimer(isPatient = true) {
    startTime = Date.now();
    timerInterval = setInterval(() => {
        const seconds = Math.floor((Date.now() - startTime) / 1000);
        UI.pElements.timer.textContent = formatTime(seconds);

        // Автоматическая остановка при достижении лимита
        if (seconds >= MAX_RECORDING_SECONDS) {
            stopRecording(isPatient);
            alert("Достигнут максимальный лимит времени записи (10 минут). Запись остановлена.");
        }
    }, 1000);
}

function stopTimer(isPatient = true) {
    clearInterval(timerInterval);
    UI.pElements.timer.textContent = "00:00";
}

function formatTime(sec) {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
}


// --- Event Listeners ---


UI.player.playPauseBtn.onclick = togglePlayback;

const deleteBtn = document.getElementById('delete-btn');
if (deleteBtn) {
    deleteBtn.addEventListener('click', deleteCurrentRecord);
}



const logoutBtn = document.getElementById('logout-btn');
if (logoutBtn) {
    logoutBtn.onclick = () => {
        localStorage.removeItem('token');
        initApp();
    };
}

// --- Invite Logic ---
const generateLinkBtn = document.getElementById('generate-link-btn');
if (generateLinkBtn) {
    generateLinkBtn.onclick = async () => {
        const doctor_type = document.getElementById('doctor-type').value;
        const patient_name = document.getElementById('patient-name').value;

        try {
            const res = await fetch(`${API_BASE}/api/sessions/create`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeader()
                },
                body: JSON.stringify({ doctor_type, patient_name })
            });
            const data = await res.json();
            const link = `${window.location.origin}${window.location.pathname}?token=${data.token}`;

            document.getElementById('share-link').value = link;
            document.getElementById('generated-link-container').classList.remove('hidden');
        } catch (err) {
            alert('Ошибка при создании ссылки');
        }
    };
}

const copyLinkWrapper = document.getElementById('copy-link-wrapper');
if (copyLinkWrapper) {
    copyLinkWrapper.onclick = () => {
        const copyText = document.getElementById('share-link');
        const copyBtn = document.getElementById('copy-link-btn');
        const copyTextSpan = document.getElementById('copy-link-text');
        const copyIcon = document.getElementById('copy-icon');

        const showSuccess = () => {
            // Clear any text selection
            if (window.getSelection) {
                window.getSelection().removeAllRanges();
            } else if (document.selection) {
                document.selection.empty();
            }
            
            // Blur buttons to remove focus outlines
            copyBtn.blur();

            // Visual feedback
            copyLinkWrapper.classList.add('copied');
            copyBtn.style.background = '#22c55e';
            copyTextSpan.textContent = 'Скопировано!';
            if (copyIcon && window.lucide) {
                copyIcon.setAttribute('data-lucide', 'check');
                lucide.createIcons();
            }

            // Reset after 2 seconds
            setTimeout(() => {
                copyLinkWrapper.classList.remove('copied');
                copyBtn.style.background = '';
                copyTextSpan.textContent = 'Копировать';
                if (copyIcon && window.lucide) {
                    copyIcon.setAttribute('data-lucide', 'copy');
                    lucide.createIcons();
                }
            }, 2000);
        };

        const fallbackCopy = () => {
            const textArea = document.createElement("textarea");
            textArea.value = copyText.value;
            
            // Ensure the textarea is off-screen but part of the DOM
            textArea.style.position = "fixed";
            textArea.style.left = "-9999px";
            textArea.style.top = "0";
            textArea.style.opacity = "0";
            document.body.appendChild(textArea);
            
            textArea.focus();
            textArea.select();
            textArea.setSelectionRange(0, 99999); // For mobile devices
            
            try {
                const successful = document.execCommand('copy');
                if (successful) {
                    showSuccess();
                }
            } catch (err) {
                console.error('Fallback copy failed: ', err);
            }
            
            document.body.removeChild(textArea);
        };

        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(copyText.value).then(() => {
                showSuccess();
            }).catch(err => {
                console.error('Clipboard API failed, trying fallback: ', err);
                fallbackCopy();
            });
        } else {
            fallbackCopy();
        }
    };
}

const refreshHistoryBtn = document.getElementById('refresh-history-btn');
if (refreshHistoryBtn) {
    refreshHistoryBtn.onclick = () => {
        refreshHistoryBtn.style.transform = 'rotate(360deg)';
        refreshHistoryBtn.style.transition = 'transform 0.5s ease';
        loadHistory();
        setTimeout(() => {
            refreshHistoryBtn.style.transform = '';
            refreshHistoryBtn.style.transition = '';
        }, 500);
    };
}

// --- Patient Listeners ---
UI.pElements.recordBtn.onclick = () => {
    if (mediaRecorder?.state === 'recording') stopRecording(true);
    else startRecording(true);
};

UI.pElements.previewPlayBtn.onclick = togglePreviewPlayback;
UI.pElements.rerecordBtn.onclick = resetRecording;
UI.pElements.sendBtn.onclick = () => uploadRecording(true);

UI.finishBtn.onclick = () => {
    window.location.href = "/app"; // Send back to admin panel for demo flow
};

const backBtn = document.getElementById('back-btn');
if (backBtn) {
    backBtn.addEventListener('click', () => {
        if (currentAudio) currentAudio.pause();
        showScreen('main');
    });
}

// Start App
initApp();

// Initialize Lucide icons
if (window.lucide) {
    lucide.createIcons();
}

// Safety check before reload
window.onbeforeunload = function () {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        return "Запись идет. Если вы закроете страницу, данные будут потеряны.";
    }
    if (currentAudioBlob && UI.pElements.postRecording && !UI.pElements.postRecording.classList.contains('hidden')) {
        return "Запись еще не отправлена. Если вы закроете страницу, она будет потеряна.";
    }
};
