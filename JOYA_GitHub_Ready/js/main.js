/* ═══════════════════════════════════════════════════════════════════════
   JOYA Mark XXXIX — Main JavaScript
   Premium interactions, scroll animations, and utility functions
   ═══════════════════════════════════════════════════════════════════════ */

'use strict';

// ── Intersection Observer for scroll-triggered animations ────────────
document.addEventListener('DOMContentLoaded', () => {
  // Animate elements when they come into view
  const observerOptions = {
    root: null,
    rootMargin: '0px 0px -60px 0px',
    threshold: 0.1
  };

  const animateOnScroll = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');
        entry.target.style.opacity = '1';
        entry.target.style.transform = 'translateY(0)';
        animateOnScroll.unobserve(entry.target);
      }
    });
  }, observerOptions);

  // Observe all elements with data-animate attribute
  document.querySelectorAll('[data-animate]').forEach(el => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(30px)';
    el.style.transition = 'opacity 0.8s cubic-bezier(0.15, 1, 0.3, 1), transform 0.8s cubic-bezier(0.15, 1, 0.3, 1)';
    animateOnScroll.observe(el);
  });

  // Stagger children animations
  document.querySelectorAll('[data-stagger]').forEach(parent => {
    const children = parent.children;
    Array.from(children).forEach((child, i) => {
      child.style.transitionDelay = `${i * 100}ms`;
    });
  });
});


// ── Smooth scroll for anchor links ───────────────────────────────────
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
  anchor.addEventListener('click', function (e) {
    e.preventDefault();
    const target = document.querySelector(this.getAttribute('href'));
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  });
});


// ── Navbar scroll effect ─────────────────────────────────────────────
(() => {
  const nav = document.querySelector('.nav, nav, header');
  if (!nav) return;

  let lastScrollY = 0;
  let ticking = false;

  window.addEventListener('scroll', () => {
    lastScrollY = window.scrollY;
    if (!ticking) {
      window.requestAnimationFrame(() => {
        if (lastScrollY > 60) {
          nav.classList.add('nav-scrolled');
        } else {
          nav.classList.remove('nav-scrolled');
        }
        ticking = false;
      });
      ticking = true;
    }
  });
})();


// ── Copy to clipboard utility ────────────────────────────────────────
function copyToClipboard(text, feedbackEl) {
  navigator.clipboard.writeText(text).then(() => {
    if (feedbackEl) {
      const original = feedbackEl.textContent;
      feedbackEl.textContent = 'Copied!';
      setTimeout(() => { feedbackEl.textContent = original; }, 2000);
    }
  });
}


// ── Format numbers with commas ───────────────────────────────────────
function formatNumber(num) {
  return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}


// ── Debounce utility ─────────────────────────────────────────────────
function debounce(func, wait = 250) {
  let timeout;
  return function executedFunction(...args) {
    const later = () => {
      clearTimeout(timeout);
      func(...args);
    };
    clearTimeout(timeout);
    timeout = setTimeout(later, wait);
  };
}


// ── Toast notification ───────────────────────────────────────────────
function showToast(message, type = 'info', duration = 3000) {
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  toast.style.cssText = `
    position: fixed;
    bottom: 32px;
    right: 32px;
    padding: 14px 24px;
    background: rgba(11, 11, 13, 0.95);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 14px;
    color: #fff;
    font-size: 0.9rem;
    font-weight: 500;
    backdrop-filter: blur(20px);
    z-index: 10000;
    opacity: 0;
    transform: translateY(20px);
    transition: all 0.4s cubic-bezier(0.15, 1, 0.3, 1);
    box-shadow: 0 10px 40px rgba(0,0,0,0.5);
  `;

  if (type === 'success') toast.style.borderColor = 'rgba(48, 209, 88, 0.3)';
  if (type === 'error') toast.style.borderColor = 'rgba(255, 55, 95, 0.3)';

  document.body.appendChild(toast);

  requestAnimationFrame(() => {
    toast.style.opacity = '1';
    toast.style.transform = 'translateY(0)';
  });

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(20px)';
    setTimeout(() => toast.remove(), 400);
  }, duration);
}


// ── Parallax effect for hero sections ────────────────────────────────
(() => {
  const parallaxElements = document.querySelectorAll('[data-parallax]');
  if (!parallaxElements.length) return;

  window.addEventListener('scroll', () => {
    const scrollY = window.scrollY;
    parallaxElements.forEach(el => {
      const speed = parseFloat(el.dataset.parallax) || 0.3;
      el.style.transform = `translateY(${scrollY * speed}px)`;
    });
  }, { passive: true });
})();


// ── Console easter egg ───────────────────────────────────────────────
console.log(
  '%c⚡ JOYA Mark XXXIX %c Human AI Companion ',
  'background: linear-gradient(135deg, #6e6aff, #bf5af2); color: #fff; padding: 8px 16px; border-radius: 8px 0 0 8px; font-weight: 700; font-size: 14px;',
  'background: #18181c; color: #86868b; padding: 8px 16px; border-radius: 0 8px 8px 0; font-size: 14px;'
);
