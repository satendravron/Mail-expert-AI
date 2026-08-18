/**
 * Mail Expert AI — In-Gmail Content Script Overlay
 * Automatically fetches triage scores from local server and injects
 * priority badges, AI summary tooltips, and Smart Reply links into Gmail DOM.
 */

(function () {
  const SERVER_URL = "http://127.0.0.1:8000";
  let classifiedEmailsMap = new Map();
  let isFetching = false;

  async function fetchClassifiedEmails() {
    if (isFetching) return;
    isFetching = true;
    try {
      const resp = await fetch(`${SERVER_URL}/api/emails`);
      if (resp.ok) {
        const data = await resp.json();
        const emails = data.emails || [];
        classifiedEmailsMap.clear();
        emails.forEach(e => {
          if (e.subject) {
            const key = cleanSubject(e.subject);
            classifiedEmailsMap.set(key, e);
          }
        });
      }
    } catch (err) {
      // Local server might be offline or initializing
    } finally {
      isFetching = false;
    }
  }

  function cleanSubject(subj) {
    if (!subj) return "";
    return subj.toLowerCase().replace(/^(re|fwd|fw):\s*/i, "").trim();
  }

  function createBadgeElement(emailData) {
    const wrapper = document.createElement("span");
    wrapper.className = "mail-expert-badge-wrapper";

    const tier = (emailData.importance || "low").toLowerCase();
    const score = (emailData.importance_score || 0).toFixed(2);
    const summary = emailData.summary || "No executive summary generated yet.";
    
    let deadlineHtml = "";
    if (emailData.extracted_dates && emailData.extracted_dates.length > 0) {
      const firstDate = emailData.extracted_dates[0];
      const label = firstDate.label || "Deadline";
      const dateStr = firstDate.raw_text || firstDate.datetime_utc || "";
      deadlineHtml = `
        <div class="mail-expert-tooltip-deadline">
          <span>⏰</span> <span>${escapeHtml(label)}: ${escapeHtml(dateStr)}</span>
        </div>
      `;
    }

    let repliedBadge = emailData.is_replied ? '<span style="color: #34d399; font-weight: 700; margin-left: 4px;">✅ Replied</span>' : '';

    wrapper.innerHTML = `
      <span class="mail-expert-badge ${tier}" title="Mail Expert AI Score: ${score}">
        ${tier.toUpperCase()} ${repliedBadge}
      </span>
      <div class="mail-expert-tooltip" onclick="event.stopPropagation()">
        <div class="mail-expert-tooltip-header">
          <span class="mail-expert-tooltip-title">📬 Mail Expert AI</span>
          <span class="mail-expert-tooltip-score">Score: ${score}</span>
        </div>
        <div class="mail-expert-tooltip-summary">
          <strong>Summary:</strong> ${escapeHtml(summary)}
        </div>
        ${deadlineHtml}
        <div class="mail-expert-tooltip-actions">
          <a href="${SERVER_URL}/" target="_blank" class="mail-expert-tooltip-btn mail-expert-btn-reply">✍️ Open Smart Reply</a>
          <a href="${SERVER_URL}/" target="_blank" class="mail-expert-tooltip-btn mail-expert-btn-dash">Dashboard ↗</a>
        </div>
      </div>
    `;

    return wrapper;
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function scanAndInjectGmail() {
    // Gmail email rows in primary view
    const rows = document.querySelectorAll("tr.zA");
    if (!rows || rows.length === 0) return;

    rows.forEach(row => {
      if (row.querySelector(".mail-expert-badge-wrapper")) {
        return; // Already injected
      }

      // Extract subject text from Gmail DOM row
      const subjectEl = row.querySelector(".y6 span, span.bog, .bog");
      if (!subjectEl) return;

      const rowSubject = cleanSubject(subjectEl.textContent);
      if (!rowSubject) return;

      // Find matching email in classifiedEmailsMap
      let matchedEmail = null;
      for (const [key, emailObj] of classifiedEmailsMap.entries()) {
        if (rowSubject.includes(key) || key.includes(rowSubject)) {
          matchedEmail = emailObj;
          break;
        }
      }

      if (matchedEmail) {
        const badgeEl = createBadgeElement(matchedEmail);
        subjectEl.parentElement.appendChild(badgeEl);
      }
    });
  }

  // Initial fetch and injection
  fetchClassifiedEmails().then(() => {
    scanAndInjectGmail();
  });

  // Periodically refresh data and scan DOM for changes (e.g. scrolling, filter tab changes)
  setInterval(fetchClassifiedEmails, 10000);
  setInterval(scanAndInjectGmail, 2000);

  // Use MutationObserver for instant DOM insertion
  const observer = new MutationObserver(() => {
    scanAndInjectGmail();
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true
  });
})();
