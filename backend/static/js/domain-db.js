/* Domain DB tab: TLD buckets kept in localStorage. */

import { escapeHtml, showToast } from "./shared.js";

const DB_STORAGE_KEY = "domainCheckerDB";
const DB_PAGE_SIZE = 50;

class DomainDB {
    constructor() {
        this._db = {};
        this._saveTimer = null;
        this._load();
    }

    _load() {
        try {
            const raw = localStorage.getItem(DB_STORAGE_KEY);
            if (raw) this._db = JSON.parse(raw);
        } catch (_e) { this._db = {}; }
    }

    _scheduleSave() {
        clearTimeout(this._saveTimer);
        this._saveTimer = setTimeout(() => this._flush(), 300);
    }

    _flush() {
        try {
            const json = JSON.stringify(this._db);
            localStorage.setItem(DB_STORAGE_KEY, json);
            if (json.length * 2 > 4.5 * 1024 * 1024) {
                showToast(
                    `DB is ${(json.length * 2 / 1024 / 1024).toFixed(1)} MB — consider exporting and clearing old buckets`,
                    "warn"
                );
            }
        } catch (_e) {
            showToast("Storage full — export and clear some buckets", "error");
        }
    }

    normalizeDomain(domain) {
        if (!domain) return null;
        let d = String(domain).trim().toLowerCase();
        d = d.replace(/^https?:\/\//i, "").replace(/^\/\//, "");
        d = d.replace(/^www\.(?=[^.]+\.)/, "");
        d = d.split(/[/?#]/)[0].replace(/\.+$/, "");
        if (!d || d.length > 253 || !d.includes(".")) return null;
        const labels = d.split(".");
        for (const l of labels) {
            if (!l || l.length > 63 || !/^[a-z0-9-]+$/.test(l) || l.startsWith("-") || l.endsWith("-")) return null;
        }
        return d;
    }

    getTlds() { return Object.keys(this._db).sort(); }
    getCount(tld) { return (this._db[tld]?.domains || []).length; }
    getTotalCount() { return Object.values(this._db).reduce((s, b) => s + (b.domains?.length || 0), 0); }

    addTld(tld) {
        tld = tld.toLowerCase().replace(/^\./, "").trim();
        if (!tld || this._db[tld]) return false;
        this._db[tld] = { domains: [], lastUpdated: new Date().toISOString(), addedAt: {} };
        this._scheduleSave();
        return true;
    }

    deleteTld(tld) {
        if (!this._db[tld]) return false;
        delete this._db[tld];
        this._scheduleSave();
        return true;
    }

    addDomains(tld, list) {
        tld = tld.toLowerCase().replace(/^\./, "").trim();
        if (!this._db[tld]) this._db[tld] = { domains: [], lastUpdated: new Date().toISOString(), addedAt: {} };
        const bucket = this._db[tld];
        const existing = new Set(bucket.domains);
        let added = 0, skipped = 0;
        const now = new Date().toISOString();
        for (const raw of list) {
            const d = this.normalizeDomain(raw);
            if (!d) { skipped++; continue; }
            if (existing.has(d)) { skipped++; continue; }
            existing.add(d);
            bucket.domains.push(d);
            bucket.addedAt[d] = now;
            added++;
        }
        if (added > 0) { bucket.lastUpdated = now; this._scheduleSave(); }
        return { added, skipped };
    }

    deleteDomain(tld, domain) {
        const b = this._db[tld];
        if (!b) return;
        b.domains = b.domains.filter(d => d !== domain);
        delete b.addedAt[domain];
        b.lastUpdated = new Date().toISOString();
        this._scheduleSave();
    }

    getDomains(tld) { return (this._db[tld]?.domains || []).slice(); }
    getLastUpdated(tld) { return this._db[tld]?.lastUpdated || null; }
    getAddedAt(tld, domain) { return this._db[tld]?.addedAt?.[domain] || null; }

    getAllDomains() {
        const all = new Set();
        for (const b of Object.values(this._db)) (b.domains || []).forEach(d => all.add(d));
        return all;
    }

    getSizeKb() {
        try { return ((localStorage.getItem(DB_STORAGE_KEY) || "").length * 2 / 1024).toFixed(1); }
        catch (_e) { return "0"; }
    }
}

const domainDB = new DomainDB();

let dbActiveTld = null;
let dbVisibleCount = DB_PAGE_SIZE;
let dbSearchQuery = "";
let dbFilteredDomains = [];
let dbDeletePendingTld = null;
let dbDeleteTimer = null;

/** Bucket deletion asks for a second click; clicking elsewhere calls this off. */
export function cancelPendingTldDelete() {
    if (!dbDeletePendingTld) return;
    clearTimeout(dbDeleteTimer);
    dbDeletePendingTld = null;
    dbDeleteTimer = null;
    renderDbSidebar();
}

function extractTldFromDomain(domain) {
    if (!domain || !domain.includes(".")) return null;
    const parts = domain.split(".");
    if (parts.length < 2) return null;
    const last = parts[parts.length - 1];
    const secondLast = parts[parts.length - 2];
    const ccParts = new Set(["co", "com", "net", "org", "gov", "edu"]);
    if (secondLast.length <= 3 && ccParts.has(secondLast)) return `${secondLast}.${last}`;
    return last;
}

export function dbAutoCreateBuckets(domains) {
    const tldSet = new Set();
    for (const d of domains) {
        const norm = domainDB.normalizeDomain(d);
        const tld = extractTldFromDomain(norm || d);
        if (tld) tldSet.add(tld.toLowerCase());
    }
    const existing = new Set(domainDB.getTlds());
    const created = [];
    for (const tld of tldSet) {
        if (!existing.has(tld)) {
            domainDB.addTld(tld);
            created.push("." + tld);
        }
    }
    if (created.length) {
        created.sort();
        showToast("Auto-created buckets: " + created.join(", "));
        updateDbTabCount();
    }
}

export function renderDbSidebar() {
    const list = document.getElementById("dbTldList");
    if (!list) return;
    const tlds = domainDB.getTlds();
    if (!tlds.length) {
        list.innerHTML = "";
        renderDbMain();
        updateDbTabCount();
        return;
    }
    list.innerHTML = tlds.map(tld => {
        const isActive = tld === dbActiveTld;
        const isPending = tld === dbDeletePendingTld;
        const cls = ["db-tld-item", isActive ? "active" : "", isPending ? "confirm-delete" : ""].filter(Boolean).join(" ");
        const deleteTitle = isPending ? "Click again to confirm delete" : "Delete bucket";
        const deleteIcon = isPending ? "✓" : "×";
        return `<div class="${cls}" data-tld="${escapeHtml(tld)}">
            <span class="db-tld-name">.${escapeHtml(tld)}</span>
            <span class="db-tld-right">
                <span class="db-tld-badge">${domainDB.getCount(tld).toLocaleString()}</span>
                <button class="db-tld-delete" title="${deleteTitle}" data-delete-tld="${escapeHtml(tld)}">${deleteIcon}</button>
            </span>
        </div>`;
    }).join("");
    updateDbSizeFooter();
    updateDbTabCount();
    renderDbMain();
}

function updateDbSizeFooter() {
    const el = document.getElementById("dbSizeInfo");
    if (el) el.textContent = `DB: ${domainDB.getSizeKb()} KB • ${domainDB.getTotalCount().toLocaleString()} total`;
}

export function updateDbTabCount() {
    const el = document.getElementById("tabDbCount");
    if (!el) return;
    const n = domainDB.getTotalCount();
    el.textContent = n > 0 ? n.toLocaleString() : "";
}

export function dbSelectTld(tld) {
    dbActiveTld = tld;
    dbSearchQuery = "";
    dbVisibleCount = DB_PAGE_SIZE;
    const si = document.getElementById("dbSearchInput");
    if (si) si.value = "";
    renderDbSidebar();
}

function renderDbMain() {
    const emptyEl = document.getElementById("dbEmptyState");
    const bucketEl = document.getElementById("dbBucketView");
    if (!emptyEl || !bucketEl) return;
    const tlds = domainDB.getTlds();
    if (!tlds.length) {
        emptyEl.style.display = "flex";
        bucketEl.style.display = "none";
        return;
    }
    if (!dbActiveTld || !tlds.includes(dbActiveTld)) dbActiveTld = tlds[0];
    emptyEl.style.display = "none";
    bucketEl.style.display = "block";
    document.getElementById("dbBucketTitle").textContent = "." + dbActiveTld;
    const count = domainDB.getCount(dbActiveTld);
    document.getElementById("dbBucketCount").textContent = count.toLocaleString() + " domains";
    const lu = domainDB.getLastUpdated(dbActiveTld);
    document.getElementById("dbBucketUpdated").textContent = lu
        ? "Updated " + new Date(lu).toLocaleDateString() : "";
    const today = new Date().toDateString();
    const domains = domainDB.getDomains(dbActiveTld);
    const addedToday = domains.filter(d => {
        const at = domainDB.getAddedAt(dbActiveTld, d);
        return at && new Date(at).toDateString() === today;
    }).length;
    document.getElementById("dbStatsBar").innerHTML =
        `<span>Total: <b>${count.toLocaleString()}</b></span>` +
        `<span>Added today: <b>${addedToday.toLocaleString()}</b></span>`;
    renderDomainList();
}

function renderDomainList() {
    const listEl = document.getElementById("dbDomainList");
    const moreEl = document.getElementById("dbLoadMore");
    if (!listEl || !dbActiveTld) return;
    const all = domainDB.getDomains(dbActiveTld);
    dbFilteredDomains = dbSearchQuery ? all.filter(d => d.includes(dbSearchQuery)) : all;
    if (!dbFilteredDomains.length) {
        const msg = dbSearchQuery
            ? "No domains match your search"
            : "Drag & drop a file or paste domains above — one per line";
        listEl.innerHTML = `<div class="db-list-empty">${msg}</div>`;
        if (moreEl) moreEl.style.display = "none";
        return;
    }
    const visible = dbFilteredDomains.slice(0, dbVisibleCount);
    const tld = dbActiveTld;
    listEl.innerHTML = visible.map(d => {
        const at = domainDB.getAddedAt(tld, d);
        const dateStr = at ? new Date(at).toLocaleDateString() : "";
        return `<div class="db-domain-item">
            <span class="db-domain-name">${escapeHtml(d)}</span>
            <span class="db-domain-date">${escapeHtml(dateStr)}</span>
            <button class="db-domain-del" title="Remove"
                data-delete-domain="${escapeHtml(d)}">✕</button>
        </div>`;
    }).join("");
    if (moreEl) {
        const remaining = dbFilteredDomains.length - dbVisibleCount;
        moreEl.style.display = remaining > 0 ? "block" : "none";
        if (remaining > 0) moreEl.querySelector("button").textContent = `Load more (${remaining.toLocaleString()} remaining)`;
    }
}

export function dbLoadMoreDomains() { dbVisibleCount += DB_PAGE_SIZE; renderDomainList(); }

export function dbSearch(query) { dbSearchQuery = query.toLowerCase().trim(); dbVisibleCount = DB_PAGE_SIZE; renderDomainList(); }

export function dbShowAddTld() {
    const form = document.getElementById("dbAddTldForm");
    if (!form) return;
    const open = !form.style.display || form.style.display === "none";
    form.style.display = open ? "block" : "none";
    if (open) { const inp = document.getElementById("dbTldInput"); if (inp) { inp.value = ""; inp.focus(); } }
}

export function dbCommitAddTld(e) {
    if (e.key !== "Enter") return;
    const inp = document.getElementById("dbTldInput");
    if (!inp) return;
    const tld = inp.value.toLowerCase().replace(/^\./, "").trim();
    if (!tld) return;
    if (!domainDB.addTld(tld)) { showToast(`.${tld} already exists`, "warn"); return; }
    dbActiveTld = tld;
    document.getElementById("dbAddTldForm").style.display = "none";
    renderDbSidebar();
    showToast(`.${tld} bucket created`);
}

export function dbDeleteTld(tld) {
    if (dbDeletePendingTld === tld) {
        clearTimeout(dbDeleteTimer);
        dbDeletePendingTld = null;
        dbDeleteTimer = null;
        domainDB.deleteTld(tld);
        if (dbActiveTld === tld) {
            const remaining = domainDB.getTlds();
            dbActiveTld = remaining.length ? remaining[0] : null;
        }
        renderDbSidebar();
        showToast(`.${tld} deleted`, "warn");
    } else {
        if (dbDeleteTimer) clearTimeout(dbDeleteTimer);
        dbDeletePendingTld = tld;
        renderDbSidebar();
        dbDeleteTimer = setTimeout(() => {
            dbDeletePendingTld = null;
            dbDeleteTimer = null;
            renderDbSidebar();
        }, 2000);
    }
}

export function dbImportFromPaste() {
    if (!dbActiveTld) return;
    const ta = document.getElementById("dbPasteArea");
    if (!ta || !ta.value.trim()) return;
    const lines = ta.value.split(/[\r\n,]+/).map(l => l.trim()).filter(Boolean);
    const { added, skipped } = domainDB.addDomains(dbActiveTld, lines);
    ta.value = "";
    dbVisibleCount = DB_PAGE_SIZE;
    renderDbSidebar();
    if (added > 0) showToast(`Added ${added.toLocaleString()} to .${dbActiveTld}` + (skipped ? ` • ${skipped.toLocaleString()} skipped` : ""));
    else showToast(`All ${skipped.toLocaleString()} domains already in bucket`, "warn");
}

export function dbSetupDropZone() {
    const zone = document.getElementById("dbUploadZone");
    if (!zone) return;
    zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("dragover"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
    zone.addEventListener("drop", async e => {
        e.preventDefault();
        zone.classList.remove("dragover");
        if (!dbActiveTld) return;
        const files = Array.from(e.dataTransfer?.files || [])
            .filter(f => f.name.endsWith(".txt") || f.name.endsWith(".csv"));
        if (!files.length) return;
        const texts = await Promise.all(files.map(f =>
            f.text ? f.text() : new Promise((res, rej) => {
                const r = new FileReader(); r.onload = () => res(r.result);
                r.onerror = () => rej(r.error); r.readAsText(f);
            })
        ));
        const lines = texts.flatMap(t => t.split(/[\r\n,]+/).map(l => l.trim()).filter(Boolean));
        const { added, skipped } = domainDB.addDomains(dbActiveTld, lines);
        dbVisibleCount = DB_PAGE_SIZE;
        renderDbSidebar();
        showToast(`Added ${added.toLocaleString()} to .${dbActiveTld}` + (skipped ? ` • ${skipped.toLocaleString()} skipped` : ""));
    });
}

export function dbExportTld() {
    if (!dbActiveTld) return;
    const blob = new Blob([domainDB.getDomains(dbActiveTld).join("\n")], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `domains-${dbActiveTld}.txt`; a.click();
    URL.revokeObjectURL(url);
}

export function dbClearTld() {
    if (!dbActiveTld) return;
    if (!confirm(`Clear all ${domainDB.getCount(dbActiveTld).toLocaleString()} domains from .${dbActiveTld}?`)) return;
    domainDB.deleteTld(dbActiveTld);
    domainDB.addTld(dbActiveTld);
    dbVisibleCount = DB_PAGE_SIZE;
    renderDbSidebar();
    showToast(`.${dbActiveTld} cleared`);
}

/** The visible list only ever shows the active bucket, so the TLD is implicit. */
export function dbDeleteDomain(domain) {
    if (!dbActiveTld) return;
    domainDB.deleteDomain(dbActiveTld, domain);
    renderDbSidebar();
}

// --- Post-scan comparison ---
let dbNewDomains = [];
let dbKnownDomains = [];
let dbActiveSubTab = "new";

export async function dbFetchAndCompareScanResults() {
    try {
        const [availResp, errResp] = await Promise.all([
            fetch("/api/download/available"),
            fetch("/api/download/errors"),
        ]);
        const parse = async (resp) => {
            if (!resp.ok) return [];
            const text = await resp.text();
            return text.split(/\r?\n/).map(d => d.trim()).filter(Boolean);
        };
        const [available, errors] = await Promise.all([parse(availResp), parse(errResp)]);
        const merged = Array.from(new Set([...available, ...errors]));
        if (merged.length) dbRunComparison(merged);
    } catch (_e) {}
}

function dbRunComparison(scanDomains) {
    const allKnown = domainDB.getAllDomains();
    dbNewDomains = scanDomains.filter(d => !allKnown.has(domainDB.normalizeDomain(d) || d));
    dbKnownDomains = scanDomains.filter(d => allKnown.has(domainDB.normalizeDomain(d) || d));
    const section = document.getElementById("dbNewDomainsSection");
    if (!section) return;
    section.style.display = "block";
    document.getElementById("dbNewCount").textContent = dbNewDomains.length.toLocaleString();
    document.getElementById("dbKnownCount").textContent = dbKnownDomains.length.toLocaleString();
    document.getElementById("dbNewSummary").textContent =
        `${dbNewDomains.length.toLocaleString()} new / ${dbKnownDomains.length.toLocaleString()} known`;
    dbActiveSubTab = "new";
    document.querySelectorAll(".db-sub-tab").forEach(b => b.classList.toggle("active", b.dataset.subtab === "new"));
    dbRenderSubTab();
}

export function dbSwitchSubTab(name) {
    dbActiveSubTab = name;
    document.querySelectorAll(".db-sub-tab").forEach(b => b.classList.toggle("active", b.dataset.subtab === name));
    dbRenderSubTab();
}

function dbRenderSubTab() {
    const newList = document.getElementById("dbNewList");
    const knownList = document.getElementById("dbKnownList");
    if (!newList || !knownList) return;
    newList.style.display = dbActiveSubTab === "new" ? "block" : "none";
    knownList.style.display = dbActiveSubTab === "known" ? "block" : "none";
    const render = (items, cls) => items.length
        ? items.map(d => `<div class="db-comparison-item ${cls}">${escapeHtml(d)}</div>`).join("")
        : '<div class="db-list-empty">Empty</div>';
    if (dbActiveSubTab === "new") newList.innerHTML = render(dbNewDomains, "is-new");
    else knownList.innerHTML = render(dbKnownDomains, "is-known");
}

export function dbCopyNew() {
    if (!dbNewDomains.length) return;
    navigator.clipboard?.writeText(dbNewDomains.join("\n"))
        .then(() => showToast(`Copied ${dbNewDomains.length.toLocaleString()} domains`))
        .catch(() => showToast("Copy failed", "error"));
}

export function dbAddNewToBucket() {
    if (!dbNewDomains.length) return;
    const tlds = domainDB.getTlds();
    if (!tlds.length) { showToast("Create a TLD bucket first in the Domain DB tab", "warn"); return; }
    const modal = document.createElement("div");
    modal.className = "db-bucket-modal";
    modal.innerHTML = `
        <div class="db-bucket-modal-inner">
            <h4>Add ${dbNewDomains.length.toLocaleString()} new domains to bucket</h4>
            <select id="dbModalTldSelect">
                ${tlds.map(t => `<option value="${escapeHtml(t)}">.${escapeHtml(t)}</option>`).join("")}
            </select>
            <div class="db-bucket-modal-btns">
                <button class="db-secondary-btn" data-bucket-cancel>Cancel</button>
                <button class="btn-primary" data-bucket-confirm>Add</button>
            </div>
        </div>`;
    document.body.appendChild(modal);
    modal.addEventListener("click", e => {
        if (e.target === modal || e.target.closest("[data-bucket-cancel]")) {
            modal.remove();
            return;
        }
        if (e.target.closest("[data-bucket-confirm]")) {
            dbConfirmAddNew(modal);
        }
    });
}

function dbConfirmAddNew(modal) {
    const tld = modal.querySelector("#dbModalTldSelect").value;
    const { added, skipped } = domainDB.addDomains(tld, dbNewDomains);
    modal.remove();
    updateDbTabCount();
    const addBtn = document.getElementById("dbAddNewBtn");
    if (addBtn) {
        const orig = addBtn.textContent;
        addBtn.textContent = "✓ Added";
        addBtn.classList.add("db-pulse");
        setTimeout(() => { addBtn.textContent = orig; addBtn.classList.remove("db-pulse"); }, 1800);
    }
    showToast(`Added ${added.toLocaleString()} to .${tld}` + (skipped ? ` • ${skipped.toLocaleString()} skipped` : ""));
}

/* ================================================
   DR Checker
   ================================================ */
