// Usage panel: fetches /v1/usage and renders per-provider cards showing
// how much of the 5-hour and weekly windows have been consumed.

const usageGridEl = document.getElementById("usage-grid");
const usageMetaEl = document.getElementById("usage-meta");
const refreshButton = document.getElementById("refresh-usage-button");

function formatNumber(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return value.toLocaleString();
  return String(value);
}

function formatTimestamp(value) {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function windowLabel(window) {
  switch (window) {
    case "5h_rolling": return "Last 5 hours";
    case "weekly": return "Last 7 days";
    case "session": return "Current session";
    case "day": return "Last 24 hours";
    case "month": return "Last 30 days";
    case "rolling": return "Rolling window";
    case "unknown": return "Unknown window";
    default: return window || "—";
  }
}

function buildUsageCard(provider, snapshots) {
  const card = document.createElement("div");
  card.className = "version-card";
  card.dataset.provider = provider;

  const unsupported = snapshots.every((s) => !s.supported);
  if (unsupported) {
    const note = snapshots[0]?.note || "Usage not supported by this CLI.";
    card.innerHTML = `<div class="version-row"><strong>${provider}</strong><span>not supported</span></div>
<div class="version-row meta">${note}</div>`;
    return card;
  }

  const rows = [`<div class="version-row"><strong>${provider}</strong><span>${snapshots[0]?.source || "session_log"}</span></div>`];
  for (const snap of snapshots) {
    const used = snap.used || {};
    const limit = snap.limit || {};
    const remaining = snap.remaining || {};
    const pct = snap.percent_remaining != null
      ? `${snap.percent_remaining.toFixed(1)}% remaining`
      : null;
    rows.push(`<div class="version-row"><span>${windowLabel(snap.window)}</span><span>${pct || formatTimestamp(snap.as_of)}</span></div>`);
    rows.push(`<div class="version-row"><span>Input tokens</span><span>${formatNumber(used.input_tokens)}${limit.input_tokens != null ? ` / ${formatNumber(limit.input_tokens)}` : ""}</span></div>`);
    rows.push(`<div class="version-row"><span>Output tokens</span><span>${formatNumber(used.output_tokens)}${limit.output_tokens != null ? ` / ${formatNumber(limit.output_tokens)}` : ""}</span></div>`);
    rows.push(`<div class="version-row"><span>Requests</span><span>${formatNumber(used.requests)}${remaining.requests != null ? ` (${formatNumber(remaining.requests)} left)` : ""}</span></div>`);
    if (snap.resets_at) {
      rows.push(`<div class="version-row"><span>Resets</span><span>${formatTimestamp(snap.resets_at)}</span></div>`);
    }
    if (snap.note) {
      rows.push(`<div class="version-row meta">${snap.note}</div>`);
    }
  }
  card.innerHTML = rows.join("\n");
  return card;
}

function renderUsage(snapshots) {
  usageGridEl.innerHTML = "";
  if (!snapshots.length) {
    usageGridEl.innerHTML = '<span class="meta">No usage data yet.</span>';
    return;
  }
  const byProvider = {};
  for (const snap of snapshots) {
    if (!byProvider[snap.provider]) byProvider[snap.provider] = [];
    byProvider[snap.provider].push(snap);
  }
  for (const provider of Object.keys(byProvider).sort()) {
    usageGridEl.appendChild(buildUsageCard(provider, byProvider[provider]));
  }
}

async function fetchUsage({ refresh = false } = {}) {
  const url = refresh ? "/v1/usage/refresh" : "/v1/usage";
  const init = refresh ? { method: "POST" } : undefined;
  const response = await fetch(url, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${response.status}`);
  }
  return await response.json();
}

async function loadUsage({ refresh = false } = {}) {
  usageMetaEl.textContent = refresh ? "Refreshing…" : "Loading…";
  try {
    const snapshots = await fetchUsage({ refresh });
    renderUsage(snapshots);
    usageMetaEl.textContent = `Updated ${new Date().toLocaleTimeString()}.`;
  } catch (error) {
    usageMetaEl.textContent = `Failed: ${error.message}`;
  }
}

refreshButton.addEventListener("click", () => loadUsage({ refresh: true }));
window.addEventListener("load", () => loadUsage({ refresh: false }));
