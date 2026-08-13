/* Web Archive modal: Wayback snapshot lookup and rendering. */

import { escapeHtml } from "./shared.js";

let archiveResults = [];
let archiveIsTruncated = false;

/**
 * Toggle archive modal visibility
 */
export function toggleArchiveModal() {
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
    parked: "parked / for sale",
    porn: "adult",
    casino: "casino",
    pharma: "pharma",
    betting: "betting",
    ideographs: "ideographs",
    chinese: "chinese spam",
    doorway: "doorway"
};



function formatSpamLabels(value) {
    if (!value) return "";
    const list = Array.isArray(value) ? value : [value];
    const labels = list.map((key) => SPAM_LABELS[key] || key).filter(Boolean);
    return labels.join(", ");
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
    const groqTopic  = item.groq_topic  || "";
    const groqReason = item.groq_reason || "";
    const spamHtml = spamLabels ? `<div class="archive-spam">SPAM: ${escapeHtml(spamLabels)}</div>` : "";
    let groqHtml = "";
    if (groqTopic && groqTopic !== "unknown") {
        const reasonAttr = groqReason ? ` title="${escapeHtml(groqReason)}"` : "";
        if (groqTopic === "legit") {
            groqHtml = `<div class="archive-groq archive-groq--ok"${reasonAttr}>Groq: clean</div>`;
        } else {
            groqHtml = `<div class="archive-groq archive-groq--bad"${reasonAttr}>Groq: ${escapeHtml(groqTopic)}</div>`;
        }
    }
    const topicHtml = item.topic_shift ? `<div class="archive-topic">Topic shift</div>` : "";
    const languageHtml = item.language_shift ? `<div class="archive-topic">Language shift</div>` : "";
    const cloakingHtml = item.cloaking ? `<div class="archive-cloaking">Cloaking</div>` : "";
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
    if (redirectHtml || spamHtml || groqHtml || topicHtml || languageHtml || cloakingHtml) {
        redirectCell = `<td class="archive-redirect-cell">${redirectHtml}${spamHtml}${groqHtml}${topicHtml}${languageHtml}${cloakingHtml}</td>`;
    } else {
        redirectCell = `<td></td>`;
    }

    return `
        <tr>
            <td>${escapeHtml(item.date)}</td>
            <td><span class="status-pill ${statusClass}"${statusTitle}>${escapeHtml(statusLabel)}</span></td>
            <td><a href="${encodeURI(item.link)}" target="_blank" rel="noopener noreferrer" class="wayback-link">Open snapshot →</a></td>
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

export async function applyArchiveFilters() {
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

}

/**
 * Fetch and display Wayback Machine data
 */
export async function fetchWaybackData() {
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
export function handleArchiveKeyPress(event) {
    if (event.key === "Enter") {
        fetchWaybackData();
    }
}
