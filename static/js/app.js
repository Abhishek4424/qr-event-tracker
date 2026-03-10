/* ═══════════════════════════════════════════════════════
   QR Event Tracker — Frontend Application
   ═══════════════════════════════════════════════════════ */

// ─── State ───
let currentEventId = null;
let currentQrId = null;
let liveInterval = null;
let selectedColor = "#0F2B3C";

// ─── Init ───
document.addEventListener("DOMContentLoaded", () => {
    initTabs();
    initColorPicker();
    loadEvents();
    updateTaggedPreview();

    // Bind input listeners for live URL preview
    ["qr-source", "qr-medium", "qr-campaign", "qr-content"].forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            element.addEventListener("input", updateTaggedPreview);
        }
    });
    
    // Make functions globally accessible for onclick handlers
    window.showNewEventModal = showNewEventModal;
    window.closeModal = closeModal;
    window.createNewEvent = createNewEvent;
    window.generateQR = generateQR;
    window.downloadQR = downloadQR;
    window.exportData = exportData;
    window.loadLiveFeed = loadLiveFeed;
});

// Global error handler
window.addEventListener('error', (e) => {
    console.error('Global error:', e.error);
    showToast('An error occurred: ' + e.error.message, true);
});

// ─── Tab Navigation ───
function initTabs() {
    document.querySelectorAll(".tab").forEach(tab => {
        tab.addEventListener("click", () => {
            document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
            document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
            tab.classList.add("active");
            const target = tab.dataset.tab;
            document.getElementById(`tab-${target}`).classList.add("active");

            // Load data for the tab
            if (currentEventId) {
                if (target === "analytics") loadAnalytics();
                if (target === "persona") loadPersonas();
                if (target === "live") loadLiveFeed();
            }

            // Manage live feed interval
            if (target === "live") {
                if (liveInterval) clearInterval(liveInterval);
                liveInterval = setInterval(loadLiveFeed, 10000);
            } else {
                if (liveInterval) { clearInterval(liveInterval); liveInterval = null; }
            }
        });
    });
}

// ─── Color Picker ───
function initColorPicker() {
    document.querySelectorAll(".color-swatch").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".color-swatch").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            selectedColor = btn.dataset.color;
        });
    });
}

// ─── API Helper ───
async function api(url, options = {}) {
    try {
        const res = await fetch(url, {
            headers: { "Content-Type": "application/json", ...options.headers },
            ...options,
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ error: "Request failed" }));
            throw new Error(err.error || `HTTP ${res.status}`);
        }
        // Check if it's a download response
        const ct = res.headers.get("content-type");
        if (ct && (ct.includes("csv") || ct.includes("json") && options.download)) {
            return res.blob();
        }
        return res.json();
    } catch (e) {
        console.error("API Error:", e);
        showToast(`Error: ${e.message}`, true);
        throw e;
    }
}

// ─── Toast Notifications ───
function showToast(message, isError = false) {
    let toast = document.querySelector(".toast");
    if (!toast) {
        toast = document.createElement("div");
        toast.className = "toast";
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.style.background = isError ? "#dc2626" : "#0F2B3C";
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 3000);
}

// ─── Events ───
async function loadEvents() {
    try {
        const events = await api("/api/events");
        const select = document.getElementById("event-select");
        
        // Store current selection
        const currentSelection = select.value;
        
        // Clear and rebuild options
        select.innerHTML = '<option value="">— Select Event —</option>';
        events.forEach(e => {
            const option = document.createElement("option");
            option.value = e.id;
            option.textContent = `${e.name} (${e.login_url})`;
            select.appendChild(option);
        });
        
        // Restore selection if it still exists
        if (currentSelection && events.some(e => e.id == currentSelection)) {
            select.value = currentSelection;
        }
        
        // Remove old listener before adding new one
        select.removeEventListener("change", handleEventChange);
        select.addEventListener("change", handleEventChange);
    } catch (err) {
        console.error("Failed to load events:", err);
    }
}

function handleEventChange() {
    const select = document.getElementById("event-select");
    currentEventId = select.value || null;
    if (currentEventId) {
        loadExistingQRs();
        // Load data for active tab
        const activeTab = document.querySelector(".tab.active");
        if (activeTab) {
            const tab = activeTab.dataset.tab;
            if (tab === "analytics") loadAnalytics();
            if (tab === "persona") loadPersonas();
            if (tab === "live") loadLiveFeed();
        }
    }
    updateTaggedPreview();
}

function showNewEventModal() {
    document.getElementById("modal-overlay").classList.add("show");
    document.getElementById("new-event-name").focus();
}
function closeModal() {
    document.getElementById("modal-overlay").classList.remove("show");
}

async function createNewEvent() {
    console.log("createNewEvent function called");
    
    const name = document.getElementById("new-event-name").value.trim();
    const url = document.getElementById("new-event-url").value.trim();
    const desc = document.getElementById("new-event-desc").value.trim();
    
    console.log("Form values:", { name, url, desc });
    
    if (!name || !url) { 
        showToast("Name and URL are required", true); 
        return; 
    }

    try {
        console.log("Sending event creation request...");
        const result = await api("/api/events", {
            method: "POST",
            body: JSON.stringify({ name, login_url: url, description: desc }),
        });
        
        console.log("Event created:", result);
        
        closeModal();
        showToast("Event created successfully!");
        
        // Clear form fields
        document.getElementById("new-event-name").value = "";
        document.getElementById("new-event-url").value = "";
        document.getElementById("new-event-desc").value = "";
        
        // Reload events and select the new one
        await loadEvents();
        const select = document.getElementById("event-select");
        if (result.id) {
            select.value = result.id;
            select.dispatchEvent(new Event("change"));
        }
    } catch (err) {
        console.error("Failed to create event:", err);
        showToast("Failed to create event: " + err.message, true);
    }
}

// ─── Tagged URL Preview ───
function updateTaggedPreview() {
    const select = document.getElementById("event-select");
    const event = select.options[select.selectedIndex];
    if (!currentEventId || !event) {
        document.getElementById("tagged-url-preview").style.display = "none";
        return;
    }

    // Extract login URL from option text
    const match = event.textContent.match(/\((.+)\)$/);
    const baseUrl = match ? match[1] : "";
    if (!baseUrl) { document.getElementById("tagged-url-preview").style.display = "none"; return; }

    const params = new URLSearchParams();
    const src = document.getElementById("qr-source").value;
    const med = document.getElementById("qr-medium").value;
    const camp = document.getElementById("qr-campaign").value;
    const cont = document.getElementById("qr-content").value;

    if (src) params.set("utm_source", src);
    if (med) params.set("utm_medium", med);
    if (camp) params.set("utm_campaign", camp);
    if (cont) params.set("utm_content", cont);

    const sep = baseUrl.includes("?") ? "&" : "?";
    const tagged = `${baseUrl}${sep}${params.toString()}`;

    document.getElementById("tagged-url-preview").style.display = "block";
    document.getElementById("preview-url-text").textContent = tagged;
}

// ─── Generate QR ───
async function generateQR() {
    if (!currentEventId) { showToast("Please select an event first", true); return; }

    const label = document.getElementById("qr-label").value.trim();
    const campaign = document.getElementById("qr-campaign").value.trim();
    const content = document.getElementById("qr-content").value.trim();

    if (!label || !campaign || !content) {
        showToast("Label, Campaign, and Content are required", true);
        return;
    }

    const data = {
        event_id: parseInt(currentEventId),
        label,
        utm_source: document.getElementById("qr-source").value.trim() || "qrcode",
        utm_medium: document.getElementById("qr-medium").value.trim() || "event_print",
        utm_campaign: campaign,
        utm_content: content,
        qr_color: selectedColor,
        error_correction: document.getElementById("qr-ec").value,
    };

    const result = await api("/api/qr", { method: "POST", body: JSON.stringify(data) });
    currentQrId = result.id;

    // Show preview
    const previewBox = document.getElementById("preview-box");
    previewBox.innerHTML = `<img src="/api/qr/${result.id}/preview?t=${Date.now()}" alt="QR Code">`;
    previewBox.classList.add("has-qr");

    // Show download buttons
    document.getElementById("download-btns").style.display = "flex";

    // Show meta
    const meta = document.getElementById("qr-meta");
    meta.style.display = "block";
    meta.textContent = label;

    // Show tags
    const tags = document.getElementById("qr-tags");
    tags.style.display = "flex";
    tags.innerHTML = [
        data.utm_source && `<span class="qr-tag">src:${data.utm_source}</span>`,
        data.utm_medium && `<span class="qr-tag">med:${data.utm_medium}</span>`,
        data.utm_campaign && `<span class="qr-tag">camp:${data.utm_campaign}</span>`,
        data.utm_content && `<span class="qr-tag">cnt:${data.utm_content}</span>`,
    ].filter(Boolean).join("");

    // Show scan URL
    const scanUrl = document.getElementById("qr-scan-url");
    scanUrl.style.display = "block";
    document.getElementById("scan-url-text").textContent = `${location.origin}/s/${result.short_code}`;

    showToast("QR code generated!");
    loadExistingQRs();
}

function downloadQR(fmt) {
    if (!currentQrId) return;
    window.open(`/api/qr/${currentQrId}/download/${fmt}?size=12`, "_blank");
}

// ─── Existing QR Codes ───
async function loadExistingQRs() {
    if (!currentEventId) return;
    const qrs = await api(`/api/qr?event_id=${currentEventId}`);
    const container = document.getElementById("existing-qrs-table");

    if (qrs.length === 0) {
        container.innerHTML = `<div class="empty-state"><p>No QR codes yet for this event. Generate one above!</p></div>`;
        return;
    }

    let html = `<table><thead><tr>
        <th>Label</th><th>utm_content</th><th>Short Code</th><th>Color</th><th>Created</th><th>Actions</th>
    </tr></thead><tbody>`;

    qrs.forEach(q => {
        html += `<tr>
            <td style="font-weight:600;color:#0F2B3C">${esc(q.label)}</td>
            <td><code>${esc(q.utm_content)}</code></td>
            <td><code>${esc(q.short_code)}</code></td>
            <td><span style="display:inline-block;width:16px;height:16px;border-radius:4px;background:${q.qr_color};vertical-align:middle"></span></td>
            <td style="font-size:12px;color:#8896a6">${new Date(q.created_at).toLocaleDateString()}</td>
            <td>
                <button class="btn btn-sm btn-outline" onclick="window.open('/api/qr/${q.id}/download/jpeg?size=12')">⬇ JPEG</button>
                <button class="btn btn-sm btn-danger" onclick="deleteQR(${q.id})">✕</button>
            </td>
        </tr>`;
    });

    html += "</tbody></table>";
    container.innerHTML = html;
}

async function deleteQR(id) {
    if (!confirm("Delete this QR code?")) return;
    await api(`/api/qr/${id}`, { method: "DELETE" });
    showToast("QR code deleted");
    loadExistingQRs();
}

// ─── Analytics Tab ───
async function loadAnalytics() {
    if (!currentEventId) return;

    // Overview stats
    const overview = await api(`/api/analytics/overview?event_id=${currentEventId}`);
    const statsGrid = document.getElementById("stats-grid");
    statsGrid.innerHTML = [
        statCard("scan", "Total Scans", overview.total_scans, "All placements", "#10b981"),
        statCard("eye", "Unique Scanners", overview.unique_scanners, `${overview.repeat_rate}% repeat rate`, "#2563eb"),
        statCard("clock", "Peak Hour", overview.peak_hour || "—", `${overview.peak_hour_scans || 0} scans`, "#d97706"),
        statCard("phone", "Top OS", overview.top_os?.name || "—", overview.top_os ? `${overview.top_os.count} scans` : "", "#7c3aed"),
        statCard("map", "Top City", overview.top_city?.name || "—", overview.top_city ? `${overview.top_city.count} scans` : "", "#dc2626"),
    ].join("");

    // Timeline
    const timeline = await api(`/api/analytics/timeline?event_id=${currentEventId}&granularity=hourly`);
    renderBarChart("timeline-chart", timeline);

    // Placements
    const placements = await api(`/api/analytics/placements?event_id=${currentEventId}`);
    renderPlacementTable("placement-table", placements, overview.total_scans);
}

function statCard(icon, label, value, sub, color) {
    const icons = {
        scan: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2"><path d="M3 7V5a2 2 0 012-2h2"/><path d="M17 3h2a2 2 0 012 2v2"/><path d="M21 17v2a2 2 0 01-2 2h-2"/><path d="M7 21H5a2 2 0 01-2-2v-2"/><line x1="7" y1="12" x2="17" y2="12"/></svg>`,
        eye: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`,
        clock: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
        phone: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2"><rect x="5" y="2" width="14" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>`,
        map: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z"/><circle cx="12" cy="10" r="3"/></svg>`,
    };
    return `<div class="stat-card">
        <div class="stat-icon" style="background:${color}15;color:${color}">${icons[icon]}</div>
        <div>
            <div class="stat-label">${label}</div>
            <div class="stat-value">${value}</div>
            <div class="stat-sub">${sub}</div>
        </div>
    </div>`;
}

function renderBarChart(containerId, data) {
    const container = document.getElementById(containerId);
    if (!data || data.length === 0) {
        container.innerHTML = `<div class="empty-state"><p>No scan data yet</p></div>`;
        return;
    }
    const maxVal = Math.max(...data.map(d => d.total), 1);
    let html = '<div class="bar-chart">';
    data.forEach(d => {
        const h = (d.total / maxVal) * 145;
        const intensity = d.total / maxVal;
        const bg = intensity > 0.7 ? "linear-gradient(180deg, #059669, #10b981)"
            : intensity > 0.4 ? "linear-gradient(180deg, #1a4a5e, #0F2B3C)"
            : "linear-gradient(180deg, #94a3b8, #cbd5e1)";
        html += `<div class="bar-col">
            <span class="bar-value">${d.total}</span>
            <div class="bar" style="height:${h}px;background:${bg}"></div>
            <span class="bar-label">${d.display}</span>
        </div>`;
    });
    html += '</div>';
    container.innerHTML = html;
}

function renderPlacementTable(containerId, data, total) {
    const container = document.getElementById(containerId);
    if (!data || data.length === 0) {
        container.innerHTML = `<div class="empty-state"><p>No placement data yet</p></div>`;
        return;
    }
    let html = `<table><thead><tr>
        <th>Placement</th><th>utm_content</th><th>Total Scans</th><th>Unique</th><th>Repeat %</th><th>Peak Hour</th><th>Share</th>
    </tr></thead><tbody>`;

    data.forEach(p => {
        const repeatPct = p.total_scans > 0 ? (((p.total_scans - p.unique_scanners) / p.total_scans) * 100).toFixed(0) : 0;
        const share = total > 0 ? ((p.total_scans / total) * 100).toFixed(0) : 0;
        html += `<tr>
            <td style="font-weight:600;color:#0F2B3C">${esc(p.label)}</td>
            <td><code>${esc(p.utm_content)}</code></td>
            <td style="font-weight:700">${p.total_scans}</td>
            <td style="font-weight:600;color:#2563eb">${p.unique_scanners}</td>
            <td>${repeatPct}%</td>
            <td style="color:#d97706;font-weight:600">${p.peak_hour || "—"}</td>
            <td><div class="progress-bar">
                <div class="progress-track"><div class="progress-fill" style="width:${share}%"></div></div>
                <span class="progress-text">${share}%</span>
            </div></td>
        </tr>`;
    });

    html += "</tbody></table>";
    container.innerHTML = html;
}

// ─── Persona Tab ───
async function loadPersonas() {
    if (!currentEventId) return;
    const data = await api(`/api/analytics/personas?event_id=${currentEventId}`);
    const overview = await api(`/api/analytics/overview?event_id=${currentEventId}`);

    const grid = document.getElementById("persona-grid");
    const total = overview.total_scans || 1;

    // OS breakdown
    let osHtml = `<div class="card"><div class="card-header"><h3>Device OS</h3></div>`;
    if (data.os && data.os.length > 0) {
        data.os.forEach(d => {
            const pct = ((d.count / total) * 100).toFixed(0);
            osHtml += `<div class="persona-bar">
                <div class="persona-bar-header">
                    <span class="persona-bar-name">${esc(d.name)}</span>
                    <span class="persona-bar-pct">${pct}%</span>
                </div>
                <div class="persona-bar-track"><div class="persona-bar-fill" style="width:${pct}%"></div></div>
            </div>`;
        });
    } else {
        osHtml += `<div class="empty-state"><p>No data yet</p></div>`;
    }
    osHtml += `</div>`;

    // Browser breakdown
    let browserHtml = `<div class="card"><div class="card-header"><h3>Browser</h3></div>`;
    if (data.browsers && data.browsers.length > 0) {
        data.browsers.forEach(d => {
            const pct = ((d.count / total) * 100).toFixed(0);
            browserHtml += `<div class="persona-bar">
                <div class="persona-bar-header">
                    <span class="persona-bar-name">${esc(d.name)}</span>
                    <span class="persona-bar-pct">${pct}%</span>
                </div>
                <div class="persona-bar-track"><div class="persona-bar-fill" style="width:${pct}%"></div></div>
            </div>`;
        });
    } else {
        browserHtml += `<div class="empty-state"><p>No data yet</p></div>`;
    }
    browserHtml += `</div>`;

    // Cities
    let cityHtml = `<div class="card"><div class="card-header"><h3>Top Cities</h3></div>`;
    if (data.cities && data.cities.length > 0) {
        data.cities.forEach((c, i) => {
            cityHtml += `<div class="city-row">
                <span class="city-rank ${i === 0 ? 'top' : ''}">${i + 1}</span>
                <span class="city-name">${esc(c.name)}</span>
                <span class="city-count">${c.count}</span>
                <span class="city-unit">scans</span>
            </div>`;
        });
    } else {
        cityHtml += `<div class="empty-state"><p>No location data yet</p></div>`;
    }
    cityHtml += `</div>`;

    grid.innerHTML = osHtml + browserHtml + cityHtml;

    // Persona summary cards
    const cards = document.getElementById("persona-cards");
    const topOs = data.os?.[0];
    const topCity = data.cities?.[0];
    const secondCity = data.cities?.[1];

    cards.innerHTML = [
        personaCard("Primary Device", topOs ? topOs.name : "—",
            topOs ? `${((topOs.count / total) * 100).toFixed(0)}% of all scans` : "No data", "#10b981"),
        personaCard("Top Region", topCity ? topCity.name : "—",
            topCity && secondCity ? `${topCity.name} + ${secondCity.name} = ${((topCity.count + secondCity.count) / total * 100).toFixed(0)}% of scans` : "No data", "#2563eb"),
        personaCard("Peak Activity", overview.peak_hour || "—",
            `${overview.peak_hour_scans || 0} scans in peak hour`, "#d97706"),
        personaCard("Repeat Behavior", `${overview.repeat_rate}% Repeat`,
            `${overview.repeat_scans} repeat scans out of ${overview.total_scans} total`, "#7c3aed"),
    ].join("");
}

function personaCard(title, value, detail, color) {
    return `<div class="persona-card" style="border-color:${color}20;background:${color}06">
        <div class="persona-card-title" style="color:${color}">${title}</div>
        <div class="persona-card-value">${value}</div>
        <div class="persona-card-detail">${detail}</div>
    </div>`;
}

// ─── Live Feed ───
async function loadLiveFeed() {
    if (!currentEventId) return;
    const data = await api(`/api/analytics/live?event_id=${currentEventId}&limit=50`);

    document.getElementById("live-timestamp").textContent = `Last updated: ${new Date().toLocaleTimeString()}`;

    const container = document.getElementById("live-feed-table");
    if (!data || data.length === 0) {
        container.innerHTML = `<div class="empty-state"><p>No scans recorded yet. Share your QR codes!</p></div>`;
        return;
    }

    let html = `<table><thead><tr>
        <th>Time</th><th>Device</th><th>OS</th><th>Browser</th><th>City</th><th>Placement</th><th>Type</th>
    </tr></thead><tbody>`;

    data.forEach((s, i) => {
        const time = new Date(s.scanned_at).toLocaleTimeString();
        const badge = s.is_bot ? "bot" : (s.is_unique ? "new" : "repeat");
        const badgeText = s.is_bot ? "BOT" : (s.is_unique ? "NEW" : "REPEAT");
        html += `<tr class="${i === 0 ? 'latest-row' : ''}">
            <td style="font-weight:600;color:#0F2B3C;font-family:'DM Mono',monospace;font-size:12px">${time}</td>
            <td>${esc(s.device_brand || s.device_type)}</td>
            <td><code>${esc(s.os_name)}${s.os_version ? ' ' + esc(s.os_version) : ''}</code></td>
            <td style="font-size:12px">${esc(s.browser_name)}</td>
            <td style="font-weight:500">${esc(s.city || '—')}</td>
            <td><code style="background:#eff6ff;color:#1e40af">${esc(s.utm_content || s.placement_label)}</code></td>
            <td><span class="scan-badge ${badge}">${badgeText}</span></td>
        </tr>`;
    });

    html += "</tbody></table>";
    container.innerHTML = html;
}

// ─── Export ───
function exportData(format) {
    if (!currentEventId) { showToast("Select an event first", true); return; }
    window.open(`/api/analytics/export?event_id=${currentEventId}&format=${format}`, "_blank");
}

// ─── Utility ───
function esc(str) {
    if (!str) return "";
    const el = document.createElement("span");
    el.textContent = str;
    return el.innerHTML;
}
