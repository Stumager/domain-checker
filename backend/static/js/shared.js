/* Helpers used by more than one tab. */

export function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

/** Transient notification in the bottom corner. Used by the DB and Maps tabs. */
export function showToast(msg, type = "success") {
    const container = document.getElementById("dbToastContainer");
    if (!container) return;

    const el = document.createElement("div");
    el.className = "db-toast" + (type !== "success" ? ` toast-${type}` : "");
    el.textContent = msg;
    container.appendChild(el);

    setTimeout(() => {
        el.classList.add("toast-out");
        setTimeout(() => el.remove(), 260);
    }, 3000);
}
