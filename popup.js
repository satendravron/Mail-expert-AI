const API_BASE = "http://127.0.0.1:8000";
let soundEnabled = true;

// Web Audio API alarm sound synthesizer (no external audio assets required)
function playAlarmChime() {
  if (!soundEnabled) return;
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;
    const ctx = new AudioContext();
    
    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.type = "sine";
    osc.frequency.setValueAtTime(587.33, now); // D5 note
    osc.frequency.setValueAtTime(880.00, now + 0.15); // A5 note

    gain.gain.setValueAtTime(0.3, now);
    gain.gain.exponentialRampToValueAtTime(0.01, now + 0.5);

    osc.connect(gain);
    gain.connect(ctx.destination);

    osc.start(now);
    osc.stop(now + 0.5);
  } catch (err) {
    console.warn("Could not play alarm chime:", err);
  }
}

async function checkAuthStatus() {
  const dot = document.getElementById("auth-dot");
  const emailEl = document.getElementById("auth-email");
  const actionContainer = document.getElementById("auth-action-btn-container");

  try {
    const res = await fetch(`${API_BASE}/api/status/gmail`);
    const data = await res.json();

    if (data.connected) {
      dot.className = "dot connected";
      emailEl.textContent = data.email || "Gmail Connected";
      actionContainer.innerHTML = `<button id="btn-logout" class="btn btn-logout">Logout</button>`;
      document.getElementById("btn-logout").addEventListener("click", logoutGmail);
    } else {
      dot.className = "dot";
      emailEl.textContent = "Not Connected";
      actionContainer.innerHTML = `<button id="btn-login" class="btn btn-login">Login Gmail</button>`;
      document.getElementById("btn-login").addEventListener("click", () => {
        window.open(`${API_BASE}/auth/gmail`, "_blank");
      });
    }
  } catch (err) {
    dot.className = "dot";
    emailEl.textContent = "Server Offline";
    actionContainer.innerHTML = "";
  }
}

async function logoutGmail() {
  try {
    const res = await fetch(`${API_BASE}/auth/logout`, { method: "POST" });
    const data = await res.json();
    alert(data.message || "Logged out from Gmail.");
    checkAuthStatus();
    loadInbox();
  } catch (err) {
    alert(`Logout failed: ${err.message}`);
  }
}

async function loadAccounts() {
  const selectEl = document.getElementById("account-select");
  try {
    const res = await fetch(`${API_BASE}/api/accounts`);
    const data = await res.json();
    const accounts = data.accounts || [];

    if (accounts.length > 0) {
      selectEl.innerHTML = accounts.map(acc => {
        const selected = acc.is_active ? "selected" : "";
        return `<option value="${acc.id}" ${selected}>${escapeHtml(acc.account_name)} (${acc.provider.toUpperCase()})</option>`;
      }).join("");
    }
  } catch (err) {
    console.warn("Failed to fetch account list:", err);
  }
}

async function switchAccount(accountId) {
  try {
    await fetch(`${API_BASE}/api/accounts/switch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: accountId })
    });
    loadInbox();
  } catch (err) {
    console.warn("Account switch error:", err);
  }
}

async function checkDueReminders() {
  const bar = document.getElementById("notification-bar");
  const content = document.getElementById("notification-content");

  try {
    const res = await fetch(`${API_BASE}/api/reminders/due`);
    const data = await res.json();
    const due = data.due || [];

    if (due.length > 0) {
      bar.style.display = "block";
      content.innerHTML = due.map(d => `
        <div style="margin-bottom: 4px; font-weight:600;">
          🔔 <strong>${escapeHtml(d.title)}</strong> (${d.offset_minutes}m warning)
        </div>
      `).join("");

      playAlarmChime();

      // Trigger Web Notification if allowed
      if ("Notification" in window && Notification.permission === "granted") {
        new Notification("Mail Expert AI — Deadline Alarm", {
          body: due[0].title,
          icon: `${API_BASE}/icon192.png`
        });
      } else if ("Notification" in window && Notification.permission !== "denied") {
        Notification.requestPermission();
      }
    } else {
      bar.style.display = "none";
    }
  } catch (err) {
    // Silent catch if backend server isn't running
  }
}

async function loadInbox() {
  const content = document.getElementById("content");
  try {
    const res = await fetch(`${API_BASE}/inbox`);
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const emails = await res.json();

    if (!emails.length) {
      content.innerHTML = '<div class="empty">No emails found. Run <code>python seed_sample_emails.py</code> or sync Gmail.</div>';
      return;
    }

    const unread = emails.filter(e => !e.is_read).slice(0, 8);
    const toShow = unread.length ? unread : emails.slice(0, 8);

    content.innerHTML = toShow.map(e => {
      const tier = e.importance || "low";
      const dates = e.extracted_dates || [];
      const deadlineHtml = dates.length ? `<div class="deadline">⏳ ${dates[0].label}: ${dates[0].datetime_utc.replace('T', ' ').slice(0, 16)}</div>` : '';
      const summaryHtml = e.summary ? `<div class="summary">💡 ${escapeHtml(e.summary)}</div>` : '';
      const accountTag = e.account_label ? `<span class="acc-tag">${escapeHtml(e.account_label)}</span>` : '';

      return `
        <div class="card ${tier}">
          <div class="row">
            <div style="display: flex; gap: 4px; align-items: center;">
              <span class="tier ${tier}">${tier.toUpperCase()}</span>
              <span class="tag">${e.category}</span>
            </div>
            ${accountTag}
          </div>
          <div class="subject">${escapeHtml(e.subject)}</div>
          <div class="sender">From: ${escapeHtml(e.sender)}</div>
          ${summaryHtml}
          ${deadlineHtml}
        </div>
      `;
    }).join("");
  } catch (err) {
    content.innerHTML = `<div class="error">Can't connect to Mail Expert AI server.<br><br>
      Make sure <code>python start_app.py</code> or <code>uvicorn api:app</code> is running.<br><br>
      <small style="color: #94a3b8;">${err.message}</small></div>`;
  }
}

function escapeHtml(str) {
  if (!str) return "";
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// Event Listeners & Initializers
document.addEventListener("DOMContentLoaded", () => {
  const accountSelect = document.getElementById("account-select");
  const muteBtn = document.getElementById("btn-mute");

  if (accountSelect) {
    accountSelect.addEventListener("change", (e) => {
      switchAccount(e.target.value);
    });
  }

  if (muteBtn) {
    muteBtn.addEventListener("click", () => {
      soundEnabled = !soundEnabled;
      muteBtn.textContent = soundEnabled ? "🔊 Sound On" : "🔇 Sound Off";
    });
  }

  checkAuthStatus();
  loadAccounts();
  loadInbox();
  checkDueReminders();

  // Poll for due reminders every 30 seconds
  setInterval(checkDueReminders, 30000);
});
