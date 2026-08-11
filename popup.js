const API_BASE = "http://127.0.0.1:8000";

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

loadInbox();
