/* ═══════════════════════════════════════════════════════
   Lumina — клиентские скрипты
   ═══════════════════════════════════════════════════════ */

/* ───── Toast ───── */
let _toastTimer = null;

function toast(msg) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), 2500);
}

/* ───── Модалка оформления заказа ───── */
function openCheckout(data) {
  const modal = document.getElementById('checkout-modal');
  if (!modal) return;

  const planIdEl = document.getElementById('checkout-plan-id');
  const nameEl = document.getElementById('modal-plan-name');
  const priceEl = document.getElementById('modal-plan-price');
  const metaEl = document.getElementById('modal-plan-meta');

  if (planIdEl) planIdEl.value = data.planId || '';
  if (nameEl) nameEl.textContent = data.planName || '';
  if (priceEl) priceEl.textContent = data.planPrice || '';
  if (metaEl) {
    const days = data.planDays || '';
    const gb = data.planGb;
    const gbText = (gb === '0' || gb === 0) ? 'квота без лимита' : `${gb} ГБ квоты`;
    metaEl.textContent = `${days} дней · ${gbText}`;
  }

  modal.hidden = false;
  document.body.classList.add('modal-open');

  const email = modal.querySelector('input[name="email"]');
  if (email) setTimeout(() => email.focus(), 60);
}

function closeCheckout() {
  const modal = document.getElementById('checkout-modal');
  if (!modal || modal.hidden) return;
  modal.hidden = true;
  if (renewClosed()) document.body.classList.remove('modal-open');
}

function openRenew(id, name) {
  const modal = document.getElementById('renew-modal');
  if (!modal) return;
  const idEl = document.getElementById('renew-subscription-id');
  const nameEl = document.getElementById('renew-sub-name');
  if (idEl) idEl.value = id || '';
  if (nameEl) nameEl.textContent = name || 'подписке';
  modal.hidden = false;
  document.body.classList.add('modal-open');
}

function closeRenew() {
  const modal = document.getElementById('renew-modal');
  if (!modal || modal.hidden) return;
  modal.hidden = true;
  const checkout = document.getElementById('checkout-modal');
  if (!checkout || checkout.hidden) document.body.classList.remove('modal-open');
}

function renewClosed() {
  const modal = document.getElementById('renew-modal');
  return !modal || modal.hidden;
}

/* Esc — закрыть модалку */
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    closeCheckout();
    closeRenew();
  }
});

/* Клик по фону — закрыть */
document.addEventListener('click', (e) => {
  const modal = document.getElementById('checkout-modal');
  if (modal && !modal.hidden && e.target === modal) closeCheckout();
  const renew = document.getElementById('renew-modal');
  if (renew && !renew.hidden && e.target === renew) closeRenew();
});

/* Кнопки «Купить» на карточках тарифов */
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.plan-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      openCheckout({
        planId:    btn.dataset.planId,
        planName:  btn.dataset.planName,
        planPrice: btn.dataset.planPrice,
        planDays:  btn.dataset.planDays,
        planGb:    btn.dataset.planGb,
      });
    });
  });

  document.querySelectorAll('.renew-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      openRenew(btn.dataset.subscriptionId, btn.dataset.subscriptionName);
    });
  });
});

/* ───── Копирование ссылки подписки ───── */
async function copySub(elementId) {
  const input = document.getElementById(elementId || 'sub-url');
  if (!input) return;
  try {
    await navigator.clipboard.writeText(input.value);
    toast('Ключ скопирован');
  } catch {
    // Фолбэк для HTTP и старых браузеров
    input.select();
    input.setSelectionRange(0, 99999);
    try {
      document.execCommand('copy');
      toast('Ключ скопирован');
    } catch {
      toast('Не удалось скопировать — выделите вручную');
    }
  }
}

/* ───── Плавная прокрутка по якорям ───── */
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('a[href^="#"]').forEach((a) => {
    a.addEventListener('click', (e) => {
      const href = a.getAttribute('href');
      if (!href || href.length < 2) return;
      const target = document.querySelector(href);
      if (!target) return;
      e.preventDefault();
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
});

/* ───── Появление блоков при скролле ───── */
if ('IntersectionObserver' in window) {
  const io = new IntersectionObserver(
    (entries) => {
      entries.forEach((en) => {
        if (en.isIntersecting) {
          en.target.classList.add('reveal');
          io.unobserve(en.target);
        }
      });
    },
    { threshold: 0.1 }
  );

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.section, .plan, .feature').forEach((el) => io.observe(el));
  });
} else {
  // Браузеры без IntersectionObserver — просто показываем всё
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.section, .plan, .feature').forEach((el) => {
      el.classList.add('reveal');
    });
  });
}

/* ───── Утилита: копирование любой строки (на будущее) ───── */
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Скопировано');
  } catch {
    toast('Не удалось скопировать');
  }
}