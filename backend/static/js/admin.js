/* Admin tab: create, delete and manage team accounts. */

import { escapeHtml, showToast } from "./shared.js";

let adminModalPassword = "";

export async function adminInit() {
    await adminLoadUsers();
}

export async function adminLoadUsers() {
    const tbody = document.getElementById("adminUsersTbody");
    if (!tbody) return;

    let users = [];
    try {
        const resp = await fetch("/api/admin/users");
        if (!resp.ok) throw new Error("Failed to load accounts");
        users = await resp.json();
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="4" class="maps-empty">Failed to load accounts</td></tr>`;
        return;
    }

    tbody.innerHTML = users.length
        ? users.map(u => `<tr>
                <td>${escapeHtml(u.email)}</td>
                <td>
                    <select data-role-user="${u.id}">
                        <option value="user" ${u.role === "user" ? "selected" : ""}>User</option>
                        <option value="admin" ${u.role === "admin" ? "selected" : ""}>Admin</option>
                    </select>
                </td>
                <td>${escapeHtml((u.created_at || "—").replace("T", " "))}</td>
                <td class="maps-proxy-actions" style="margin-top:0">
                    <button type="button" class="maps-row-btn" data-reset-user="${u.id}" data-reset-email="${escapeHtml(u.email)}">Reset password</button>
                    <button type="button" class="maps-row-btn" data-delete-user="${u.id}" data-delete-email="${escapeHtml(u.email)}">Delete</button>
                </td>
            </tr>`).join("")
        : `<tr><td colspan="4" class="maps-empty">No accounts yet.</td></tr>`;
}

export async function adminCreateUser() {
    const emailInput = document.getElementById("adminNewEmail");
    const roleSelect = document.getElementById("adminNewRole");
    if (!emailInput || !roleSelect) return;

    const email = emailInput.value.trim();
    const role = roleSelect.value;
    if (!email) {
        showToast("Enter an email", "warn");
        return;
    }

    try {
        const resp = await fetch("/api/admin/users", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, role }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            showToast(data.error || "Failed to create account", "error");
            return;
        }
        emailInput.value = "";
        roleSelect.value = "user";
        adminOpenPasswordModal(data.email, data.password);
        await adminLoadUsers();
    } catch (e) {
        showToast("Network error: " + e.message, "error");
    }
}

async function adminChangeRole(userId, role) {
    try {
        const resp = await fetch(`/api/admin/users/${userId}/role`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ role }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            showToast(data.error || "Failed to change role", "error");
            await adminLoadUsers(); // revert the <select> to the real value
            return;
        }
        showToast("Role updated");
    } catch (e) {
        showToast("Network error: " + e.message, "error");
        await adminLoadUsers();
    }
}

async function adminResetPassword(userId, email) {
    try {
        const resp = await fetch(`/api/admin/users/${userId}/reset-password`, { method: "POST" });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            showToast(data.error || "Failed to reset password", "error");
            return;
        }
        adminOpenPasswordModal(email, data.password);
    } catch (e) {
        showToast("Network error: " + e.message, "error");
    }
}

async function adminDeleteUser(userId, email) {
    if (!confirm(`Delete the account ${email}? This cannot be undone.`)) return;
    try {
        const resp = await fetch(`/api/admin/users/${userId}`, { method: "DELETE" });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            showToast(data.error || "Failed to delete account", "error");
            return;
        }
        showToast("Account deleted");
        await adminLoadUsers();
    } catch (e) {
        showToast("Network error: " + e.message, "error");
    }
}

function adminOpenPasswordModal(email, password) {
    adminModalPassword = password;
    document.getElementById("adminModalEmail").value = email;
    document.getElementById("adminModalPassword").value = password;
    document.getElementById("adminPasswordModal").classList.add("active");
}

export function adminCloseModal() {
    const modal = document.getElementById("adminPasswordModal");
    if (modal) modal.classList.remove("active");
    const passwordField = document.getElementById("adminModalPassword");
    if (passwordField) passwordField.value = "";
    adminModalPassword = "";
}

export async function adminCopyPassword() {
    if (!adminModalPassword) return;
    try {
        await navigator.clipboard.writeText(adminModalPassword);
        showToast("Password copied");
    } catch (e) {
        showToast("Copy failed — the browser blocked clipboard access", "error");
    }
}

export function bindAdminTab() {
    const tbody = document.getElementById("adminUsersTbody");
    if (tbody) {
        tbody.addEventListener("change", (e) => {
            const select = e.target.closest("[data-role-user]");
            if (select) adminChangeRole(Number(select.dataset.roleUser), select.value);
        });
        tbody.addEventListener("click", (e) => {
            const resetBtn = e.target.closest("[data-reset-user]");
            if (resetBtn) {
                adminResetPassword(Number(resetBtn.dataset.resetUser), resetBtn.dataset.resetEmail);
                return;
            }
            const deleteBtn = e.target.closest("[data-delete-user]");
            if (deleteBtn) adminDeleteUser(Number(deleteBtn.dataset.deleteUser), deleteBtn.dataset.deleteEmail);
        });
    }

    const modal = document.getElementById("adminPasswordModal");
    if (modal) {
        modal.addEventListener("click", (e) => {
            if (e.target === modal) adminCloseModal();
        });
    }
}
