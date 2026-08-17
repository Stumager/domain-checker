/* Entry point: tab navigation and every DOM binding.
 *
 * Modules export behaviour and never attach listeners to markup they do not
 * own, so all wiring lives here. Buttons declare `data-action` (plus optional
 * `data-arg`) and are dispatched through one delegated click handler, which
 * keeps working for markup rendered after load.
 */

import {
    adminCloseModal,
    adminCopyPassword,
    adminCreateUser,
    adminInit,
    bindAdminTab,
} from "./admin.js";
import { authLogout } from "./auth.js";
import { showToast } from "./shared.js";
import {
    applyArchiveFilters,
    fetchWaybackData,
    handleArchiveKeyPress,
    toggleArchiveModal,
} from "./archive.js";
import {
    downloadAllResults,
    downloadResult,
    filterTlds,
    FILE_HINT_IDLE,
    handleDragLeave,
    handleDragOver,
    handleDrop,
    handleFileInputChange,
    openDomainFilePicker,
    replaceDomainsInputValue,
    restoreDomainsInputDraft,
    resumeCheckIfRunning,
    setDropHint,
    startCheck,
    stopCheck,
    updateDomainCount,
} from "./checker.js";
import {
    cancelPendingTldDelete,
    dbAddNewToBucket,
    dbClearTld,
    dbCommitAddTld,
    dbCopyNew,
    dbDeleteDomain,
    dbDeleteTld,
    dbExportTld,
    dbImportFromPaste,
    dbLoadMoreDomains,
    dbSearch,
    dbSelectTld,
    dbSetupDropZone,
    dbShowAddTld,
    dbSwitchSubTab,
    renderDbSidebar,
    updateDbTabCount,
} from "./domain-db.js";
import {
    mapsAddProxies,
    mapsCheckProxies,
    mapsClearFailedProxies,
    mapsCopyDomains,
    mapsDeleteProxy,
    mapsExportCsv,
    mapsExportTxt,
    mapsFetchDomainsForChecker,
    mapsInit,
    mapsLoadDomains,
    mapsLoadProxies,
    mapsOnNicheChange,
    mapsShowAllSessions,
    mapsStartJob,
    mapsStopJob,
    mapsSwitchSubTab,
    mapsToggleSection,
} from "./maps.js";

// A refresh with no memory of which tab was open always landed back on Domain
// Checker — jarring mid-scrape, since it also masked that Maps had a live job
// (see restoreActiveTab() below; the job itself never stopped, only the view of it).
const ACTIVE_TAB_KEY = "activeTab";
const KNOWN_TABS = ["checker", "domaindb", "maps", "admin"];

function switchTab(tabName) {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === tabName));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.toggle("active", p.id === "tab-" + tabName));
    if (tabName === "domaindb") renderDbSidebar();
    if (tabName === "maps") mapsInit();
    if (tabName === "admin") adminInit();
    try {
        localStorage.setItem(ACTIVE_TAB_KEY, tabName);
    } catch (_e) {}
}

function restoreActiveTab() {
    let saved = null;
    try {
        saved = localStorage.getItem(ACTIVE_TAB_KEY);
    } catch (_e) {}
    if (saved && KNOWN_TABS.includes(saved) && saved !== "checker") {
        switchTab(saved);
    }
}

/** Owns the tab switch itself, which is why this lives here rather than in
 * maps.js — see the file header comment. mapsFetchDomainsForChecker() does
 * the fetching, replaceDomainsInputValue() does the filling. */
async function mapsSendToChecker() {
    const { domains, truncated } = await mapsFetchDomainsForChecker();
    if (!domains.length) {
        showToast("No domains match the current filter", "warn");
        return;
    }
    replaceDomainsInputValue(domains.join("\n"));
    switchTab("checker");
    showToast(
        `Sent ${domains.length.toLocaleString()} domain(s) to Domain Checker` +
        (truncated ? " (capped at 5000)" : "")
    );
}

// Every data-action value in index.html must have an entry here.
const ACTIONS = {
    authLogout,
    adminCreateUser,
    adminCloseModal,
    adminCopyPassword,
    startCheck,
    stopCheck,
    toggleArchiveModal,
    fetchWaybackData,
    downloadResult,
    downloadAllResults,
    dbSwitchSubTab,
    dbAddNewToBucket,
    dbCopyNew,
    dbExportTld,
    dbClearTld,
    dbImportFromPaste,
    dbLoadMoreDomains,
    mapsOnNicheChange,
    mapsShowAllSessions,
    mapsStartJob,
    mapsStopJob,
    mapsToggleSection,
    mapsAddProxies,
    mapsCheckProxies,
    mapsClearFailedProxies,
    mapsSwitchSubTab,
    mapsExportTxt,
    mapsExportCsv,
    mapsCopyDomains,
    mapsSendToChecker,
};

function bindActionDispatch() {
    document.addEventListener("click", (event) => {
        const target = event.target.closest("[data-action]");
        if (!target) return;

        const action = ACTIONS[target.dataset.action];
        if (!action) {
            console.warn("Unknown data-action:", target.dataset.action);
            return;
        }
        action(target.dataset.arg);
    });
}

function bindCheckerTab() {
    const textarea = document.getElementById("domainsInput");
    if (!textarea) return;

    restoreDomainsInputDraft(); // before the first updateDomainCount() reads the textarea
    textarea.addEventListener("input", updateDomainCount);
    updateDomainCount();

    const filterBtn = document.getElementById("filterBtn");
    if (filterBtn) filterBtn.addEventListener("click", filterTlds);

    setDropHint(FILE_HINT_IDLE);

    const dropHint = document.getElementById("dropHint");
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

    const fileInput = document.getElementById("domainsFileInput");
    if (fileInput) fileInput.addEventListener("change", handleFileInputChange);
}

function bindArchiveModal() {
    const hideNaToggle = document.getElementById("archiveHideNaToggle");
    if (hideNaToggle) {
        hideNaToggle.addEventListener("change", () => { void applyArchiveFilters(); });
    }

    const modal = document.getElementById("archiveModal");
    if (modal) {
        modal.addEventListener("click", (e) => {
            if (e.target === modal) toggleArchiveModal();
        });
    }

    ["archiveSearchInput", "archiveProxyInput"].forEach((id) => {
        const input = document.getElementById(id);
        if (input) input.addEventListener("keypress", handleArchiveKeyPress);
    });
}

function bindDomainDbTab() {
    const addTldBtn = document.getElementById("dbAddTldBtn");
    if (addTldBtn) addTldBtn.addEventListener("click", dbShowAddTld);

    const tldInput = document.getElementById("dbTldInput");
    if (tldInput) tldInput.addEventListener("keydown", dbCommitAddTld);

    const searchInput = document.getElementById("dbSearchInput");
    if (searchInput) searchInput.addEventListener("input", (e) => dbSearch(e.target.value));

    // Delegated: both lists are re-rendered constantly.
    const tldList = document.getElementById("dbTldList");
    if (tldList) {
        tldList.addEventListener("click", (e) => {
            const deleteBtn = e.target.closest("[data-delete-tld]");
            if (deleteBtn) {
                e.stopPropagation();
                dbDeleteTld(deleteBtn.dataset.deleteTld);
                return;
            }
            const item = e.target.closest(".db-tld-item[data-tld]");
            if (item) dbSelectTld(item.dataset.tld);
        });
    }

    const domainList = document.getElementById("dbDomainList");
    if (domainList) {
        domainList.addEventListener("click", (e) => {
            const btn = e.target.closest("[data-delete-domain]");
            if (btn) dbDeleteDomain(btn.dataset.deleteDomain);
        });
    }

    document.addEventListener("click", (e) => {
        if (!e.target.closest("#dbTldList")) cancelPendingTldDelete();
    });

    dbSetupDropZone();
    updateDbTabCount();
}

function bindMapsTab() {
    // Delegated: pagination and the proxy table are rebuilt on every load.
    const pager = (containerId, attribute, handler) => {
        const container = document.getElementById(containerId);
        if (!container) return;
        container.addEventListener("click", (e) => {
            const btn = e.target.closest(`[${attribute}]`);
            if (btn && !btn.disabled) handler(Number(btn.getAttribute(attribute)));
        });
    };

    pager("mapsPagination", "data-domains-page", mapsLoadDomains);
    pager("mapsProxyPagination", "data-proxy-page", mapsLoadProxies);

    const proxyTable = document.getElementById("mapsProxyTbody");
    if (proxyTable) {
        proxyTable.addEventListener("click", (e) => {
            const btn = e.target.closest("[data-delete-proxy]");
            if (btn) mapsDeleteProxy(Number(btn.dataset.deleteProxy));
        });
    }
}

window.addEventListener("DOMContentLoaded", () => {
    bindActionDispatch();

    document.querySelectorAll(".tab-btn").forEach((btn) => {
        btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });

    bindCheckerTab();
    bindArchiveModal();
    bindDomainDbTab();
    bindMapsTab();
    bindAdminTab();

    // Neither the Domain Checker scan nor a Maps job stop just because the
    // page reloaded — both are server-side daemon work. Reconnect to whatever
    // is actually running before the user notices anything looks idle.
    restoreActiveTab();
    resumeCheckIfRunning();
});
