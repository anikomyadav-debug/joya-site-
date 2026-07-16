/* ═══════════════════════════════════════════════════════════════════════
   JOYA Mark XXXIX — Auth JavaScript
   Login, Registration, and Session Management
   ═══════════════════════════════════════════════════════════════════════ */

'use strict';

const AUTH_API = {
  login: '/api/login',
  register: '/api/register',
  logout: '/api/logout',
  me: '/api/me',
};


// ── Form Submission Handler ──────────────────────────────────────────
async function submitAuth(formEl, endpoint) {
  const formData = new FormData(formEl);
  const data = Object.fromEntries(formData.entries());
  const errorBox = formEl.querySelector('.login-error, .form-error');
  const submitBtn = formEl.querySelector('button[type="submit"], .login-btn');

  // Reset error
  if (errorBox) { errorBox.style.display = 'none'; errorBox.textContent = ''; }

  // Loading state
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.dataset.originalText = submitBtn.textContent;
    submitBtn.textContent = 'Processing...';
  }

  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });

    const result = await res.json();

    if (res.ok && result.ok) {
      // Success — redirect
      if (typeof showToast === 'function') showToast('Welcome back!', 'success');
      setTimeout(() => {
        window.location.href = result.redirect || '/';
      }, 300);
      return true;
    } else {
      // Error
      const msg = result.error || result.message || 'Something went wrong';
      if (errorBox) {
        errorBox.textContent = msg;
        errorBox.style.display = 'block';
      }
      return false;
    }
  } catch (err) {
    if (errorBox) {
      errorBox.textContent = 'Network error. Please check your connection.';
      errorBox.style.display = 'block';
    }
    return false;
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = submitBtn.dataset.originalText || 'Submit';
    }
  }
}


// ── Logout ───────────────────────────────────────────────────────────
async function logout() {
  try {
    await fetch(AUTH_API.logout, { method: 'POST' });
  } catch (e) {
    // Silent
  }
  window.location.href = '/login.html';
}


// ── Password Visibility Toggle ───────────────────────────────────────
function togglePasswordVisibility(inputId, toggleEl) {
  const input = document.getElementById(inputId);
  if (!input) return;
  if (input.type === 'password') {
    input.type = 'text';
    if (toggleEl) toggleEl.textContent = '🙈';
  } else {
    input.type = 'password';
    if (toggleEl) toggleEl.textContent = '👁️';
  }
}


// ── Input Validation ─────────────────────────────────────────────────
function validateEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function validatePassword(password) {
  return password.length >= 6;
}


// ── Auto-attach login/register forms ─────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const loginForm = document.getElementById('loginForm');
  if (loginForm) {
    loginForm.addEventListener('submit', (e) => {
      e.preventDefault();
      submitAuth(loginForm, AUTH_API.login);
    });
  }

  const registerForm = document.getElementById('registerForm');
  if (registerForm) {
    registerForm.addEventListener('submit', (e) => {
      e.preventDefault();
      submitAuth(registerForm, AUTH_API.register);
    });
  }
});
