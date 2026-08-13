/* Domain Checker tab: running a scan, the domain textarea and file import. */

import { dbAutoCreateBuckets, dbFetchAndCompareScanResults } from "./domain-db.js";

let pollInterval = null;
let isChecking = false;
let isStopping = false;

/**
 * Start domain checking process
 */
export async function startCheck() {
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
    const dnsEnabled = document.getElementById("dnsEnabledToggle").checked;
    const rdapEnabled = document.getElementById("rdapEnabledToggle").checked;
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
    if (errInfoEl) { errInfoEl.textContent = ""; errInfoEl.className = ""; }

    try {
        const resp = await fetch("/api/check", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                domains,
                threads,
                dns_enabled: dnsEnabled,
                rdap_enabled: rdapEnabled,
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
export async function stopCheck() {
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
                    warnEl.classList.add("error");
                }
            }

            console.log("Check complete! Results ready for download");
            if (data.available > 0 || data.errors > 0) dbFetchAndCompareScanResults();
            const inputEl = document.getElementById("domainsInput");
            if (inputEl) {
                const allLines = inputEl.value.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
                dbAutoCreateBuckets(allLines);
            }
        }
    } catch (e) {
        console.error("Update error:", e);
    }
}

/**
 * Download all results
 */
export function downloadAllResults() {
    const link = document.createElement("a");
    link.href = "/api/download-all";
    link.download = "checker-results.zip";
    link.click();
}

/**
 * Download results as text file
 * @param {string} type - Result type (available|taken|invalid|errors)
 */
export function downloadResult(type) {
    const link = document.createElement("a");
    link.href = "/api/download/" + type;
    link.download = type + ".txt";
    link.click();
}

/**
 * Update the counter that shows how many domains are in the textarea.
 */
export function updateDomainCount() {
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
export function filterTlds() {
    const textarea = document.getElementById("domainsInput");
    if (!textarea) return;

    const allowedExtraTlds = parseExtraTldAllowList();
    const lines = textarea.value
        .split(/\r?\n/)
        .map(l => normalizeRawInputLine(l) || l.trim().toLowerCase())
        .filter(Boolean);
    const filtered = lines.filter(line => shouldKeepDomainForTldFilter(line, allowedExtraTlds));
    const unique = Array.from(new Set(filtered));
    unique.sort();
    textarea.value = unique.join("\n");
    updateDomainCount();
}

export const FILE_HINT_IDLE = "Drag & drop .csv / .txt files here, or click to choose";
const FILE_HINT_LOADING = "Loading files...";
const DOMAIN_SPLIT_RE = /[\s,;\t|]+/;

export function setDropHint(message) {
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

export function handleDragOver(event) {
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
    const textarea = document.getElementById("domainsInput");
    if (textarea) textarea.classList.add("dragover");
}

export function handleDragLeave(event) {
    event.preventDefault();
    event.stopPropagation();
    const textarea = document.getElementById("domainsInput");
    if (textarea) textarea.classList.remove("dragover");
}

export function handleDrop(event) {
    event.preventDefault();
    event.stopPropagation();
    const textarea = document.getElementById("domainsInput");
    if (textarea) textarea.classList.remove("dragover");
    const files = event.dataTransfer ? event.dataTransfer.files : null;
    loadDomainsFromFiles(files);
}

export function handleFileInputChange(event) {
    const files = event.target ? event.target.files : null;
    loadDomainsFromFiles(files);
    if (event.target) {
        event.target.value = "";
    }
}

export function openDomainFilePicker() {
    const input = document.getElementById("domainsFileInput");
    if (input) {
        input.click();
    }
}

// =============================================================================
// Input normalization helpers
// =============================================================================

function normalizeRawInputLine(raw) {
    if (!raw) return null;
    let v = raw.trim().toLowerCase();
    v = v.replace(/^https?:\/\//i, "");
    v = v.replace(/^\/\//, "");
    v = v.replace(/^www\.(?=[^.]+\.)/, "");
    v = v.split(/[/?#]/)[0].trim();
    return v || null;
}
