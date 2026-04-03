const form = document.getElementById("chat-form");
const providerSelect = document.getElementById("provider");
const modelSelect = document.getElementById("model");
const modeSelect = document.getElementById("mode");
const sessionInput = document.getElementById("provider_session_ref");
const workspaceInput = document.getElementById("workspace_path");
const promptInput = document.getElementById("prompt");
const consoleEl = document.getElementById("console");
const sessionRefEl = document.getElementById("session-ref");
const eventCountEl = document.getElementById("event-count");
const healthStatusEl = document.getElementById("health-status");
const shellPathEl = document.getElementById("shell-path");
const bashVersionEl = document.getElementById("bash-version");
const musicianCountEl = document.getElementById("musician-count");
const musicianListEl = document.getElementById("musician-list");
const requestMetaEl = document.getElementById("request-meta");
const sendButton = document.getElementById("send-button");
const refreshButton = document.getElementById("refresh-button");
const checkUpdatesButton = document.getElementById("check-updates-button");
const versionGridEl = document.getElementById("version-grid");
const versionMetaEl = document.getElementById("version-meta");

let musicians = [];
let eventCount = 0;

function writeConsole(line = "") { consoleEl.textContent += `${line}\n`; consoleEl.scrollTop = consoleEl.scrollHeight; }
function resetConsole() { consoleEl.textContent = ""; eventCount = 0; eventCountEl.textContent = "0"; sessionRefEl.textContent = "none"; }
function setMeta(message, isError = false) { requestMetaEl.textContent = message; requestMetaEl.className = isError ? "meta error" : "meta"; }
function updateSessionVisibility() { sessionInput.disabled = modeSelect.value !== "resume"; }
function modelsForProvider(provider) { return musicians.filter((m) => m.provider === provider).map((m) => m.model); }
function websocketUrl(scoreId) {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/v1/chat/${scoreId}/ws`;
}

function renderModelOptions() {
  const current = providerSelect.value;
  modelSelect.innerHTML = "";
  for (const model of modelsForProvider(current)) {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = model;
    modelSelect.appendChild(option);
  }
}

function renderMusicians() {
  musicianListEl.innerHTML = "";
  const groups = {};
  for (const m of musicians) {
    groups[m.provider] = groups[m.provider] || [];
    groups[m.provider].push(m);
  }
  for (const [provider, models] of Object.entries(groups)) {
    const group = document.createElement("div");
    group.className = "musician-group";
    group.innerHTML = `<div class="musician-group-header">${provider} <span class="musician-group-count">(${models.length})</span></div>`;
    const items = document.createElement("div");
    items.className = "musician-group-items";
    for (const m of models) {
      const chip = document.createElement("div");
      chip.className = "musician-chip";
      const statusClass = m.ready ? "ok" : "error";
      chip.innerHTML = `<strong>${m.model}</strong><span class="musician-status ${statusClass}">${m.ready ? "ready" : "down"} · ${m.busy ? "busy" : "idle"} · q=${m.queue_length}</span>`;
      items.appendChild(chip);
    }
    group.appendChild(items);
    musicianListEl.appendChild(group);
  }
}

async function fetchScore(scoreId) {
  const response = await fetch(`/v1/chat/${scoreId}`);
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || JSON.stringify(body));
  return body;
}

async function waitForTerminalScore(scoreId, onSnapshot) {
  for (;;) {
    const snapshot = await fetchScore(scoreId);
    onSnapshot(snapshot);
    if (["completed", "failed", "stopped"].includes(snapshot.status)) {
      return snapshot;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

function attachScoreSocket(scoreId, onMessage) {
  return new Promise((resolve) => {
    const socket = new WebSocket(websocketUrl(scoreId));
    socket.addEventListener("open", () => resolve(socket));
    socket.addEventListener("message", (event) => {
      try {
        onMessage(JSON.parse(event.data));
      } catch (_) {}
    });
    socket.addEventListener("error", () => resolve(socket));
    socket.addEventListener("close", () => resolve(socket));
  });
}

function applySessionRef(sessionRef) {
  if (!sessionRef) return;
  sessionRefEl.textContent = sessionRef;
  sessionInput.value = sessionRef;
}

function buildVersionCard(v) {
  const card = document.createElement("div");
  card.className = "version-card";
  card.dataset.provider = v.provider;
  const btnDisabled = !v.needs_update;
  card.innerHTML = `<div class="version-row"><strong>${v.provider}</strong><span>${v.needs_update ? "update available" : "up to date"}</span></div>
<div class="version-row"><span>Installed</span><span>${v.current_version || "—"}</span></div>
<div class="version-row"><span>Latest</span><span>${v.latest_version || "—"}</span></div>
<div class="version-row"><span>Next check</span><span>${v.next_check_at ? new Date(v.next_check_at).toLocaleTimeString() : "—"}</span></div>
<button class="version-update-btn" data-provider="${v.provider}" ${btnDisabled ? "disabled" : ""}>Update</button>`;
  card.querySelector(".version-update-btn").addEventListener("click", () => updateProvider(v.provider));
  return card;
}

function renderVersions(versions) {
  versionGridEl.innerHTML = "";
  if (!versions.length) { versionGridEl.innerHTML = '<span class="meta">No version data yet.</span>'; return; }
  for (const v of versions) versionGridEl.appendChild(buildVersionCard(v));
}

async function fetchVersions() { const response = await fetch("/v1/cli-versions"); return await response.json(); }

async function updateProvider(provider) {
  const card = versionGridEl.querySelector(`.version-card[data-provider="${provider}"]`);
  const btn = card?.querySelector(".version-update-btn");
  if (!card || !btn) return;

  btn.replaceWith(Object.assign(document.createElement("div"), {
    className: "version-progress", innerHTML: '<div class="version-progress-bar"></div>',
  }));
  const statusEl = document.createElement("div");
  statusEl.className = "version-status";
  statusEl.textContent = "Updating…";
  card.appendChild(statusEl);
  versionMetaEl.textContent = `Updating ${provider}…`;

  try {
    const response = await fetch(`/v1/cli-versions/${provider}/update`, { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Update failed");

    const bar = card.querySelector(".version-progress-bar");
    if (bar) { bar.style.animation = "none"; bar.style.width = "100%"; }
    statusEl.textContent = result.update_skipped_reason ? result.update_skipped_reason : "Done!";
    await new Promise((r) => setTimeout(r, 400));

    const newCard = buildVersionCard(result);
    card.replaceWith(newCard);
    versionMetaEl.textContent = result.update_skipped_reason
      ? `${provider}: ${result.update_skipped_reason}`
      : `${provider} updated to ${result.current_version || "latest"}.`;
  } catch (error) {
    const bar = card.querySelector(".version-progress-bar");
    if (bar) { bar.style.animation = "none"; bar.style.width = "100%"; bar.style.background = "var(--error)"; }
    statusEl.textContent = error.message;
    versionMetaEl.textContent = error.message;
  }
}

export async function refreshState() {
  const [healthResponse, musiciansResponse, providersResponse] = await Promise.all([fetch("/health"), fetch("/v1/musicians"), fetch("/v1/providers")]);
  const health = await healthResponse.json();
  musicians = await musiciansResponse.json();
  const providers = await providersResponse.json();

  healthStatusEl.textContent = health.status;
  shellPathEl.textContent = health.shell_path || "not detected";
  bashVersionEl.textContent = health.bash_version || "not detected";
  musicianCountEl.textContent = String(health.musician_count);

  if (!workspaceInput.value && health.config_path) {
    const parts = health.config_path.replace(/\\/g, "/").split("/");
    parts.pop();
    workspaceInput.value = parts.join("/") || "/";
  }

  const savedProvider = providerSelect.value;
  const savedModel = modelSelect.value;
  providerSelect.innerHTML = "";
  for (const provider of providers.filter((item) => item.enabled && item.available)) {
    const option = document.createElement("option");
    option.value = provider.provider;
    option.textContent = provider.provider;
    providerSelect.appendChild(option);
  }
  if (savedProvider) providerSelect.value = savedProvider;
  renderModelOptions();
  if (savedModel) modelSelect.value = savedModel;
  renderMusicians();
  updateSessionVisibility();

  try {
    const versions = await fetchVersions();
    renderVersions(versions);
    versionMetaEl.textContent = versions.length && versions[0].last_checked ? `Last checked: ${new Date(versions[0].last_checked).toLocaleString()}` : "Checked.";
  } catch (_) {}
}

export function getMusicians() { return musicians; }
export function getWorkspaceInput() { return workspaceInput; }

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  resetConsole();
  sendButton.disabled = true;
  setMeta("Submitting score...");

  const payload = {
    provider: providerSelect.value,
    model: modelSelect.value,
    workspace_path: workspaceInput.value.trim(),
    mode: modeSelect.value,
    prompt: promptInput.value,
  };
  if (payload.mode === "resume") payload.provider_session_ref = sessionInput.value.trim();

  try {
    sessionStorage.setItem("workspace_path", payload.workspace_path);
    const response = await fetch("/v1/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const accepted = await response.json();
    if (!response.ok) throw new Error(accepted.detail || JSON.stringify(accepted));

    writeConsole(`[accepted] score=${accepted.score_id} status=${accepted.status}`);
    let seenText = "";

    const socket = await attachScoreSocket(accepted.score_id, (message) => {
      const type = message.type || "unknown";
      eventCount += 1;
      eventCountEl.textContent = String(eventCount);
      if (type === "score_snapshot" && message.score) {
        applySessionRef(message.score.provider_session_ref);
        if (!seenText && message.score.accumulated_text) {
          seenText = message.score.accumulated_text;
          writeConsole(message.score.accumulated_text);
        }
        return;
      }
      if (type === "provider_session") {
        applySessionRef(message.provider_session_ref);
        return;
      }
      if (type === "output_delta" && message.text) {
        seenText = seenText ? `${seenText}\n${message.text}` : message.text;
        writeConsole(message.text);
        return;
      }
      writeConsole(`[${type}] ${JSON.stringify(message, null, 2)}`);
    });

    const terminal = await waitForTerminalScore(accepted.score_id, (snapshot) => {
      applySessionRef(snapshot.provider_session_ref);
      if (snapshot.accumulated_text && snapshot.accumulated_text.startsWith(seenText)) {
        const suffix = snapshot.accumulated_text.slice(seenText.length).replace(/^\n+/, "");
        if (suffix) {
          seenText = snapshot.accumulated_text;
          writeConsole(suffix);
        }
      } else if (!seenText && snapshot.accumulated_text) {
        seenText = snapshot.accumulated_text;
        writeConsole(snapshot.accumulated_text);
      }
    });

    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
      socket.close();
    }

    writeConsole(`[terminal] ${JSON.stringify(terminal, null, 2)}`);
    setMeta(`Score ${terminal.status}.`);
    await refreshState();
  } catch (error) {
    writeConsole(`[failed] ${error.message}`);
    setMeta(error.message, true);
  } finally {
    sendButton.disabled = false;
  }
});

refreshButton.addEventListener("click", async () => {
  refreshButton.disabled = true;
  try { await refreshState(); setMeta("State refreshed."); } catch (error) { setMeta(error.message, true); } finally { refreshButton.disabled = false; }
});

export async function checkAllVersions() {
  checkUpdatesButton.disabled = true;
  versionMetaEl.textContent = "Checking providers...";
  try {
    const providers = await (await fetch("/v1/providers")).json();
    const available = providers.filter((p) => p.enabled && p.available).map((p) => p.provider);
    const results = await Promise.all(available.map(async (provider) => (await (await fetch(`/v1/cli-versions/${provider}/check`, { method: "POST" })).json())));
    renderVersions(results.filter(Boolean));
    versionMetaEl.textContent = "Done.";
  } catch (error) {
    versionMetaEl.textContent = error.message;
  } finally {
    checkUpdatesButton.disabled = false;
  }
}

checkUpdatesButton.addEventListener("click", checkAllVersions);

providerSelect.addEventListener("change", renderModelOptions);
modeSelect.addEventListener("change", updateSessionVisibility);
