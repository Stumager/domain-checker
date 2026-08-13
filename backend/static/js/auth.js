/* Session actions shared by the header bar. */

export async function authLogout() {
    try {
        await fetch("/api/auth/logout", { method: "POST" });
    } catch (e) {}
    window.location.reload();
}
