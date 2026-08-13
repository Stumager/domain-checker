/* DR Checker tab: bulk Ahrefs Domain Rating lookup. */

import { escapeHtml } from "./shared.js";

let _drRunning = false;
let _drStopped = false;
let _drResults = [];
let drSortState = 0;
let drOriginalRows = [];

function _drNormalize(raw) {
    let s = (raw || "").trim();
    s = s.replace(/^https?:\/\//i, "");
    s = s.replace(/^www\./i, "");
    s = s.split("/")[0].split("?")[0].split("#")[0].toLowerCase();
    return s;
}

// Sent per request; the server resolves the whole chunk in parallel. Smaller
// chunks show progress sooner, larger ones cut round-trips.
const DR_BATCH_SIZE = 20;

export async function drStartCheck() {
    if (_drRunning) return;
    const lines = [...new Set((document.getElementById("drInput").value || "")
        .split("\n").map(_drNormalize).filter(Boolean))];
    if (!lines.length) { alert("Введите домены"); return; }

    _drRunning = true;
    _drStopped = false;
    _drResults = [];

    document.getElementById("drStartBtn").style.display = "none";
    document.getElementById("drStopBtn").style.display = "block";
    document.getElementById("drProgress").style.display = "block";
    document.getElementById("drProgressText").textContent = "";
    document.getElementById("drResultsSection").style.display = "block";
    document.getElementById("drTbody").innerHTML = "";
    document.getElementById("drExportRow").style.display = "none";

    const tbody = document.getElementById("drTbody");

    for (let start = 0; start < lines.length && !_drStopped; start += DR_BATCH_SIZE) {
        const batch = lines.slice(start, start + DR_BATCH_SIZE);
        const upTo = Math.min(start + batch.length, lines.length);
        document.getElementById("drProgressText").textContent =
            `Checking ${start + 1}–${upTo} / ${lines.length}`;

        let results = [];
        try {
            const r = await fetch("/api/dr-check", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ domains: batch }),
            });
            if (r.ok) {
                results = (await r.json()).results || [];
            }
        } catch (_) { /* network error → placeholders below */ }

        if (!results.length) {
            results = batch.map(domain => ({ domain, dr: null }));
        }

        for (const item of results) {
            const drValue = (item.dr === null || item.dr === undefined) ? "—" : String(item.dr);
            _drResults.push({ domain: item.domain, dr: drValue });
            const tr = document.createElement("tr");
            tr.innerHTML = `<td>${escapeHtml(item.domain)}</td><td>${escapeHtml(drValue)}</td>`;
            tbody.appendChild(tr);
        }

        drOriginalRows = Array.from(tbody.rows);
        document.getElementById("drExportRow").style.display = "block";
    }

    _drRunning = false;
    document.getElementById("drStartBtn").style.display = "block";
    document.getElementById("drStopBtn").style.display = "none";
    document.getElementById("drProgressText").textContent = _drStopped
        ? `Stopped at ${_drResults.length} / ${lines.length}`
        : `Done: ${_drResults.length} domain(s)`;
}

export function drStop() { _drStopped = true; }

/** Cycle the DR column through original order -> descending -> ascending. */
export function drToggleSort() {
    drSortState = (drSortState + 1) % 3;
    document.getElementById("drSortIcon").textContent = ["⇅", "↓", "↑"][drSortState];

    const tbody = document.getElementById("drTbody");
    if (drSortState === 0) {
        drOriginalRows.forEach(r => tbody.appendChild(r));
        return;
    }

    const rows = Array.from(tbody.rows);
    rows.sort((a, b) => {
        const av = parseFloat(a.cells[1].textContent) || -1;
        const bv = parseFloat(b.cells[1].textContent) || -1;
        return drSortState === 1 ? bv - av : av - bv;
    });
    rows.forEach(r => tbody.appendChild(r));
}

export function drExportCsv() {
    const rows = [["domain", "dr"]];
    Array.from(document.getElementById("drTbody").rows).forEach(r => {
        const domain = r.cells[0].textContent.trim();
        const dr = r.cells[1].textContent.trim();
        rows.push([domain, dr === "—" ? "" : dr]);
    });
    const csv = rows.map(r => r.join(";")).join("\n");
    const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
    const date = new Date().toISOString().slice(0, 10);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `dr-export-${date}.csv`;
    a.click();
}
