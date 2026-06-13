/* DNS Checker Frontend JavaScript */

let pollInterval = null;
let pingInterval = null;
let isChecking = false;
let isStopping = false;
let browserSessionId = null;
let disconnectSent = false;
let archiveResults = [];
let archiveIsTruncated = false;

function ensureBrowserSessionId() {
    if (browserSessionId) {
        return browserSessionId;
    }

    if (window.crypto && typeof window.crypto.randomUUID === "function") {
        browserSessionId = window.crypto.randomUUID();
    } else {
        browserSessionId = `browser-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }

    return browserSessionId;
}

/**
 * Ping server to indicate browser is active
 */
async function pingServer() {
    disconnectSent = false;
    const sessionId = ensureBrowserSessionId();
    try {
        await fetch(`/api/ping?session=${encodeURIComponent(sessionId)}`, {
            method: "POST",
            cache: "no-cache",
            keepalive: true
        });
    } catch (e) {}
}

function disconnectServer() {
    if (disconnectSent) {
        return;
    }

    disconnectSent = true;

    if (pingInterval) {
        clearInterval(pingInterval);
        pingInterval = null;
    }

    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }

    const sessionId = ensureBrowserSessionId();
    const url = `/api/browser-disconnect?session=${encodeURIComponent(sessionId)}`;

    try {
        if (navigator.sendBeacon) {
            navigator.sendBeacon(url);
            return;
        }
    } catch (e) {}

    try {
        fetch(url, { method: "POST", keepalive: true });
    } catch (e) {}
}

/**
 * Start domain checking process
 */
async function startCheck() {
    if (isChecking) {
        return;
    }

    const domains = document.getElementById("domainsInput").value.trim();
    let threads = parseInt(document.getElementById("threadsInput").value);
    // if the user cleared the field or entered something invalid, fall back to 32
    if (isNaN(threads) || threads < 1) {
        threads = 32;
        document.getElementById("threadsInput").value = threads;
    }
    const rdapRecheckErrors = document.getElementById("rdapErrorsToggle").checked;

    if (!domains) {
        alert("Please enter domains/labels");
        return;
    }

    isChecking = true;
    isStopping = false;
    document.getElementById("startBtn").style.display = "none";
    document.getElementById("stopBtn").style.display = "block";
    document.getElementById("stopBtn").disabled = false;
    document.getElementById("stopBtn").textContent = "Stop";
    document.getElementById("progressSection").classList.add("active");
    document.getElementById("resultsSection").classList.remove("active");
    // reset any previous RDAP error info
    const errInfoEl = document.getElementById("rdapErrorInfo");
    if (errInfoEl) errInfoEl.textContent = "";

    try {
        const resp = await fetch("/api/check", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                domains,
                threads,
                rdap_recheck_errors: rdapRecheckErrors
            })
        });

        if (!resp.ok) {
            const err = await resp.json();
            alert("Error: " + (err.error || "Unknown"));
            finishCheckUI(false);
            return;
        }

        // Start polling status
        pollInterval = setInterval(updateStatus, 200);
    } catch (e) {
        alert("Error: " + e.message);
        finishCheckUI(false);
    }
}

/**
 * Reset check controls after the run finishes or fails.
 */
function finishCheckUI(showResultsSection) {
    isChecking = false;
    isStopping = false;
    document.getElementById("startBtn").style.display = "block";
    document.getElementById("stopBtn").style.display = "none";
    document.getElementById("stopBtn").disabled = false;
    document.getElementById("stopBtn").textContent = "Stop";
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
    if (!showResultsSection) {
        document.getElementById("progressSection").classList.remove("active");
    }
    if (!showResultsSection) {
        document.getElementById("resultsSection").classList.remove("active");
    }
}

/**
 * Request server-side cancellation for the current check.
 */
async function stopCheck() {
    if (!isChecking || isStopping) {
        return;
    }

    isStopping = true;
    const stopBtn = document.getElementById("stopBtn");
    stopBtn.disabled = true;
    stopBtn.textContent = "Stopping...";

    try {
        const resp = await fetch("/api/stop", { method: "POST" });
        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.error || "Could not stop the scan");
        }
    } catch (e) {
        console.error("Stop error:", e);
        isStopping = false;
        stopBtn.disabled = false;
        stopBtn.textContent = "Stop";
        alert("Error: " + e.message);
    }
}

/**
 * Update status from server
 */
async function updateStatus() {
    try {
        const resp = await fetch("/api/status");
        const data = await resp.json();

        // Update stats
        document.getElementById("availableStat").textContent = data.available;
        document.getElementById("takenStat").textContent = data.taken;
        document.getElementById("invalidStat").textContent = data.invalid;
        document.getElementById("errorsStat").textContent = data.errors;

        // Update progress
        document.getElementById("progressLabel").textContent = data.progress_pct + "%";
        document.getElementById("progressFill").style.width = data.progress_pct + "%";
        document.getElementById("currentDomain").textContent = "Current: " + (data.current_domain || "");
        // show main progress message and, if applicable, RDAP error count
        let msg = (data.stage ? ("Stage: " + data.stage + " • ") : "") + (data.message || "");
        if (data.final_errors && data.final_errors > 0) {
            msg += ` • RDAP errors: ${data.final_errors}`;
        }
        document.getElementById("statusMsg").textContent = msg;

        // Check if done
        if (!data.running && isChecking) {
            finishCheckUI(true);
            
            // Update result cards
            document.getElementById("resultAvailable").textContent = data.available;
            document.getElementById("resultTaken").textContent = data.taken;
            document.getElementById("resultInvalid").textContent = data.invalid;
            document.getElementById("resultErrors").textContent = data.errors;
            // optionally show RDAP error count if we ended up with some
            if (data.final_errors && data.final_errors > 0) {
                const extra = document.getElementById("rdapErrorInfo");
                if (extra) extra.textContent = `RDAP errors: ${data.final_errors}`;
            }
            
            // Show results section and hide progress bar to avoid layout jump
            document.getElementById("progressSection").classList.remove("active");
            document.getElementById("resultsSection").classList.add("active");
            
            // if we ended up with zero available domains but saw errors, warn
            if (data.available === 0 && data.errors > 0) {
                const warnEl = document.getElementById("rdapErrorInfo");
                if (warnEl) {
                    warnEl.textContent =
                        `No available domains were identified; ${data.errors} lookups failed. ` +
                        `Try again later or check your network/proxy settings.`;
                    warnEl.style.color = '#f87171'; // red-ish warning
                }
            }

            console.log("Check complete! Results ready for download");
        }
    } catch (e) {
        console.error("Update error:", e);
    }
}

/**
 * Download all results
 */
function downloadAllResults() {
    const link = document.createElement("a");
    link.href = "/api/download-all";
    link.download = "checker-results.zip";
    link.click();
}

/**
 * Download results as text file
 * @param {string} type - Result type (available|taken|invalid|errors)
 */
function downloadResult(type) {
    const link = document.createElement("a");
    link.href = "/api/download/" + type;
    link.download = type + ".txt";
    link.click();
}

/**
 * Toggle archive modal visibility
 */
function toggleArchiveModal() {
    const m = document.getElementById("archiveModal");
    const visible = m.classList.toggle("active");
    if (visible) {
        restoreArchiveProxyInput();
    }
}

const ARCHIVE_PROXY_STORAGE_KEY = "archive_proxy_input";

function restoreArchiveProxyInput() {
    const input = document.getElementById("archiveProxyInput");
    if (!input) return;
    try {
        const saved = localStorage.getItem(ARCHIVE_PROXY_STORAGE_KEY) || "";
        if (!input.value.trim() && saved) {
            input.value = saved;
        }
    } catch (_e) {}
}

function getArchiveProxyInput() {
    const input = document.getElementById("archiveProxyInput");
    if (!input) return "";
    return input.value.trim();
}

function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function normalizeArchiveRedirect(str) {
    if (!str) return str;
    try {
        const m = str.match(/^https?:\/\/web\.archive\.org\/web\/\d+[a-z_]*\/(.+)$/i);
        if (m && m[1]) {
            return m[1];
        }
    } catch (_e) {}
    return str;
}

const SPAM_LABELS = {
    parked: "\u0434\u043e\u043c\u0435\u043d \u043f\u0440\u043e\u0434\u0430\u0435\u0442\u0441\u044f",
    porn: "порно",
    casino: "казино",
    pharma: "фарма",
    betting: "ставки",
    ideographs: "иероглифы",
    chinese: "китайский спам",
    doorway: "дорвеи"
};

const RISK_LABELS = {
    parked: "\u0434\u043e\u043c\u0435\u043d \u043f\u0440\u043e\u0434\u0430\u0435\u0442\u0441\u044f",
    spam_content: "спам",
    ideographs: "иероглифы",
    topic_shift: "смена тематики",
    language_shift: "смена языка",
    cloaking: "клоакинг",
    spam_links: "спам-ссылки",
    keyword_stuffing: "переспам",
    thin_content: "тонкий контент",
    link_farm: "линк-ферма",
    tracking_links: "трекинг",
    young_domain: "молодой домен",
    young_cert: "молодой сертификат",
    reputation_hit: "репутация"
};

function formatSpamLabels(value) {
    if (!value) return "";
    const list = Array.isArray(value) ? value : [value];
    const labels = list.map((key) => SPAM_LABELS[key] || key).filter(Boolean);
    return labels.join(", ");
}

// -----------------------------------------------------------------------------
// Domain list utilities
// -----------------------------------------------------------------------------

/**
 * Update the counter that shows how many domains are in the textarea.
 */
function updateDomainCount() {
    const textarea = document.getElementById("domainsInput");
    if (!textarea) return;
    const lines = textarea.value.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    const countEl = document.getElementById("domainCount");
    if (countEl) {
        countEl.textContent = lines.length;
    }
}

function parseExtraTldAllowList() {
    const input = document.getElementById("extraTldsInput");
    if (!input) return new Set();

    return new Set(
        input.value
            .split(",")
            .map(value => value.trim().toLowerCase().replace(/^\.+/, ""))
            .filter(Boolean)
    );
}

function isValidDomainLabel(label) {
    return /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label);
}

function shouldKeepDomainForTldFilter(domain, allowedExtraTlds) {
    const parts = domain.split(".").filter(Boolean);

    if (!parts.length || !parts.every(isValidDomainLabel)) {
        return false;
    }

    if (parts.length === 1) {
        return true;
    }

    if (parts.length === 2) {
        return true;
    }

    const extraTlds = parts.slice(1, -1);
    return extraTlds.every(part => allowedExtraTlds.has(part));
}

/**
 * Filter the current list to keep plain domains plus allowed multi-level TLDs.
 * Invalid lines are removed and the result is deduplicated/sorted.
 */
function filterTlds() {
    const textarea = document.getElementById("domainsInput");
    if (!textarea) return;

    const allowedExtraTlds = parseExtraTldAllowList();
    const lines = textarea.value
        .split(/\r?\n/)
        .map(l => l.trim().toLowerCase())
        .filter(Boolean);
    const filtered = lines.filter(line => shouldKeepDomainForTldFilter(line, allowedExtraTlds));
    const unique = Array.from(new Set(filtered));
    unique.sort();
    textarea.value = unique.join("\n");
    updateDomainCount();
}

function buildArchiveRowHtml(item) {
    const rawStatus = String(item.status || "");
    const isUnknownStatus = rawStatus === "-" || rawStatus.length === 0;
    const statusLabel = isUnknownStatus ? "N/A" : rawStatus;
    const statusTitle = isUnknownStatus ? ' title="No HTTP status in Wayback CDX metadata"' : "";

    let statusClass = rawStatus.startsWith("2")
        ? "s2xx"
        : rawStatus.startsWith("3")
            ? "s3xx"
            : rawStatus.startsWith("5")
                ? "s5xx"
                : isUnknownStatus
                    ? "sunknown"
                    : "s4xx";

    let redirectCell = "";
    const spamLabels = formatSpamLabels(item.spam);
    const spamHtml = spamLabels ? `<div class="archive-spam">SPAM: ${escapeHtml(spamLabels)}</div>` : "";
    const topicHtml = item.topic_shift ? `<div class="archive-topic">Смена тематики</div>` : "";
    const languageHtml = item.language_shift ? `<div class="archive-topic">Смена языка</div>` : "";
    const cloakingHtml = item.cloaking ? `<div class="archive-cloaking">Клоакинг</div>` : "";
    let redirectHtml = "";
    if (rawStatus === "301" || rawStatus === "302") {
        if (item.redirect) {
            const clean = normalizeArchiveRedirect(item.redirect);
            const txt = escapeHtml(clean);
            redirectHtml = `<div class="archive-redirect"><a href="${encodeURI(clean)}" target="_blank" rel="noopener noreferrer">${txt}</a></div>`;
        } else {
            const txt = escapeHtml("(no data)");
            redirectHtml = `<div class="archive-redirect">${txt}</div>`;
        }
    }
    if (redirectHtml || spamHtml || topicHtml || languageHtml || cloakingHtml) {
        redirectCell = `<td class="archive-redirect-cell">${redirectHtml}${spamHtml}${topicHtml}${languageHtml}${cloakingHtml}</td>`;
    } else {
        redirectCell = `<td></td>`;
    }

    return `
        <tr>
            <td style="padding:10px; font-family:monospace; color:#94a3b8;">${escapeHtml(item.date)}</td>
            <td style="padding:10px;"><span class="status-pill ${statusClass}"${statusTitle}>${escapeHtml(statusLabel)}</span></td>
            <td style="padding:10px;"><a href="${encodeURI(item.link)}" target="_blank" rel="noopener noreferrer" class="wayback-link">Open snapshot →</a></td>
            ${redirectCell}
        </tr>
    `;
}

async function renderArchiveRowsChunked(body, items) {
    const chunkSize = 400;
    body.innerHTML = "";
    for (let i = 0; i < items.length; i += chunkSize) {
        const chunk = items.slice(i, i + chunkSize);
        const html = chunk.map(buildArchiveRowHtml).join("");
        body.insertAdjacentHTML("beforeend", html);
        if (i + chunkSize < items.length) {
            await new Promise(resolve => setTimeout(resolve, 0));
        }
    }
}

async function applyArchiveFilters() {
    const body = document.getElementById("archiveTableBody");
    const hideNaToggle = document.getElementById("archiveHideNaToggle");
    if (!body) return;

    let items = Array.isArray(archiveResults) ? archiveResults.slice() : [];
    const hideNa = !hideNaToggle || hideNaToggle.checked;

    if (hideNa) {
        items = items.filter((item) => {
            const s = String(item.status || "");
            return s && s !== "-" && s.toUpperCase() !== "N/A";
        });
    }

    if (!archiveResults.length) {
        body.innerHTML = '<tr><td colspan="4" style="padding:14px; color:#94a3b8;">No snapshots</td></tr>';
        return;
    }

    if (!items.length) {
        body.innerHTML = '<tr><td colspan="4" style="padding:14px; color:#94a3b8;">No snapshots match the current filter</td></tr>';
        return;
    }

    await renderArchiveRowsChunked(body, items);
    if (archiveIsTruncated) {
        body.insertAdjacentHTML(
            "afterbegin",
            '<tr><td colspan="4" style="padding:10px; color:#f59e0b;">Showing max configured number of snapshots.</td></tr>'
        );
    }
}

function updateArchiveMeta(payload) {
    const rangeEl = document.getElementById("archiveRangeInfo");
    const proxyEl = document.getElementById("archiveProxyInfo");
    const totalEl = document.getElementById("archiveTotalInfo");
    const usedConnection = payload && payload.used_connection ? payload.used_connection : "";
    const usedEndpoint = payload && payload.cdx_endpoint ? payload.cdx_endpoint : "";
    const totalResults = payload && Number.isFinite(payload.total_results) ? payload.total_results : 0;
    const usedParts = [];
    if (usedConnection) usedParts.push(`Used: ${usedConnection}`);
    if (usedEndpoint) usedParts.push(`CDX: ${usedEndpoint.replace("://web.archive.org/cdx/search/cdx", "://web.archive.org/cdx")}`);
    const usedSuffix = usedParts.length ? ` | ${usedParts.join(" | ")}` : "";

    if (rangeEl && payload && payload.range) {
        rangeEl.textContent = `Range: ${payload.range.from}-${payload.range.to}`;
    }

    const proxy = payload && payload.proxy ? payload.proxy : null;
    if (proxyEl) {
        if (proxy && proxy.enabled) {
            proxyEl.textContent = `Proxy: ${proxy.current}${usedSuffix}`;
        } else {
            proxyEl.textContent = `Proxy: Direct connection${usedSuffix}`;
        }
    }

    if (totalEl) {
        totalEl.textContent = `Total snapshots: ${totalResults}`;
    }
    // show redirect probe/resolved counts if present
    const redirectEl = document.getElementById("archiveRedirectInfo");
    if (redirectEl) {
        const probed = payload && Number.isFinite(payload.redirects_probed) ? payload.redirects_probed : 0;
        const resolved = payload && Number.isFinite(payload.redirects_resolved) ? payload.redirects_resolved : 0;
        const direct = payload && Number.isFinite(payload.redirects_direct_fallback) ? payload.redirects_direct_fallback : 0;
        if (probed || resolved || direct) {
            redirectEl.textContent = `Redirects: ${resolved}/${probed}${direct?` (direct ${direct})`:``}`;
        } else {
            redirectEl.textContent = "";
        }
    }

    const riskEl = document.getElementById("archiveRiskInfo");
    if (riskEl) {
        const risk = payload && payload.risk ? payload.risk : null;
        if (risk && typeof risk.score === "number") {
            const flags = Array.isArray(risk.flags) && risk.flags.length
                ? ` | ${risk.flags.map(f => RISK_LABELS[f] || f).join(", ")}`
                : "";
            riskEl.textContent = `Risk: ${risk.not_suitable ? "Not suitable" : "OK"} (${risk.score})${flags}`;
            riskEl.className = risk.not_suitable ? "archive-risk bad" : "archive-risk ok";
        } else {
            riskEl.textContent = "";
            riskEl.className = "";
        }
    }
}

/**
 * Fetch and display Wayback Machine data
 */
async function fetchWaybackData() {
    const domain = document.getElementById("archiveSearchInput").value.trim();
    const proxy = getArchiveProxyInput();
    const body = document.getElementById("archiveTableBody");
    const totalEl = document.getElementById("archiveTotalInfo");
    if (!domain) return alert("Enter domain!");

    try {
        if (proxy) {
            localStorage.setItem(ARCHIVE_PROXY_STORAGE_KEY, proxy);
        } else {
            localStorage.removeItem(ARCHIVE_PROXY_STORAGE_KEY);
        }
    } catch (_e) {}

    body.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:20px;"><span class="spinner-inline"><span class="spinner"></span>Searching...</span></td></tr>';
    if (totalEl) totalEl.textContent = "Total snapshots: ...";
    archiveResults = [];
    archiveIsTruncated = false;

    try {
        const r = await fetch("/api/archive", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ domain: domain, proxy: proxy })
        });
        if (!r.ok) throw new Error("Archive request failed");
        const data = await r.json();
        updateArchiveMeta(data);
        body.innerHTML = "";

        if (data.fetch_error) {
            body.innerHTML = `<tr><td colspan="4" style="padding:14px; color:#f59e0b;">${escapeHtml(data.fetch_error)}</td></tr>`;
            return;
        }

        archiveResults = Array.isArray(data.results) ? data.results : [];
        archiveIsTruncated = Boolean(data.truncated);
        await applyArchiveFilters();
    } catch (e) {
        body.innerHTML = '<tr><td colspan="4">Error loading data</td></tr>';
        console.error(e);
    }
}

/**
 * Handle key press in archive search
 */
function handleArchiveKeyPress(event) {
    if (event.key === "Enter") {
        fetchWaybackData();
    }
}

const FILE_HINT_IDLE = "Drag & drop CSV/TXT files here or click to choose";
const FILE_HINT_LOADING = "Loading files...";
const DOMAIN_SPLIT_RE = /[\s,;\t|]+/;

function setDropHint(message) {
    const hint = document.getElementById("dropHint");
    if (hint) {
        hint.textContent = message;
    }
}

function isSupportedBatchFile(file) {
    const name = ((file && file.name) || "").toLowerCase();
    return name.endsWith(".csv") || name.endsWith(".txt");
}

const FILE_IMPORT_META_TOKENS = new Set([
    "available",
    "taken",
    "invalid",
    "error",
    "errors",
    "status",
    "domain",
    "domains",
    "name",
]);

function normalizeInputToken(token) {
    if (!token) return null;

    let value = String(token).trim().toLowerCase();
    if (!value) return null;

    value = value.replace(/^\uFEFF/, "");
    value = value.replace(/[。｡．]/g, ".");
    value = value.replace(/^[`"'(\[{<]+|[`)"'\]}>.,;:!?]+$/g, "");
    return value || null;
}

function normalizeDomainToken(token) {
    let value = normalizeInputToken(token);
    if (!value) return null;

    if (/^[a-z]+:\/\//i.test(value)) {
        try {
            value = new URL(value).hostname.toLowerCase();
        } catch (_e) {
            return null;
        }
    } else if (value.startsWith("//")) {
        try {
            value = new URL("http:" + value).hostname.toLowerCase();
        } catch (_e) {
            return null;
        }
    } else if (value.includes("/") || value.includes("?") || value.includes("#")) {
        try {
            value = new URL("http://" + value).hostname.toLowerCase();
        } catch (_e) {
            value = value.split(/[\/\?#]/)[0];
        }
    }

    if (value.includes("@")) {
        const parts = value.split("@");
        value = parts[parts.length - 1];
    }

    if (/[^\x00-\x7F]/.test(value)) {
        try {
            value = new URL("http://" + value).hostname.toLowerCase();
        } catch (_e) {
            return null;
        }
    }

    value = value.replace(/^\.+|\.+$/g, "");
    if (!value || !value.includes(".") || value.length > 253) return null;

    const labels = value.split(".");
    if (labels.length < 2) return null;

    for (const label of labels) {
        if (!label || label.length > 63) return null;
        if (!/^[a-z0-9-]+$/.test(label)) return null;
        if (label.startsWith("-") || label.endsWith("-")) return null;
    }

    const tld = labels[labels.length - 1];
    if (!/^[a-z]{2,63}$/.test(tld) && !/^xn--[a-z0-9-]{2,59}$/.test(tld)) return null;

    return value;
}

function normalizeLabelToken(token) {
    const value = normalizeInputToken(token);
    if (!value) return null;
    if (FILE_IMPORT_META_TOKENS.has(value)) return null;
    if (value.includes(".") || value.includes("/") || value.includes("?") || value.includes("#")) return null;
    if (value.includes("@") || /^[a-z]+:\/\//i.test(value) || value.startsWith("//")) return null;
    if (!/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(value)) return null;
    return value;
}

function extractDomainsOrLabelsFromLine(line) {
    const rawLine = String(line || "").replace(/\u0000/g, " ").trim();
    if (!rawLine) return [];

    const cells = rawLine.split(/[,\t;|]+/).map((value) => value.trim()).filter(Boolean);
    const out = [];

    cells.forEach((cell) => {
        const domain = normalizeDomainToken(cell);
        if (domain) {
            out.push(domain);
        }
    });

    if (out.length) {
        return out;
    }

    const primary = cells[0] || rawLine;
    const label = normalizeLabelToken(primary);
    if (!label) {
        return [];
    }

    const trailing = cells.slice(1).map((value) => value.trim().toLowerCase()).filter(Boolean);
    if (trailing.length && !trailing.every((value) => FILE_IMPORT_META_TOKENS.has(value))) {
        return [];
    }

    return [label];
}

function parseDomainsFromText(text) {
    if (!text) return [];

    const found = new Set();
    const normalizedText = String(text).replace(/\u0000/g, " ");

    normalizedText.split(/\r?\n/).forEach((line) => {
        extractDomainsOrLabelsFromLine(line).forEach((value) => found.add(value));
    });

    const fallbackMatches = normalizedText.match(/[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+/gi) || [];
    fallbackMatches.forEach((match) => {
        const domain = normalizeDomainToken(match);
        if (domain) found.add(domain);
    });

    return Array.from(found);
}

function appendDomainsToTextarea(newDomains) {
    const textarea = document.getElementById("domainsInput");
    if (!textarea) return;

    const existingLines = textarea.value
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);

    const existingLower = new Set(existingLines.map((line) => line.toLowerCase()));

    newDomains.forEach((domain) => {
        if (!existingLower.has(domain)) {
            existingLines.push(domain);
            existingLower.add(domain);
        }
    });

    textarea.value = existingLines.join("\n");
    updateDomainCount();
}

function readFileAsText(file) {
    if (file && typeof file.text === "function") {
        return file.text();
    }

    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result || "");
        reader.onerror = () => reject(reader.error || new Error("File read error"));
        reader.readAsText(file);
    });
}

async function loadDomainsFromFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;

    const supported = files.filter(isSupportedBatchFile);
    const skippedCount = files.length - supported.length;
    if (!supported.length) {
        alert("Only .csv and .txt files are supported");
        return;
    }

    setDropHint(FILE_HINT_LOADING);

    try {
        const texts = await Promise.all(supported.map((file) => readFileAsText(file)));
        const parsed = [];
        texts.forEach((text) => parsed.push(...parseDomainsFromText(text)));
        const uniqueDomains = Array.from(new Set(parsed));
        appendDomainsToTextarea(uniqueDomains);
        const skippedSuffix = skippedCount ? `, skipped ${skippedCount} unsupported file(s)` : "";
        setDropHint(`Loaded ${uniqueDomains.length} domains from ${supported.length} file(s)${skippedSuffix}`);
    } catch (error) {
        console.error("Failed to load files", error);
        alert("Could not read dropped files");
        setDropHint("Failed to parse files");
    } finally {
        setTimeout(() => setDropHint(FILE_HINT_IDLE), 2500);
    }
}

function handleDragOver(event) {
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
    const textarea = document.getElementById("domainsInput");
    if (textarea) textarea.classList.add("dragover");
}

function handleDragLeave(event) {
    event.preventDefault();
    event.stopPropagation();
    const textarea = document.getElementById("domainsInput");
    if (textarea) textarea.classList.remove("dragover");
}

function handleDrop(event) {
    event.preventDefault();
    event.stopPropagation();
    const textarea = document.getElementById("domainsInput");
    if (textarea) textarea.classList.remove("dragover");
    const files = event.dataTransfer ? event.dataTransfer.files : null;
    loadDomainsFromFiles(files);
}

function handleFileInputChange(event) {
    const files = event.target ? event.target.files : null;
    loadDomainsFromFiles(files);
    if (event.target) {
        event.target.value = "";
    }
}

function openDomainFilePicker() {
    const input = document.getElementById("domainsFileInput");
    if (input) {
        input.click();
    }
}

window.addEventListener("DOMContentLoaded", () => {
    const textarea = document.getElementById("domainsInput");
    const dropHint = document.getElementById("dropHint");
    const fileInput = document.getElementById("domainsFileInput");
    const filterBtn = document.getElementById("filterBtn");
    const archiveHideNaToggle = document.getElementById("archiveHideNaToggle");

    if (!textarea) return;

    // update the count whenever the textarea changes
    textarea.addEventListener("input", updateDomainCount);
    updateDomainCount();

    if (filterBtn) {
        filterBtn.addEventListener("click", filterTlds);
    }

    setDropHint(FILE_HINT_IDLE);

    const dropTargets = [textarea];
    if (dropHint) {
        dropTargets.push(dropHint);
        dropHint.addEventListener("click", openDomainFilePicker);
    }

    dropTargets.forEach((target) => {
        target.addEventListener("dragenter", handleDragOver);
        target.addEventListener("dragover", handleDragOver);
        target.addEventListener("dragleave", handleDragLeave);
        target.addEventListener("drop", handleDrop);
    });

    if (fileInput) {
        fileInput.addEventListener("change", handleFileInputChange);
    }

    if (archiveHideNaToggle) {
        archiveHideNaToggle.addEventListener("change", () => {
            void applyArchiveFilters();
        });
    }

    const archiveModal = document.getElementById("archiveModal");
    if (archiveModal) {
        archiveModal.addEventListener("click", (e) => {
            if (e.target === archiveModal) toggleArchiveModal();
        });
    }

    ensureBrowserSessionId();
    window.addEventListener("pagehide", disconnectServer);
    window.addEventListener("beforeunload", disconnectServer);
    window.addEventListener("pageshow", () => {
        disconnectSent = false;
        pingServer();
    });
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) {
            pingServer();
        }
    });

    pingInterval = setInterval(pingServer, 3000);
    pingServer();
});
