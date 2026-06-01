// Telefonisch Kennismaken Inplan Tool — Frontend wizard (NL/EN)

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
        stepLabels: ['Kies moment', 'Bevestig'],
        heading: 'Telefonisch kennismaken',
        subtitle: 'Kies een datum en tijd voor je kennismakingsgesprek',
        tip: '<strong>Tip:</strong> We bellen je op het gekozen tijdstip. Zorg dat je bereikbaar bent.',
        slotsTitle: 'Beschikbare tijden',
        slotsPlaceholder: 'Kies een datum om beschikbare tijden te zien',
        slotsLoading: 'Beschikbare tijden laden...',
        slotsEmpty: 'Geen beschikbare tijden op deze dag',
        slotsError: 'Fout bij laden. Probeer opnieuw.',
        btnNext: 'Verder',
        btnBack: 'Terug',
        btnConfirm: 'Gesprek bevestigen',
        btnSubmitting: 'Bezig met aanvragen...',
        overviewTitle: 'Overzicht',
        overviewSubtitle: 'Controleer je gekozen moment',
        sessionLabel: 'Kennismakingsgesprek',
        priceLabel: 'Telefonisch kennismaken',
        priceAmount: 'Gratis',
        priceNote: 'We bellen je op het gekozen moment. Je ontvangt een bevestiging per e-mail.',
        successTitle: 'Je kennismakingsgesprek is ingepland!',
        successSubtitle: 'Je ontvangt een bevestiging per e-mail. We bellen je op het afgesproken moment.',
        calendarLoading: 'Beschikbare dagen laden...',
        calendarErrorMsg: 'Er ging iets mis bij het laden van de beschikbaarheid. Probeer het opnieuw.',
        calendarErrorRetry: 'Probeer opnieuw',
        loading: 'Even geduld...',
        errorEmail: 'E-mailadres ontbreekt. Zorg dat je via de juiste link op deze pagina bent gekomen.',
        errorGeneric: 'Er ging iets mis. Controleer je internetverbinding en probeer opnieuw.',
        redirectUrl: 'https://physicum-pt.nl/bedankt/kennismaken',
    },
    en: {
        stepLabels: ['Choose time', 'Confirm'],
        heading: 'Phone introduction',
        subtitle: 'Choose a date and time for your introduction call',
        tip: '<strong>Tip:</strong> We\'ll call you at the chosen time. Make sure you\'re available.',
        slotsTitle: 'Available times',
        slotsPlaceholder: 'Select a date to see available times',
        slotsLoading: 'Loading available times...',
        slotsEmpty: 'No available times on this day',
        slotsError: 'Error loading. Please try again.',
        btnNext: 'Next',
        btnBack: 'Back',
        btnConfirm: 'Confirm call',
        btnSubmitting: 'Submitting...',
        overviewTitle: 'Overview',
        overviewSubtitle: 'Review your selected time',
        sessionLabel: 'Introduction call',
        priceLabel: 'Phone introduction',
        priceAmount: 'Free',
        priceNote: 'We\'ll call you at the chosen time. You\'ll receive a confirmation by email.',
        successTitle: 'Your introduction call is scheduled!',
        successSubtitle: 'You\'ll receive a confirmation by email. We\'ll call you at the agreed time.',
        calendarLoading: 'Loading available days...',
        calendarErrorMsg: 'Something went wrong loading availability. Please try again.',
        calendarErrorRetry: 'Try again',
        loading: 'Please wait...',
        errorEmail: 'Email address is missing. Make sure you arrived via the correct link.',
        errorGeneric: 'Something went wrong. Check your internet connection and try again.',
        redirectUrl: 'https://physicum-pt.nl/bedankt/kennismaken?lang=en',
    },
}[LANG];

// State
let currentStep = 0;
let selection = null;
let calendarState = {};

// ─── Initialisatie ────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    applyStaticTranslations();
    const now = new Date();
    calendarState = { year: now.getFullYear(), month: now.getMonth() };
    renderCalendar();
    updateUI();
});

function applyStaticTranslations() {
    const labels = document.querySelectorAll('.steps-labels span');
    T.stepLabels.forEach((label, i) => { if (labels[i]) labels[i].textContent = label; });

    const step0 = document.querySelector('.wizard-step[data-step="0"]');
    if (step0) {
        step0.querySelector('h2').textContent = T.heading;
        step0.querySelector('.step-subtitle').textContent = T.subtitle;
        const tip = step0.querySelector('.tip-banner');
        if (tip) tip.innerHTML = T.tip;
    }

    const overviewStep = document.querySelector('.wizard-step[data-step="1"]');
    if (overviewStep) {
        overviewStep.querySelector('h2').textContent = T.overviewTitle;
        overviewStep.querySelector('.step-subtitle').textContent = T.overviewSubtitle;
        overviewStep.querySelector('.price-label').textContent = T.priceLabel;
        overviewStep.querySelector('.price-amount').textContent = T.priceAmount;
        overviewStep.querySelector('.price-note').textContent = T.priceNote;
        overviewStep.querySelector('#btn-confirm').textContent = T.btnConfirm;
    }

    const successStep = document.querySelector('.wizard-step[data-step="2"]');
    if (successStep) {
        successStep.querySelector('h2').textContent = T.successTitle;
        successStep.querySelector('.step-subtitle').textContent = T.successSubtitle;
    }

    document.getElementById('btn-next').textContent = T.btnNext;
    document.getElementById('btn-back').textContent = T.btnBack;

    const loadingP = document.querySelector('.loading-overlay p');
    if (loadingP) loadingP.textContent = T.loading;
}

// ─── Navigatie ────────────────────────────────────────

function nextStep() {
    if (currentStep === 0 && !selection) return;
    if (currentStep < 2) {
        currentStep++;
        if (currentStep === 1) renderOverview();
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

    btnBack.style.display = currentStep > 0 && currentStep < 2 ? '' : 'none';

    if (currentStep >= 1) {
        navButtons.style.display = 'none';
    } else {
        navButtons.style.display = '';
        btnNext.disabled = !selection;
    }
}

// ─── Kalender ─────────────────────────────────────────

async function renderCalendar() {
    const picker = document.querySelector('.session-picker[data-step="0"]');
    const listingId = picker.dataset.listingId;

    const monthStr = `${calendarState.year}${String(calendarState.month + 1).padStart(2, '0')}`;
    const monthLabel = `${MONTHS[calendarState.month]} ${calendarState.year}`;

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
    const minDateStr = tomorrowObj.toISOString().split('T')[0];

    const now = new Date();
    const canGoPrev = calendarState.year > now.getFullYear() ||
        (calendarState.year === now.getFullYear() && calendarState.month > now.getMonth());

    const firstDay = new Date(calendarState.year, calendarState.month, 1);
    let startDay = firstDay.getDay() - 1;
    if (startDay < 0) startDay = 6;
    const daysInMonth = new Date(calendarState.year, calendarState.month + 1, 0).getDate();

    let html = `
        <div class="calendar">
            <div class="calendar-header">
                <button class="calendar-nav" onclick="changeMonth(-1)" ${canGoPrev ? '' : 'disabled'}>&larr;</button>
                <span class="calendar-title">${monthLabel}</span>
                <button class="calendar-nav" onclick="changeMonth(1)">&rarr;</button>
            </div>
            <div class="calendar-grid">
                ${DAYS.map(d => `<div class="day-header">${d}</div>`).join('')}
    `;

    for (let i = 0; i < startDay; i++) {
        html += '<div class="calendar-day other-month"></div>';
    }

    for (let day = 1; day <= daysInMonth; day++) {
        const dateStr = `${calendarState.year}-${String(calendarState.month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
        const isAvailable = availableDates.includes(dateStr) && dateStr >= minDateStr;
        const isToday = dateStr === todayStr;
        const isSelected = selection && selection.date === dateStr;
        const isBeforeMin = dateStr < minDateStr;

        let classes = 'calendar-day';
        if (isAvailable) classes += ' available';
        if (isToday) classes += ' today';
        if (isSelected) classes += ' selected';
        if (isBeforeMin && !isAvailable) classes += ' disabled-date';

        const onClick = isAvailable ? `onclick="selectDate('${dateStr}')"` : '';
        html += `<div class="${classes}" ${onClick}>${day}</div>`;
    }

    html += '</div></div>';
    html += '<div class="slots-container" id="slots-0">';
    html += `<div class="slots-placeholder">${T.slotsPlaceholder}</div>`;
    html += '</div>';

    picker.innerHTML = html;

    if (selection && selection.date) {
        const dateInMonth = new Date(selection.date);
        if (dateInMonth.getMonth() === calendarState.month && dateInMonth.getFullYear() === calendarState.year) {
            loadSlots(selection.date);
        }
    }
}

function changeMonth(delta) {
    calendarState.month += delta;
    if (calendarState.month > 11) { calendarState.month = 0; calendarState.year++; }
    if (calendarState.month < 0) { calendarState.month = 11; calendarState.year--; }
    renderCalendar();
}

async function selectDate(dateStr) {
    if (selection && selection.date !== dateStr) {
        selection = null;
        updateUI();
    }

    const picker = document.querySelector('.session-picker[data-step="0"]');
    picker.querySelectorAll('.calendar-day').forEach(el => el.classList.remove('selected'));
    picker.querySelectorAll('.calendar-day.available').forEach(el => {
        if (el.getAttribute('onclick') && el.getAttribute('onclick').includes(dateStr)) {
            el.classList.add('selected');
        }
    });

    await loadSlots(dateStr);
}

async function loadSlots(dateStr) {
    const picker = document.querySelector('.session-picker[data-step="0"]');
    const listingId = picker.dataset.listingId;
    const container = document.getElementById('slots-0');

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

        window._slotsData = { date: dateStr, slots: slots };

        let html = `<div class="slots-title">${T.slotsTitle}</div><div class="slots-grid">`;
        for (let si = 0; si < slots.length; si++) {
            const slot = slots[si];
            const isSelected = selection &&
                selection.date === dateStr &&
                selection.start === slot.start;
            const selectedClass = isSelected ? ' selected' : '';

            html += `<button class="slot-btn${selectedClass}" onclick="pickSlot(${si})">${slot.start}</button>`;
        }
        html += '</div>';
        container.innerHTML = html;

    } catch (e) {
        console.error('Error fetching slots:', e);
        container.innerHTML = `<div class="slots-empty">${T.slotsError}</div>`;
    }
}

function pickSlot(slotIndex) {
    const data = window._slotsData;
    const slot = data.slots[slotIndex];

    selection = {
        date: data.date,
        start: slot.start,
        end: slot.end,
        instructor: slot.instructor,
        instructor_id: slot.instructor_id,
        key: slot.key,
        listing_id: LISTING_ID,
    };

    const container = document.getElementById('slots-0');
    container.querySelectorAll('.slot-btn').forEach(btn => {
        btn.classList.remove('selected');
        if (btn.textContent.trim().startsWith(slot.start)) {
            btn.classList.add('selected');
        }
    });

    updateUI();

    // Na slot-keuze: scroll naar het einde van de pagina. De Verder-knop
    // staat onderaan, dus die komt sowieso in beeld. Idempotent: zit je al
    // onderaan dan gebeurt er niets meer — geen jitter bij volgende klikken.
    setTimeout(() => {
        window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' });
    }, 100);
}

// ─── Overzicht ────────────────────────────────────────

function renderOverview() {
    const container = document.getElementById('overview-cards');
    if (!selection) return;

    const dateObj = new Date(selection.date + 'T12:00:00');
    const dayName = DAY_NAMES[dateObj.getDay()];
    const day = dateObj.getDate();
    const month = MONTHS[dateObj.getMonth()];

    container.innerHTML = `
        <div class="overview-card">
            <div class="session-num">${T.sessionLabel}</div>
            <div class="session-date">${dayName} ${day} ${month}</div>
            <div class="session-time">${selection.start} - ${selection.end}</div>
        </div>
    `;
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

    const btn = document.getElementById('btn-confirm');
    btn.disabled = true;
    btn.textContent = T.btnSubmitting;
    document.getElementById('loading').style.display = '';

    try {
        const resp = await fetch('/api/book-kennismaken', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                client: clientData,
                session: {
                    listing_id: selection.listing_id,
                    date: selection.date,
                    start: selection.start,
                    end: selection.end,
                    instructor: selection.instructor || '',
                    instructor_id: selection.instructor_id,
                    key: selection.key,
                },
            }),
        });

        const data = await resp.json();

        if (data.success) {
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
