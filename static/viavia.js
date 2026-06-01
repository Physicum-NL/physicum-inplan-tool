// Via Via Cadeau Inplan Tool — Frontend wizard (NL/EN)

// ─── Language ─────────────────────────────────────────
const LANG = (typeof PAGE_LANG !== 'undefined' && PAGE_LANG === 'en') ? 'en' : 'nl';

const MONTHS = {
    nl: ['januari', 'februari', 'maart', 'april', 'mei', 'juni', 'juli', 'augustus', 'september', 'oktober', 'november', 'december'],
    en: ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'],
}[LANG];

const DAYS = {
    nl: ['ma', 'di', 'wo', 'do', 'vr', 'za', 'zo'],
    en: ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'],
}[LANG];

const DAY_NAMES = {
    nl: ['zondag', 'maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag'],
    en: ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'],
}[LANG];

const T = {
    nl: {
        stepLabels: ['1e Training', '2e Training', 'Overzicht'],
        headings: ['1e Cadeau training', '2e Cadeau training'],
        subtitles: ['Kies een datum en tijd voor je eerste gratis sessie', 'Kies een datum en tijd voor je tweede gratis sessie'],
        tip: '<strong>Tip:</strong> Plan minimaal 1 rustdag tussen je trainingen voor het beste resultaat.',
        slotsTitle: 'Beschikbare tijden',
        slotsPlaceholder: 'Kies een datum om beschikbare tijden te zien',
        slotsLoading: 'Beschikbare tijden laden...',
        slotsEmpty: 'Geen beschikbare tijden op deze dag',
        slotsError: 'Fout bij laden. Probeer opnieuw.',
        btnNext: 'Verder',
        btnBack: 'Terug',
        btnConfirm: 'Trainingen bevestigen',
        btnSubmitting: 'Bezig met aanvragen...',
        overviewTitle: 'Overzicht',
        overviewSubtitle: 'Controleer je gekozen trainingen',
        sessionLabel: (n) => `${n}e cadeau training`,
        summaryLabel: (n) => `${n}e cadeau training`,
        priceLabel: 'Via Via Cadeau Pakket',
        priceAmount: 'Gratis',
        priceNote: 'Gratis cadeau — je ontvangt een bevestiging per e-mail.',
        successTitle: 'Je gratis trainingen zijn ingepland!',
        successSubtitle: 'Je ontvangt een bevestiging per e-mail.',
        calendarLoading: 'Beschikbare dagen laden...',
        calendarErrorMsg: 'Er ging iets mis bij het laden van de beschikbaarheid. Probeer het opnieuw.',
        calendarErrorRetry: 'Probeer opnieuw',
        loading: 'Even geduld...',
        errorEmail: 'E-mailadres ontbreekt. Zorg dat je via de juiste link op deze pagina bent gekomen.',
        errorOrder: (n) => `De ${n}e training moet na de ${n-1}e training gepland worden. Ga terug en kies een latere datum.`,
        errorGeneric: 'Er ging iets mis. Controleer je internetverbinding en probeer opnieuw.',
        redirectUrl: 'https://physicum-pt.nl/bedankt/introductiepakket',
    },
    en: {
        stepLabels: ['1st Session', '2nd Session', 'Overview'],
        headings: ['1st Gift session', '2nd Gift session'],
        subtitles: ['Choose a date and time for your first free session', 'Choose a date and time for your second free session'],
        tip: '<strong>Tip:</strong> Schedule at least 1 rest day between your sessions for the best results.',
        slotsTitle: 'Available times',
        slotsPlaceholder: 'Select a date to see available times',
        slotsLoading: 'Loading available times...',
        slotsEmpty: 'No available times on this day',
        slotsError: 'Error loading. Please try again.',
        btnNext: 'Next',
        btnBack: 'Back',
        btnConfirm: 'Confirm sessions',
        btnSubmitting: 'Submitting...',
        overviewTitle: 'Overview',
        overviewSubtitle: 'Review your selected sessions',
        sessionLabel: (n) => `${n}${n===1?'st':'nd'} gift session`,
        summaryLabel: (n) => `${n}${n===1?'st':'nd'} gift session`,
        priceLabel: 'Via Via Gift Package',
        priceAmount: 'Free',
        priceNote: 'Free gift — you\'ll receive a confirmation by email.',
        successTitle: 'Your free sessions are scheduled!',
        successSubtitle: 'You\'ll receive a confirmation by email.',
        calendarLoading: 'Loading available days...',
        calendarErrorMsg: 'Something went wrong loading availability. Please try again.',
        calendarErrorRetry: 'Try again',
        loading: 'Please wait...',
        errorEmail: 'Email address is missing. Make sure you arrived via the correct link.',
        errorOrder: (n) => `Session ${n} must be after session ${n-1}. Go back and choose a later date.`,
        errorGeneric: 'Something went wrong. Check your internet connection and try again.',
        redirectUrl: 'https://physicum-pt.nl/bedankt/introductiepakket?lang=en',
    },
}[LANG];

// State
let currentStep = 0;
const totalSteps = 3;
const selections = [null, null];
const calendarStates = [{}, {}];

// ─── Initialisatie ────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    applyStaticTranslations();
    for (let i = 0; i < 2; i++) {
        const now = new Date();
        calendarStates[i] = { year: now.getFullYear(), month: now.getMonth() };
        renderCalendar(i);
    }
    updateUI();
});

function applyStaticTranslations() {
    // Step labels
    const labels = document.querySelectorAll('.steps-labels span');
    T.stepLabels.forEach((label, i) => { if (labels[i]) labels[i].textContent = label; });

    // Wizard step headings and subtitles
    for (let i = 0; i < 2; i++) {
        const step = document.querySelector(`.wizard-step[data-step="${i}"]`);
        if (step) {
            step.querySelector('h2').textContent = T.headings[i];
            step.querySelector('.step-subtitle').textContent = T.subtitles[i];
            const tip = step.querySelector('.tip-banner');
            if (tip) tip.innerHTML = T.tip;
        }
    }

    // Overview step
    const overviewStep = document.querySelector('.wizard-step[data-step="2"]');
    if (overviewStep) {
        overviewStep.querySelector('h2').textContent = T.overviewTitle;
        overviewStep.querySelector('.step-subtitle').textContent = T.overviewSubtitle;
        overviewStep.querySelector('.price-label').textContent = T.priceLabel;
        overviewStep.querySelector('.price-amount').textContent = T.priceAmount;
        overviewStep.querySelector('.price-note').textContent = T.priceNote;
        overviewStep.querySelector('#btn-confirm').textContent = T.btnConfirm;
    }

    // Success step
    const successStep = document.querySelector('.wizard-step[data-step="3"]');
    if (successStep) {
        successStep.querySelector('h2').textContent = T.successTitle;
        successStep.querySelector('.step-subtitle').textContent = T.successSubtitle;
    }

    // Navigation buttons
    document.getElementById('btn-next').textContent = T.btnNext;
    document.getElementById('btn-back').textContent = T.btnBack;

    // Loading
    const loadingP = document.querySelector('.loading-overlay p');
    if (loadingP) loadingP.textContent = T.loading;
}

// ─── Navigatie ────────────────────────────────────────

function nextStep() {
    if (currentStep < 2 && !selections[currentStep]) return;
    if (currentStep < 3) {
        currentStep++;
        if (currentStep === 2) renderOverview();
        updateUI();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }
}

function prevStep() {
    if (currentStep > 0) {
        currentStep--;
        updateUI();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }
}

function updateUI() {
    document.querySelectorAll('.wizard-step').forEach(el => {
        el.style.display = parseInt(el.dataset.step) === currentStep ? '' : 'none';
    });

    document.querySelectorAll('.step-dot').forEach(dot => {
        const step = parseInt(dot.dataset.step);
        dot.classList.remove('active', 'completed');
        if (step === currentStep) dot.classList.add('active');
        else if (step < currentStep) dot.classList.add('completed');
    });

    document.querySelectorAll('.step-line').forEach((line, i) => {
        line.classList.toggle('completed', i < currentStep);
    });

    const btnBack = document.getElementById('btn-back');
    const btnNext = document.getElementById('btn-next');
    const navButtons = document.getElementById('nav-buttons');

    btnBack.style.display = currentStep > 0 && currentStep < 3 ? '' : 'none';

    if (currentStep >= 2) {
        navButtons.style.display = 'none';
    } else {
        navButtons.style.display = '';
        btnNext.disabled = !selections[currentStep];
    }

    renderSummaryBefore(1);
}


// ─── Kalender ─────────────────────────────────────────

async function renderCalendar(stepIndex) {
    const picker = document.querySelector(`.session-picker[data-step="${stepIndex}"]`);
    const listingId = picker.dataset.listingId;
    const state = calendarStates[stepIndex];

    const monthStr = `${state.year}${String(state.month + 1).padStart(2, '0')}`;
    const monthLabel = `${MONTHS[state.month]} ${state.year}`;

    // Toon spinner terwijl we beschikbare datums uit Trainin ophalen
    picker.innerHTML = `
        <div class="calendar-loading">
            <div class="spinner-sm"></div>
            <span>${T.calendarLoading}</span>
        </div>
    `;

    let availableDates = [];
    let loadFailed = false;
    try {
        const resp = await fetch(`/api/dates/${listingId}?month=${monthStr}`);
        const data = await resp.json();
        if (!resp.ok || data.error) {
            loadFailed = true;
            console.error('Error fetching dates (status %s):', resp.status, data.error || data);
        } else {
            availableDates = data.dates || [];
        }
    } catch (e) {
        loadFailed = true;
        console.error('Error fetching dates:', e);
    }

    // Bij een fout: toon een nette fout-staat met retry-knop (hard refresh) en stop verder renderen
    if (loadFailed) {
        picker.innerHTML = `
            <div class="calendar-error">
                <div class="calendar-error-icon">!</div>
                <div class="calendar-error-msg">${T.calendarErrorMsg}</div>
                <button class="calendar-error-retry" onclick="window.location.reload()">${T.calendarErrorRetry}</button>
            </div>
        `;
        return;
    }

    const todayObj = new Date();
    const todayStr = todayObj.toISOString().split('T')[0];
    const tomorrowObj = new Date(todayObj);
    tomorrowObj.setDate(tomorrowObj.getDate() + 1);
    let minDateStr = tomorrowObj.toISOString().split('T')[0];

    if (stepIndex > 0 && selections[stepIndex - 1]) {
        const prevDateObj = new Date(selections[stepIndex - 1].date + 'T12:00:00');
        prevDateObj.setDate(prevDateObj.getDate() + 1);
        const nextDayStr = prevDateObj.toISOString().split('T')[0];
        if (nextDayStr > minDateStr) minDateStr = nextDayStr;
    }

    const blockedDates = [];
    for (let i = 0; i < stepIndex; i++) {
        if (selections[i]) blockedDates.push(selections[i].date);
    }

    const now = new Date();
    const canGoPrev = state.year > now.getFullYear() ||
        (state.year === now.getFullYear() && state.month > now.getMonth());

    const firstDay = new Date(state.year, state.month, 1);
    let startDay = firstDay.getDay() - 1;
    if (startDay < 0) startDay = 6;
    const daysInMonth = new Date(state.year, state.month + 1, 0).getDate();

    let html = `
        <div class="calendar">
            <div class="calendar-header">
                <button class="calendar-nav" onclick="changeMonth(${stepIndex}, -1)" ${canGoPrev ? '' : 'disabled'}>&larr;</button>
                <span class="calendar-title">${monthLabel}</span>
                <button class="calendar-nav" onclick="changeMonth(${stepIndex}, 1)">&rarr;</button>
            </div>
            <div class="calendar-grid">
                ${DAYS.map(d => `<div class="day-header">${d}</div>`).join('')}
    `;

    for (let i = 0; i < startDay; i++) {
        html += '<div class="calendar-day other-month"></div>';
    }

    for (let day = 1; day <= daysInMonth; day++) {
        const dateStr = `${state.year}-${String(state.month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
        const isBlocked = blockedDates.includes(dateStr);
        const isAvailable = availableDates.includes(dateStr) && dateStr >= minDateStr && !isBlocked;
        const isToday = dateStr === todayStr;
        const isSelected = selections[stepIndex] && selections[stepIndex].date === dateStr;
        const isBeforeMin = dateStr < minDateStr || isBlocked;

        let classes = 'calendar-day';
        if (isAvailable) classes += ' available';
        if (isToday) classes += ' today';
        if (isSelected) classes += ' selected';
        if (isBeforeMin && !isAvailable) classes += ' disabled-date';

        const onClick = isAvailable ? `onclick="selectDate(${stepIndex}, '${dateStr}')"` : '';
        html += `<div class="${classes}" ${onClick}>${day}</div>`;
    }

    html += '</div></div>';
    html += `<div class="slots-container" id="slots-${stepIndex}">`;
    html += `<div class="slots-placeholder">${T.slotsPlaceholder}</div>`;
    html += '</div>';

    picker.innerHTML = html;

    if (selections[stepIndex] && selections[stepIndex].date) {
        const dateInMonth = new Date(selections[stepIndex].date);
        if (dateInMonth.getMonth() === state.month && dateInMonth.getFullYear() === state.year) {
            loadSlots(stepIndex, selections[stepIndex].date);
        }
    }
}

function changeMonth(stepIndex, delta) {
    const state = calendarStates[stepIndex];
    state.month += delta;
    if (state.month > 11) { state.month = 0; state.year++; }
    if (state.month < 0) { state.month = 11; state.year--; }
    renderCalendar(stepIndex);
}

async function selectDate(stepIndex, dateStr) {
    if (selections[stepIndex] && selections[stepIndex].date !== dateStr) {
        selections[stepIndex] = null;
        updateUI();
    }

    const picker = document.querySelector(`.session-picker[data-step="${stepIndex}"]`);
    picker.querySelectorAll('.calendar-day').forEach(el => el.classList.remove('selected'));
    picker.querySelectorAll('.calendar-day.available').forEach(el => {
        if (el.getAttribute('onclick') && el.getAttribute('onclick').includes(dateStr)) {
            el.classList.add('selected');
        }
    });

    await loadSlots(stepIndex, dateStr);
}

async function loadSlots(stepIndex, dateStr) {
    const picker = document.querySelector(`.session-picker[data-step="${stepIndex}"]`);
    const listingId = picker.dataset.listingId;
    const container = document.getElementById(`slots-${stepIndex}`);

    container.innerHTML = `<div class="slots-loading">${T.slotsLoading}</div>`;

    // Op mobiel: scroll meteen naar het tijden-blok zodat de gebruiker
    // direct ziet dat er geladen wordt en niet hoeft te scrollen na keuze.
    if (window.matchMedia('(max-width: 640px)').matches) {
        container.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    try {
        const resp = await fetch(`/api/slots/${listingId}?date=${dateStr}`);
        const data = await resp.json();
        const slots = data.slots || [];

        if (slots.length === 0) {
            container.innerHTML = `<div class="slots-empty">${T.slotsEmpty}</div>`;
            return;
        }

        window._slotsData = window._slotsData || {};
        window._slotsData[stepIndex] = { date: dateStr, slots: slots };

        let html = `<div class="slots-title">${T.slotsTitle}</div><div class="slots-grid">`;
        for (let si = 0; si < slots.length; si++) {
            const slot = slots[si];
            const isSelected = selections[stepIndex] &&
                selections[stepIndex].date === dateStr &&
                selections[stepIndex].start === slot.start;
            const selectedClass = isSelected ? ' selected' : '';

            html += `<button class="slot-btn${selectedClass}" onclick="pickSlot(${stepIndex}, ${si})">${slot.start}</button>`;
        }
        html += '</div>';
        container.innerHTML = html;

    } catch (e) {
        console.error('Error fetching slots:', e);
        container.innerHTML = `<div class="slots-empty">${T.slotsError}</div>`;
    }
}

function pickSlot(stepIndex, slotIndex) {
    const data = window._slotsData[stepIndex];
    selectSlot(stepIndex, data.date, data.slots[slotIndex]);
}

function selectSlot(stepIndex, dateStr, slot) {
    selections[stepIndex] = {
        date: dateStr,
        start: slot.start,
        end: slot.end,
        instructor: slot.instructor,
        instructor_id: slot.instructor_id,
        key: slot.key,
        listing_id: parseInt(document.querySelector(`.session-picker[data-step="${stepIndex}"]`).dataset.listingId),
    };

    const container = document.getElementById(`slots-${stepIndex}`);
    container.querySelectorAll('.slot-btn').forEach(btn => {
        btn.classList.remove('selected');
        if (btn.textContent.trim().startsWith(slot.start)) {
            btn.classList.add('selected');
        }
    });

    for (let i = stepIndex + 1; i < 2; i++) {
        if (selections[i]) {
            if (selections[i].date <= selections[stepIndex].date) {
                selections[i] = null;
                renderCalendar(i);
            }
        }
    }

    for (let i = stepIndex + 1; i < 2; i++) {
        if (!selections[i] || selections[i].date > selections[stepIndex].date) {
            renderCalendar(i);
        }
    }

    updateUI();

    // Na slot-keuze: scroll naar het einde van de pagina. De Verder-knop
    // staat onderaan, dus die komt sowieso in beeld. Idempotent: zit je al
    // onderaan dan gebeurt er niets meer — geen jitter bij volgende klikken.
    setTimeout(() => {
        window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' });
    }, 100);
}


// ─── Samenvattingen ───────────────────────────────────

function renderSummaryBefore(stepIndex) {
    const container = document.getElementById(`summary-before-${stepIndex}`);
    if (!container) return;

    let html = '';
    for (let i = 0; i < stepIndex; i++) {
        if (selections[i]) html += renderSummaryCard(i);
    }
    container.innerHTML = html;
}

function renderSummaryCard(stepIndex) {
    const sel = selections[stepIndex];
    if (!sel) return '';

    const dateObj = new Date(sel.date + 'T12:00:00');
    const dayName = DAY_NAMES[dateObj.getDay()];
    const day = dateObj.getDate();
    const month = MONTHS[dateObj.getMonth()];

    return `
        <div class="summary-card">
            <div class="check">&#10003;</div>
            <div class="info">
                <div class="title">${T.summaryLabel(stepIndex + 1)}</div>
                <div class="detail">${dayName} ${day} ${month} ${LANG === 'nl' ? 'om' : 'at'} ${sel.start}</div>
            </div>
        </div>
    `;
}


// ─── Overzicht ────────────────────────────────────────

function renderOverview() {
    const container = document.getElementById('overview-cards');
    let html = '';

    for (let i = 0; i < 2; i++) {
        const sel = selections[i];
        if (!sel) continue;

        const dateObj = new Date(sel.date + 'T12:00:00');
        const dayName = DAY_NAMES[dateObj.getDay()];
        const day = dateObj.getDate();
        const month = MONTHS[dateObj.getMonth()];

        html += `
            <div class="overview-card">
                <div class="session-num">${T.sessionLabel(i + 1)}</div>
                <div class="session-date">${dayName} ${day} ${month}</div>
                <div class="session-time">${sel.start} - ${sel.end}</div>
            </div>
        `;
    }

    container.innerHTML = html;
}


// ─── Client data uit URL parameters ──────────────────

function getClientData() {
    const params = new URLSearchParams(window.location.search);
    return {
        first_name: params.get('firstname') || '',
        last_name: params.get('lastname') || '',
        email: params.get('email') || '',
        phone: params.get('telephone') || '',
    };
}


// ─── Booking ──────────────────────────────────────────

async function submitBooking() {
    const clientData = getClientData();
    if (!clientData.email) {
        alert(T.errorEmail);
        return;
    }

    for (let i = 1; i < 2; i++) {
        if (selections[i] && selections[i - 1] && selections[i].date <= selections[i - 1].date) {
            alert(T.errorOrder(i + 1));
            return;
        }
    }

    const btn = document.getElementById('btn-confirm');
    btn.disabled = true;
    btn.textContent = T.btnSubmitting;
    document.getElementById('loading').style.display = '';

    try {
        const resp = await fetch('/api/book-viavia', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                client: clientData,
                sessions: selections.map(s => ({
                    listing_id: s.listing_id,
                    date: s.date,
                    start: s.start,
                    end: s.end,
                    instructor: s.instructor || '',
                    instructor_id: s.instructor_id,
                    key: s.key,
                })),
            }),
        });

        const data = await resp.json();

        if (data.success) {
            // Redirect naar bedankpagina
            const customRedirect = new URLSearchParams(window.location.search).get('redirect');
            window.location.href = customRedirect || T.redirectUrl;
        } else {
            alert((LANG === 'nl' ? 'Er ging iets mis: ' : 'Something went wrong: ') + (data.error || ''));
            btn.disabled = false;
            btn.textContent = T.btnConfirm;
        }

    } catch (e) {
        console.error('Booking error:', e);
        alert(T.errorGeneric);
        btn.disabled = false;
        btn.textContent = T.btnConfirm;
    } finally {
        document.getElementById('loading').style.display = 'none';
    }
}

function renderSuccess() {
    const container = document.getElementById('success-details');
    let html = '<div style="margin-top: 24px">';
    for (let i = 0; i < 2; i++) {
        html += renderSummaryCard(i);
    }
    html += '</div>';
    container.innerHTML = html;
}
