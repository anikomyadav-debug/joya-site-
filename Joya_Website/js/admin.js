/* ═══════════════════════════════════════════════════════════════════════
   JOYA Mark XXXIX — Admin Dashboard JavaScript
   User management, stats, and order tracking
   ═══════════════════════════════════════════════════════════════════════ */

'use strict';

const ADMIN_API = {
  users: '/api/admin/users',
  orders: '/api/admin/orders',
  stats: '/api/admin/stats',
  deleteUser: '/api/admin/delete-user',
  togglePro: '/api/admin/toggle-pro',
};


// ── Fetch and render admin stats ─────────────────────────────────────
async function loadAdminStats() {
  try {
    const res = await fetch(ADMIN_API.stats);
    if (!res.ok) return;
    const data = await res.json();

    const statsContainer = document.getElementById('adminStats');
    if (!statsContainer) return;

    const stats = [
      { label: 'Total Users', value: data.total_users || 0, icon: '👥' },
      { label: 'Pro Users', value: data.pro_users || 0, icon: '👑' },
      { label: 'Active Sessions', value: data.active_sessions || 0, icon: '🟢' },
      { label: 'Total Orders', value: data.total_orders || 0, icon: '📦' },
    ];

    statsContainer.innerHTML = stats.map(s => `
      <div class="stat-card">
        <div class="label">${s.icon} ${s.label}</div>
        <div class="value">${typeof formatNumber === 'function' ? formatNumber(s.value) : s.value}</div>
      </div>
    `).join('');
  } catch (e) {
    console.error('[Admin] Stats load error:', e);
  }
}


// ── Fetch and render users table ─────────────────────────────────────
async function loadUsers() {
  try {
    const res = await fetch(ADMIN_API.users);
    if (!res.ok) return;
    const data = await res.json();

    const tbody = document.getElementById('usersTableBody');
    if (!tbody) return;

    tbody.innerHTML = (data.users || []).map(u => `
      <tr>
        <td>${u.id}</td>
        <td>${u.name}</td>
        <td>${u.email}</td>
        <td>${u.phone || '—'}</td>
        <td>${u.is_admin ? '<span class="admin-badge-admin">Admin</span>' : ''}
            ${u.is_pro ? '<span class="admin-badge-pro">Pro</span>' : 'Free'}</td>
        <td>${u.login_count || 0}</td>
        <td>${u.created_at || '—'}</td>
        <td>
          <button onclick="togglePro(${u.id})" style="color:#d4af37;background:none;border:none;cursor:pointer;font-size:0.85rem;">
            ${u.is_pro ? 'Remove Pro' : 'Make Pro'}
          </button>
        </td>
      </tr>
    `).join('');
  } catch (e) {
    console.error('[Admin] Users load error:', e);
  }
}


// ── Toggle Pro status ────────────────────────────────────────────────
async function togglePro(userId) {
  try {
    const res = await fetch(ADMIN_API.togglePro, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId }),
    });
    if (res.ok) {
      if (typeof showToast === 'function') showToast('User updated', 'success');
      loadUsers();
      loadAdminStats();
    }
  } catch (e) {
    console.error('[Admin] Toggle pro error:', e);
  }
}


// ── Initialize admin dashboard ───────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadAdminStats();
  loadUsers();

  // Auto-refresh every 30 seconds
  setInterval(() => {
    loadAdminStats();
    loadUsers();
  }, 30000);
});
