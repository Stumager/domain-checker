/* Maps Scraper tab: job lifecycle, results and the proxy pool. */

import { escapeHtml, showToast } from "./shared.js";

const MAPS_PAGE_SIZE = 50;
const MAPS_PROXY_PAGE_SIZE = 10;

let mapsGeo = [];
let mapsNiches = [];
let mapsSessions = [];
let mapsInitStarted = false;
let mapsStatusTimer = null;
let mapsSearchTimer = null;
let mapsPage = 1;
let mapsTotalPages = 0;
let mapsNicheManual = false;
let mapsIsRunning = false;
let mapsLastDomainCount = -1;
let mapsProxyPage = 1;
let mapsProxyTotalPages = 0;
let mapsProxySearchTimer = null;

function mapsShowError(message) {
    const el = document.getElementById("mapsError");
    if (!el) return;
    el.textContent = message || "";
    el.classList.toggle("active", Boolean(message));
}

export function mapsToggleSection(id) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("open");
}

export function mapsSwitchSubTab(name) {
    document.querySelectorAll("[data-mapstab]").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.mapstab === name);
    });
    const domainsPane = document.getElementById("mapsDomainsPane");
    const exportPane = document.getElementById("mapsExportPane");
    if (domainsPane) domainsPane.style.display = name === "domains" ? "block" : "none";
    if (exportPane) exportPane.style.display = name === "export" ? "block" : "none";
}

export async function mapsInit() {
    if (mapsInitStarted) return;
    mapsInitStarted = true;

    await mapsLoadGeo();
    await mapsLoadSessions();
    mapsLoadProxies();
    mapsLoadDomains(1);
    mapsStatusPoll();

    const proxySearch = document.getElementById("mapsProxySearch");
    if (proxySearch) {
        proxySearch.addEventListener("input", () => {
            clearTimeout(mapsProxySearchTimer);
            mapsProxySearchTimer = setTimeout(() => mapsLoadProxies(1), 250);
        });
    }

    if (!mapsStatusTimer) {
        mapsStatusTimer = setInterval(mapsStatusPoll, 10000);
    }

    const search = document.getElementById("mapsSearch");
    if (search) {
        search.addEventListener("input", () => {
            clearTimeout(mapsSearchTimer);
            mapsSearchTimer = setTimeout(() => mapsLoadDomains(1), 300);
        });
    }

    const tldSelect = document.getElementById("mapsTldSelect");
    if (tldSelect) {
        tldSelect.addEventListener("change", () => mapsLoadDomains(1));
    }

    ["mapsSessionSelect", "mapsStatusSelect", "mapsActiveOnly"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener("change", () => mapsLoadDomains(1));
    });
}

async function mapsLoadSessions() {
    try {
        const resp = await fetch("/api/maps/sessions");
        if (!resp.ok) return;
        mapsSessions = await resp.json();
        const select = document.getElementById("mapsSessionSelect");
        if (!select) return;
        select.innerHTML = '<option value="">Все сессии</option>' + mapsSessions.map(s => {
            const label = `#${s.id} ${s.niche || s.custom_query || "поиск"} — ${s.city}, ${s.country}`;
            return `<option value="${s.id}">${escapeHtml(label)}</option>`;
        }).join("");
    } catch (e) {
        mapsShowError("Could not load sessions: " + e.message);
    }
}

async function mapsLoadGeo() {
    try {
        const [geoResp, nicheResp] = await Promise.all([
            fetch("/api/maps/geo"),
            fetch("/api/maps/niches")
        ]);

        if (!geoResp.ok) {
            const err = await geoResp.json().catch(() => ({}));
            throw new Error(err.error || "Could not load geo data");
        }

        mapsGeo = await geoResp.json();
        mapsNiches = nicheResp.ok ? await nicheResp.json() : [];

        const nicheSelect = document.getElementById("mapsNiche");
        if (nicheSelect) {
            nicheSelect.innerHTML = mapsNiches
                .map(n => `<option value="${escapeHtml(n.value)}">${escapeHtml(n.label)}</option>`)
                .join("");
        }

        const countrySelect = document.getElementById("mapsCountry");
        if (countrySelect) {
            const countryOptions = document.getElementById("mapsCountryOptions");
            if (countryOptions) {
                countryOptions.innerHTML = mapsGeo
                    .map(c => `<option value="${escapeHtml(c.name)}"></option>`)
                    .join("");
            }
            countrySelect.addEventListener("input", mapsOnCountryChange);
        }

        mapsOnCountryChange();
        mapsInitGeoFilters();
        mapsShowError("");
    } catch (e) {
        mapsShowError("Maps: " + e.message);
    }
}

function mapsOnCountryChange() {
    const countrySelect = document.getElementById("mapsCountry");
    const citySelect = document.getElementById("mapsCity");
    if (!countrySelect || !citySelect) return;

    const country = mapsGeo.find(c => c.name === countrySelect.value || c.code === countrySelect.value);
    const cities = (country && country.cities) || [];

    const cityOptions = document.getElementById("mapsCityOptions");
    if (cityOptions) {
        cityOptions.innerHTML = cities
            .map(city => `<option value="${escapeHtml(city.name)}"></option>`)
            .join("");
    }
    if (!country) citySelect.value = "";

    const languageInput = document.getElementById("mapsLanguage");
    if (languageInput && country && country.language) {
        languageInput.value = country.language;
    }
}

export function mapsOnNicheChange() {
    mapsNicheManual = !mapsNicheManual;

    const select = document.getElementById("mapsNiche");
    const manual = document.getElementById("mapsNicheManual");
    const toggle = document.getElementById("mapsNicheToggle");
    if (!select || !manual || !toggle) return;

    select.style.display = mapsNicheManual ? "none" : "block";
    manual.style.display = mapsNicheManual ? "block" : "none";
    toggle.textContent = mapsNicheManual ? "list" : "manual";

    if (mapsNicheManual) manual.focus();
}

function mapsCurrentNiche() {
    if (mapsNicheManual) {
        return (document.getElementById("mapsNicheManual")?.value || "").trim();
    }
    return document.getElementById("mapsNiche")?.value || "";
}

export async function mapsStartJob() {
    const countrySelect = document.getElementById("mapsCountry");
    const country = mapsGeo.find(c => c.name === countrySelect?.value || c.code === countrySelect?.value);

    const payload = {
        niche: mapsCurrentNiche(),
        country: country ? country.name : "",
        city: document.getElementById("mapsCity")?.value || "",
        language: (document.getElementById("mapsLanguage")?.value || "").trim(),
        tld_filter: (document.getElementById("mapsTldFilter")?.value || "").trim(),
        depth: parseInt(document.getElementById("mapsDepth")?.value, 10) || 10,
        zoom: parseInt(document.getElementById("mapsZoom")?.value, 10) || 15,
        custom_query: (document.getElementById("mapsCustomQuery")?.value || "").trim()
    };

    if (!payload.niche && !payload.custom_query) {
        mapsShowError("Pick a niche or enter a custom query");
        return;
    }
    if (!payload.country || !payload.city) {
        mapsShowError("Pick a country and a city");
        return;
    }

    const startBtn = document.getElementById("mapsStartBtn");
    if (startBtn) startBtn.disabled = true;
    mapsShowError("");

    try {
        const resp = await fetch("/api/maps/job/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await resp.json().catch(() => ({}));

        if (!resp.ok) {
            mapsShowError(resp.status === 503
                ? "Google Maps scraper is unreachable — is the Docker container running? " + (data.error || "")
                : (data.error || "Could not start the job"));
            return;
        }

        mapsStatusPoll();
        mapsLoadSessions();
    } catch (e) {
        mapsShowError("Network error: " + e.message);
    } finally {
        if (startBtn) startBtn.disabled = false;
    }
}

export async function mapsStopJob() {
    const stopBtn = document.getElementById("mapsStopBtn");
    if (stopBtn) stopBtn.disabled = true;

    try {
        const resp = await fetch("/api/maps/job/stop", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({})
        });
        const data = await resp.json().catch(() => ({}));

        if (!resp.ok) {
            mapsShowError(data.error || "Could not stop the job");
            return;
        }

        mapsStatusPoll();
    } catch (e) {
        mapsShowError("Network error: " + e.message);
    } finally {
        if (stopBtn) stopBtn.disabled = false;
    }
}

async function mapsStatusPoll() {
    try {
        const resp = await fetch("/api/maps/job/status");
        if (!resp.ok) return;

        const data = await resp.json();
        const job = data.job;
        const bar = document.getElementById("mapsStatusBar");
        const startBtn = document.getElementById("mapsStartBtn");
        const stopBtn = document.getElementById("mapsStopBtn");
        const coverage = data.coverage;
        const coverageBox = document.getElementById("mapsCoverage");
        const coverageFill = document.getElementById("mapsCoverageFill");
        const coveragePercent = document.getElementById("mapsCoveragePercent");
        const coverageMeta = document.getElementById("mapsCoverageMeta");

        mapsIsRunning = Boolean(job && job.status === "running");

        if (bar) bar.classList.toggle("active", Boolean(job));
        if (coverageBox) coverageBox.hidden = !coverage?.available;
        if (coverage?.available) {
            const percent = Number(coverage.percent || 0);
            if (coverageFill) coverageFill.style.width = percent + "%";
            if (coveragePercent) coveragePercent.textContent = percent + "%";
            if (coverageMeta) coverageMeta.textContent =
                `Completed cells: ${coverage.completed_cells} / ${coverage.total_cells}`;
            const track = coverageBox?.querySelector(".maps-coverage-track");
            if (track) track.setAttribute("aria-valuenow", String(percent));
        }
        if (startBtn) startBtn.style.display = mapsIsRunning ? "none" : "block";
        if (stopBtn) stopBtn.style.display = mapsIsRunning ? "block" : "none";

        if (job) {
            const statusEl = document.getElementById("mapsStatStatus");
            if (statusEl) {
                statusEl.textContent = job.status;
                statusEl.className = "is-" + job.status;
            }
            document.getElementById("mapsStatCycles").textContent = job.cycle_count ?? 0;
            document.getElementById("mapsStatDomains").textContent = (data.domains ?? 0).toLocaleString();
            document.getElementById("mapsStatTime").textContent =
                job.last_run_at ? new Date(job.last_run_at).toLocaleString() : "—";
        }

        // подтянуть таблицу, когда появились новые домены
        const total = data.total_domains ?? 0;
        if (total !== mapsLastDomainCount) {
            mapsLastDomainCount = total;
            if (mapsPage === 1) mapsLoadDomains(1);
        }
    } catch (e) {
        // сеть моргнула — следующий тик повторит
    }
}

function mapsFilterParams() {
    const params = new URLSearchParams();
    const search = (document.getElementById("mapsSearch")?.value || "").trim();
    const tld = document.getElementById("mapsTldSelect")?.value || "";
    const country = document.getElementById("mapsCountryFilter")?.value || "";
    const city = document.getElementById("mapsCityFilter")?.value || "";
    if (search) params.set("search", search);
    if (tld) params.set("tld", tld);
    if (country) params.set("country", country);
    if (city) params.set("city", city);
    const session = document.getElementById("mapsSessionSelect")?.value || "";
    const exportStatus = document.getElementById("mapsStatusSelect")?.value || "new";
    const active = document.getElementById("mapsActiveOnly")?.checked;
    if (session) params.set("session", session);
    if (exportStatus) params.set("export_status", exportStatus);
    if (active) params.set("active", "1");
    return params;
}

function mapsInitGeoFilters() {
    const country = document.getElementById("mapsCountryFilter");
    const city = document.getElementById("mapsCityFilter");
    if (!country || !city) return;

    const countryInput = document.createElement("input");
    countryInput.type = "text";
    countryInput.id = "mapsCountryFilter";
    countryInput.setAttribute("list", "mapsCountryFilterOptions");
    countryInput.placeholder = "Country";
    countryInput.autocomplete = "off";
    const countryOptions = document.createElement("datalist");
    countryOptions.id = "mapsCountryFilterOptions";
    country.replaceWith(countryInput, countryOptions);

    const cityInput = document.createElement("input");
    cityInput.type = "text";
    cityInput.id = "mapsCityFilter";
    cityInput.setAttribute("list", "mapsCityFilterOptions");
    cityInput.placeholder = "City";
    cityInput.autocomplete = "off";
    const cityOptions = document.createElement("datalist");
    cityOptions.id = "mapsCityFilterOptions";
    city.replaceWith(cityInput, cityOptions);

    countryOptions.innerHTML = mapsGeo.map(item =>
        `<option value="${escapeHtml(item.name)}"></option>`
    ).join("");

    const allCities = mapsGeo.flatMap(item => item.cities || []);
    const refreshCities = () => {
        const selected = mapsGeo.find(item => item.name === countryInput.value);
        const source = selected ? selected.cities : allCities;
        const query = cityInput.value.trim().toLocaleLowerCase();
        cityOptions.innerHTML = source
            .filter(item => !query || item.name.toLocaleLowerCase().includes(query))
            .slice(0, 300)
            .map(item => `<option value="${escapeHtml(item.name)}"></option>`)
            .join("");
    };

    countryInput.addEventListener("input", () => {
        cityInput.value = "";
        refreshCities();
        if (!countryInput.value || mapsGeo.some(item => item.name === countryInput.value)) mapsLoadDomains(1);
    });
    cityInput.addEventListener("input", () => {
        refreshCities();
        if (!cityInput.value || allCities.some(item => item.name === cityInput.value)) mapsLoadDomains(1);
    });
    refreshCities();
    return;
    country.innerHTML = '<option value="">Все страны</option>' + mapsGeo.map(item =>
        `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)}</option>`
    ).join("");
    country.onchange = () => {
        const selected = mapsGeo.find(item => item.name === country.value);
        city.innerHTML = '<option value="">Все города</option>' + ((selected && selected.cities) || []).map(item =>
            `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)}</option>`
        ).join("");
        mapsLoadDomains(1);
    };
    city.onchange = () => mapsLoadDomains(1);
    city.innerHTML = '<option value="">Все города</option>';
}

export async function mapsLoadDomains(page) {
    mapsPage = Math.max(1, page || 1);

    const params = mapsFilterParams();
    params.set("page", mapsPage);
    params.set("limit", MAPS_PAGE_SIZE);

    try {
        const resp = await fetch("/api/maps/domains?" + params.toString());
        if (!resp.ok) return;

        const data = await resp.json();
        mapsTotalPages = data.pages || 0;

        // фильтр мог сократить выдачу — не оставляем пользователя на пустой странице
        if (mapsTotalPages > 0 && mapsPage > mapsTotalPages) {
            return mapsLoadDomains(mapsTotalPages);
        }

        const countEl = document.getElementById("mapsDomainsCount");
        if (countEl) countEl.textContent = (data.total || 0).toLocaleString();

        mapsRenderTldOptions(data.tlds || []);

        const tbody = document.getElementById("mapsDomainsTbody");
        if (tbody) {
            tbody.innerHTML = (data.items || []).length
                ? data.items.map(item => `<tr>
                        <td>${escapeHtml(item.domain)}</td>
                        <td>${escapeHtml(item.business_name)}</td>
                        <td>${escapeHtml(item.country)}</td>
                        <td>${escapeHtml(item.city)}</td>
                        <td>${escapeHtml(item.niche)}</td>
                        <td>${escapeHtml((item.discovered_at || "").replace("T", " "))}</td>
                        <td>${item.exported_at ? "Выгружен" : "Новый"}</td>
                    </tr>`).join("")
                : `<tr><td colspan="6" class="maps-empty">No domains yet — start a scrape to collect them.</td></tr>`;
        }

        const pagination = document.getElementById("mapsPagination");
        if (pagination) {
            pagination.innerHTML = mapsTotalPages > 1
                ? `<button type="button" class="db-load-more-btn" ${mapsPage <= 1 ? "disabled" : ""}
                        data-domains-page="${mapsPage - 1}">Prev</button>
                   <span>Page ${mapsPage} of ${mapsTotalPages}</span>
                   <button type="button" class="db-load-more-btn" ${mapsPage >= mapsTotalPages ? "disabled" : ""}
                        data-domains-page="${mapsPage + 1}">Next</button>`
                : "";
        }
    } catch (e) {
        mapsShowError("Could not load domains: " + e.message);
    }
}

function mapsRenderTldOptions(tlds) {
    const select = document.getElementById("mapsTldSelect");
    if (!select) return;

    const current = select.value;
    const options = ['<option value="">All TLDs</option>'].concat(
        tlds.map(tld => `<option value="${escapeHtml(tld)}">.${escapeHtml(tld)}</option>`)
    );
    const markup = options.join("");

    if (select.innerHTML !== markup) {
        select.innerHTML = markup;
        if (current && tlds.includes(current)) select.value = current;
    }
}

export function mapsExportTxt() {
    const params = mapsFilterParams();
    params.set("format", "txt");
    window.location = "/api/maps/domains/export?" + params.toString();
}

export function mapsExportCsv() {
    const params = mapsFilterParams();
    params.set("format", "csv");
    window.location = "/api/maps/domains/export?" + params.toString();
}

export async function mapsLoadProxies(page) {
    mapsProxyPage = Math.max(1, page || mapsProxyPage);
    try {
        const params = new URLSearchParams({
            page: mapsProxyPage,
            limit: MAPS_PROXY_PAGE_SIZE,
            search: (document.getElementById("mapsProxySearch")?.value || "").trim()
        });
        const resp = await fetch("/api/maps/proxies?" + params.toString());
        if (!resp.ok) return;

        const data = await resp.json();
        mapsProxyTotalPages = data.pages || 0;
        if (mapsProxyTotalPages > 0 && mapsProxyPage > mapsProxyTotalPages) {
            return mapsLoadProxies(mapsProxyTotalPages);
        }
        const tbody = document.getElementById("mapsProxyTbody");
        if (!tbody) return;

        tbody.innerHTML = (data.items || []).length
            ? data.items.map(item => `<tr>
                    <td>${escapeHtml(item.proxy_masked || item.proxy)}</td>
                    <td><span class="status-badge ${escapeHtml(item.status)}">${escapeHtml(item.status)}</span></td>
                    <td>${escapeHtml((item.last_checked || "—").replace("T", " "))}</td>
                    <td><button type="button" class="maps-row-btn"
                            data-delete-proxy="${item.id}">Delete</button></td>
                </tr>`).join("")
            : `<tr><td colspan="4" class="maps-empty">No proxies added.</td></tr>`;

        const checkBtn = document.getElementById("mapsCheckProxiesBtn");
        if (checkBtn) {
            checkBtn.disabled = Boolean(data.checking);
            checkBtn.textContent = data.checking ? "Checking…" : "Check All";
        }

        // пока проверка идёт — обновляем таблицу
        const pagination = document.getElementById("mapsProxyPagination");
        if (pagination) {
            pagination.innerHTML = mapsProxyTotalPages > 1
                ? `<button type="button" class="db-load-more-btn" ${mapsProxyPage <= 1 ? "disabled" : ""}
                        data-proxy-page="${mapsProxyPage - 1}">Prev</button>
                   <span>Page ${mapsProxyPage} of ${mapsProxyTotalPages}</span>
                   <button type="button" class="db-load-more-btn" ${mapsProxyPage >= mapsProxyTotalPages ? "disabled" : ""}
                        data-proxy-page="${mapsProxyPage + 1}">Next</button>`
                : "";
        }

        if (data.checking) setTimeout(mapsLoadProxies, 2000);
    } catch (e) {
        mapsShowError("Could not load proxies: " + e.message);
    }
}

export async function mapsClearFailedProxies() {
    if (!confirm("Clear all failed proxies?")) return;
    try {
        const resp = await fetch("/api/maps/proxies/failed", { method: "DELETE" });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            mapsShowError(data.error || "Could not clear failed proxies");
            return;
        }
        showToast(`Cleared ${data.deleted || 0} failed proxy(ies)`);
        await mapsLoadProxies(mapsProxyPage);
    } catch (e) {
        mapsShowError("Network error: " + e.message);
    }
}

export async function mapsAddProxies() {
    const textarea = document.getElementById("mapsProxyInput");
    const raw = (textarea?.value || "").trim();
    if (!raw) return;

    try {
        const resp = await fetch("/api/maps/proxies", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ proxies: raw })
        });
        const data = await resp.json().catch(() => ({}));

        if (!resp.ok) {
            mapsShowError(data.error || "Could not add proxies");
            return;
        }

        if (textarea) textarea.value = "";
        showToast(`Added ${data.added} proxy(ies)`);
        await mapsLoadProxies();
    } catch (e) {
        mapsShowError("Network error: " + e.message);
    }
}

export async function mapsCheckProxies() {
    try {
        const resp = await fetch("/api/maps/proxies/check", { method: "POST" });
        const data = await resp.json().catch(() => ({}));

        if (!resp.ok) {
            mapsShowError(data.error || "Could not start the proxy check");
            return;
        }

        await mapsLoadProxies();
    } catch (e) {
        mapsShowError("Network error: " + e.message);
    }
}

export async function mapsDeleteProxy(proxyId) {
    try {
        const resp = await fetch("/api/maps/proxies/" + proxyId, { method: "DELETE" });
        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            mapsShowError(data.error || "Could not delete the proxy");
            return;
        }
        await mapsLoadProxies();
    } catch (e) {
        mapsShowError("Network error: " + e.message);
    }
}
