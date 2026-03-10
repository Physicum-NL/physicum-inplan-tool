// Introductie Inplan Tool — Frontend wizard

const MONTHS_NL = [
    'januari', 'februari', 'maart', 'april', 'mei', 'juni',
    'juli', 'augustus', 'september', 'oktober', 'november', 'december'
];
const DAYS_NL = ['ma', 'di', 'wo', 'do', 'vr', 'za', 'zo'];

// State
let currentStep = 0;
const totalSteps = 4; // 0-2: pick sessions, 3: overview, 4: success
const selections = [null, null, null]; // Geselecteerde slots per stap
const calendarStates = [{}, {}, {}]; // { year, month } per stap

// ─── Initialisatie ────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    // Initialiseer kalenders
    for (let i = 0; i < 3; i++) {
        const now = new Date();
        calendarStates[i] = { year: now.getFullYear(), month: now.getMonth() };
        renderCalendar(i);
    }
    updateUI();
});


// ─── Navigatie ────────────────────────────────────────

function nextStep() {
    if (currentStep < 3 && !selections[currentStep]) return;
    if (currentStep < 4) {
        currentStep++;
        if (currentStep === 3) renderOverview();
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
    // Toon juiste stap
    document.querySelectorAll('.wizard-step').forEach(el => {
        el.style.display = parseInt(el.dataset.step) === currentStep ? '' : 'none';
    });

    // Update stappen-indicator
    document.querySelectorAll('.step-dot').forEach(dot => {
        const step = parseInt(dot.dataset.step);
        dot.classList.remove('active', 'completed');
        if (step === currentStep) dot.classList.add('active');
        else if (step < currentStep) dot.classList.add('completed');
    });

    document.querySelectorAll('.step-line').forEach((line, i) => {
        line.classList.toggle('completed', i < currentStep);
    });

    // Navigatie knoppen
    const btnBack = document.getElementById('btn-back');
    const btnNext = document.getElementById('btn-next');
    const navButtons = document.getElementById('nav-buttons');

    btnBack.style.display = currentStep > 0 && currentStep < 4 ? '' : 'none';

    if (currentStep >= 3) {
        navButtons.style.display = 'none';
    } else {
        navButtons.style.display = '';
        btnNext.disabled = !selections[currentStep];
    }

    // Update samenvattingen boven stap 2 en 3
    renderSummaryBefore(1);
    renderSummaryBefore(2);
}


// ─── Kalender ─────────────────────────────────────────

async function renderCalendar(stepIndex) {
    const picker = document.querySelector(`.session-picker[data-step="${stepIndex}"]`);
    const listingId = picker.dataset.listingId;
    const state = calendarStates[stepIndex];

    const monthStr = `${state.year}${String(state.month + 1).padStart(2, '0')}`;
    const monthLabel = `${MONTHS_NL[state.month]} ${state.year}`;

    // Haal beschikbare datums op
    let availableDates = [];
    try {
        const resp = await fetch(`/api/dates/${listingId}?month=${monthStr}`);
        const data = await resp.json();
        availableDates = data.dates || [];
    } catch (e) {
        console.error('Fout bij ophalen datums:', e);
    }

    // Minimale datum: vandaag, of dag na vorige sessie
    let minDate = new Date();
    minDate.setHours(0, 0, 0, 0);
    if (stepIndex > 0 && selections[stepIndex - 1]) {
        const prevDate = new Date(selections[stepIndex - 1].date);
        prevDate.setDate(prevDate.getDate() + 1);
        if (prevDate > minDate) minDate = prevDate;
    }

    // Is vorige maand navigeerbaar?
    const now = new Date();
    const canGoPrev = state.year > now.getFullYear() ||
        (state.year === now.getFullYear() && state.month > now.getMonth());

    // Render
    const firstDay = new Date(state.year, state.month, 1);
    let startDay = firstDay.getDay() - 1; // ma=0
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
                ${DAYS_NL.map(d => `<div class="day-header">${d}</div>`).join('')}
    `;

    // Lege cellen voor de eerste dag
    for (let i = 0; i < startDay; i++) {
        html += '<div class="calendar-day other-month"></div>';
    }

    // Dagen van de maand
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    for (let day = 1; day <= daysInMonth; day++) {
        const dateStr = `${state.year}-${String(state.month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
        const dateObj = new Date(state.year, state.month, day);
        const isAvailable = availableDates.includes(dateStr) && dateObj >= minDate;
        const isToday = dateObj.getTime() === today.getTime();
        const isSelected = selections[stepIndex] && selections[stepIndex].date === dateStr;
        const isBeforeMin = dateObj < minDate;

        let classes = 'calendar-day';
        if (isAvailable) classes += ' available';
        if (isToday) classes += ' today';
        if (isSelected) classes += ' selected';
        if (isBeforeMin) classes += ' disabled-date';

        const onClick = isAvailable ? `onclick="selectDate(${stepIndex}, '${dateStr}')"` : '';
        html += `<div class="${classes}" ${onClick}>${day}</div>`;
    }

    html += '</div></div>';
    html += '<div class="slots-container" id="slots-' + stepIndex + '"></div>';

    picker.innerHTML = html;

    // Als er al een datum geselecteerd is, laad de slots
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
    // Reset slot selectie als datum verandert
    if (selections[stepIndex] && selections[stepIndex].date !== dateStr) {
        selections[stepIndex] = null;
        updateUI();
    }

    // Markeer geselecteerde dag
    const picker = document.querySelector(`.session-picker[data-step="${stepIndex}"]`);
    picker.querySelectorAll('.calendar-day').forEach(el => el.classList.remove('selected'));
    // Vind de dag-cel met deze datum
    picker.querySelectorAll('.calendar-day.available').forEach(el => {
        if (el.getAttribute('onclick') && el.getAttribute('onclick').includes(dateStr)) {
            el.classList.add('selected');
        }
    });

    // Laad tijdsloten
    await loadSlots(stepIndex, dateStr);
}

async function loadSlots(stepIndex, dateStr) {
    const picker = document.querySelector(`.session-picker[data-step="${stepIndex}"]`);
    const listingId = picker.dataset.listingId;
    const container = document.getElementById(`slots-${stepIndex}`);

    container.innerHTML = '<div class="slots-loading">Beschikbare tijden laden...</div>';

    try {
        const resp = await fetch(`/api/slots/${listingId}?date=${dateStr}`);
        const data = await resp.json();
        const slots = data.slots || [];

        if (slots.length === 0) {
            container.innerHTML = '<div class="slots-empty">Geen beschikbare tijden op deze dag</div>';
            return;
        }

        // Sla slots op in een globale variabele voor onclick
        window._slotsData = window._slotsData || {};
        window._slotsData[stepIndex] = { date: dateStr, slots: slots };

        let html = '<div class="slots-title">Beschikbare tijden</div><div class="slots-grid">';
        for (let si = 0; si < slots.length; si++) {
            const slot = slots[si];
            const isSelected = selections[stepIndex] &&
                selections[stepIndex].date === dateStr &&
                selections[stepIndex].start === slot.start;
            const selectedClass = isSelected ? ' selected' : '';

            html += `
                <button class="slot-btn${selectedClass}"
                        onclick="pickSlot(${stepIndex}, ${si})"
                >
                    ${slot.start}
                    ${slot.instructor ? `<span class="slot-instructor">${slot.instructor}</span>` : ''}
                </button>
            `;
        }
        html += '</div>';
        container.innerHTML = html;

    } catch (e) {
        console.error('Fout bij ophalen slots:', e);
        container.innerHTML = '<div class="slots-empty">Fout bij laden. Probeer opnieuw.</div>';
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

    // Update slot knoppen
    const container = document.getElementById(`slots-${stepIndex}`);
    container.querySelectorAll('.slot-btn').forEach(btn => {
        btn.classList.remove('selected');
        if (btn.textContent.trim().startsWith(slot.start)) {
            btn.classList.add('selected');
        }
    });

    // Enable "Verder" knop
    updateUI();

    // Als volgende stappen al geselecteerd zijn en de datum is veranderd, reset ze
    for (let i = stepIndex + 1; i < 3; i++) {
        if (selections[i]) {
            const prevDate = new Date(selections[stepIndex].date);
            const nextDate = new Date(selections[i].date);
            if (nextDate <= prevDate) {
                selections[i] = null;
                renderCalendar(i);
            }
        }
    }
}


// ─── Samenvattingen ───────────────────────────────────

function renderSummaryBefore(stepIndex) {
    const container = document.getElementById(`summary-before-${stepIndex}`);
    if (!container) return;

    let html = '';
    for (let i = 0; i < stepIndex; i++) {
        if (selections[i]) {
            html += renderSummaryCard(i);
        }
    }
    container.innerHTML = html;
}

function renderSummaryCard(stepIndex) {
    const sel = selections[stepIndex];
    if (!sel) return '';

    const dateObj = new Date(sel.date);
    const dayName = ['zondag', 'maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag'][dateObj.getDay()];
    const day = dateObj.getDate();
    const month = MONTHS_NL[dateObj.getMonth()];

    return `
        <div class="summary-card">
            <div class="check">&#10003;</div>
            <div class="info">
                <div class="title">${stepIndex + 1}e training</div>
                <div class="detail">${dayName} ${day} ${month} om ${sel.start}${sel.instructor ? ' — ' + sel.instructor : ''}</div>
            </div>
        </div>
    `;
}


// ─── Overzicht ────────────────────────────────────────

function renderOverview() {
    const container = document.getElementById('overview-cards');
    let html = '';

    for (let i = 0; i < 3; i++) {
        const sel = selections[i];
        if (!sel) continue;

        const dateObj = new Date(sel.date);
        const dayName = ['zondag', 'maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag'][dateObj.getDay()];
        const day = dateObj.getDate();
        const month = MONTHS_NL[dateObj.getMonth()];

        html += `
            <div class="overview-card">
                <div class="session-num">${i + 1}e introductie training</div>
                <div class="session-date">${dayName} ${day} ${month}</div>
                <div class="session-time">${sel.start} - ${sel.end}</div>
                ${sel.instructor ? `<div class="session-trainer">${sel.instructor}</div>` : ''}
            </div>
        `;
    }

    container.innerHTML = html;
}


// ─── Client data uit URL parameters ──────────────────

function getClientData() {
    const params = new URLSearchParams(window.location.search);
    const fullName = (params.get('name') || '').trim();
    const nameParts = fullName.split(' ');
    return {
        first_name: nameParts[0] || '',
        last_name: nameParts.slice(1).join(' ') || '',
        email: params.get('email') || '',
        phone: params.get('phone') || '',
    };
}


// ─── Booking ──────────────────────────────────────────

async function submitBooking() {
    const clientData = getClientData();
    if (!clientData.email) {
        alert('E-mailadres ontbreekt. Zorg dat je via de juiste link op deze pagina bent gekomen.');
        return;
    }

    const btn = document.getElementById('btn-confirm');
    btn.disabled = true;
    btn.textContent = 'Bezig met aanvragen...';
    document.getElementById('loading').style.display = '';

    try {
        const resp = await fetch('/api/book', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                client: clientData,
                sessions: selections.map(s => ({
                    listing_id: s.listing_id,
                    date: s.date,
                    start: s.start,
                    key: s.key,
                })),
            }),
        });

        const data = await resp.json();

        if (data.success) {
            // Meta Pixel conversie event
            if (typeof fbq !== 'undefined') {
                fbq('track', 'Schedule', {
                    content_name: 'Introductie Pakket',
                    content_category: 'Personal Training',
                    value: 99.00,
                    currency: 'EUR',
                });
            }

            // Ga naar succes-stap
            currentStep = 4;
            renderSuccess();
            updateUI();
        } else {
            alert('Er ging iets mis: ' + (data.error || 'Probeer opnieuw'));
            btn.disabled = false;
            btn.textContent = 'Trainingen bevestigen';
        }

    } catch (e) {
        console.error('Booking fout:', e);
        alert('Er ging iets mis. Controleer je internetverbinding en probeer opnieuw.');
        btn.disabled = false;
        btn.textContent = 'Trainingen bevestigen';
    } finally {
        document.getElementById('loading').style.display = 'none';
    }
}

function renderSuccess() {
    const container = document.getElementById('success-details');
    let html = '<div style="margin-top: 24px">';
    for (let i = 0; i < 3; i++) {
        html += renderSummaryCard(i);
    }
    html += '</div>';
    container.innerHTML = html;
}
