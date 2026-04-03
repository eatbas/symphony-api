import { getMusicians, getWorkspaceInput } from "/static/request.js";

const testStoryEl = document.getElementById("test-story");
const testQAList = document.getElementById("test-qa-list");
const testModelGrid = document.getElementById("test-model-grid");
const testResultsBody = document.getElementById("test-results-body");
const testMetaEl = document.getElementById("test-meta");
const runTestBtn = document.getElementById("run-test-btn");
const addQABtn = document.getElementById("add-qa-btn");
const generateAllBtn = document.getElementById("generate-all-btn");
const generateModelSelect = document.getElementById("generate-model-select");
const selectAll = document.getElementById("test-select-all");

let qaCounter = 0;
let generateController = null;
const generateBtnDefaultHTML = generateAllBtn.innerHTML;

function setTestMeta(msg, isError = false) {
  testMetaEl.textContent = msg;
  testMetaEl.className = isError ? "meta error" : "meta";
}

function clearTestResults() { testResultsBody.innerHTML = ""; }

export function addQAPair(question = "", expected = "") {
  qaCounter += 1;
  const div = document.createElement("div");
  div.className = "test-qa-row";
  div.innerHTML = `<div class="field"><label>Question ${qaCounter}</label><input type="text" class="test-question" value="${question.replaceAll('"', '&quot;')}"></div>
<div class="field"><label>Expected Keywords</label><input type="text" class="test-expected" value="${expected.replaceAll('"', '&quot;')}"></div>
<button type="button" class="qa-remove-btn">\u2715</button>`;
  div.querySelector(".qa-remove-btn").addEventListener("click", () => {
    div.remove();
    renumberQA();
  });
  testQAList.appendChild(div);
  renumberQA();
}

function renumberQA() {
  testQAList.querySelectorAll(".test-qa-row").forEach((row, i) => {
    row.querySelector("label").textContent = `Question ${i + 1}`;
  });
}

function getQAPairs() {
  const pairs = [];
  for (const row of testQAList.querySelectorAll(".test-qa-row")) {
    const q = row.querySelector(".test-question").value.trim();
    const kws = row
      .querySelector(".test-expected")
      .value.split(",")
      .map((k) => k.trim())
      .filter(Boolean);
    if (q && kws.length) pairs.push({ question: q, keywords: kws });
  }
  return pairs;
}

function syncSelectAllState() {
  const allModels = testModelGrid.querySelectorAll("input[data-provider][data-model]");
  selectAll.checked = [...allModels].every((cb) => cb.checked);
}

function syncProviderCheckbox(provider) {
  const models = testModelGrid.querySelectorAll(`input[data-provider="${provider}"][data-model]`);
  const header = testModelGrid.querySelector(`input.provider-toggle[data-provider="${provider}"]`);
  if (header) header.checked = [...models].every((cb) => cb.checked);
}

function toggleProvider(provider, checked) {
  for (const cb of testModelGrid.querySelectorAll(`input[data-provider="${provider}"][data-model]`)) {
    cb.checked = checked;
  }
  syncSelectAllState();
}

function getSelectedTestModels() {
  return [...testModelGrid.querySelectorAll("input[data-provider][data-model]:checked")]
    .map((cb) => ({ provider: cb.dataset.provider, model: cb.dataset.model }));
}

const SPINNER_HTML = '<span class="spinner"></span>';

function getOrCreateRow(provider, model) {
  const rowId = `test-row-${provider}-${model}`;
  let row = document.getElementById(rowId);
  if (!row) {
    row = document.createElement("tr");
    row.id = rowId;
    row.innerHTML = `<td>${testResultsBody.children.length + 1}</td><td>${provider}</td><td>${model}</td><td data-col="new">${SPINNER_HTML}</td><td data-col="resume">\u2014</td><td data-col="grade">\u2014</td>`;
    testResultsBody.appendChild(row);
  }
  return row;
}

function updateCell(row, col, value, className = "") {
  const cell = row.querySelector(`[data-col="${col}"]`);
  cell.textContent = value;
  cell.className = className;
}

async function waitForTerminalScore(scoreId) {
  for (;;) {
    const response = await fetch(`/v1/chat/${scoreId}`);
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    if (["completed", "failed", "stopped"].includes(body.status)) return body;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

async function submitAndWaitScore(payload) {
  const submitResponse = await fetch("/v1/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const accepted = await submitResponse.json();
  if (!submitResponse.ok) throw new Error(accepted.detail || `HTTP ${submitResponse.status}`);
  return waitForTerminalScore(accepted.score_id);
}

async function generateScenario() {
  if (generateController) {
    generateController.abort();
    generateController = null;
    generateAllBtn.innerHTML = generateBtnDefaultHTML;
    setTestMeta("Generation cancelled.");
    return;
  }

  generateController = new AbortController();
  generateAllBtn.innerHTML = "Cancel Generation";
  setTestMeta("Generating scenario...");
  try {
    const payload = { field: "all", workspace_path: getWorkspaceInput().value.trim() };
    const selected = generateModelSelect.value;
    if (selected !== "auto") {
      const [provider, ...modelParts] = selected.split("/");
      payload.provider = provider;
      payload.model = modelParts.join("/");
    }
    const response = await fetch("/v1/test/generate-scenario", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: generateController.signal,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    if (data.story) testStoryEl.value = data.story;
    if (Array.isArray(data.qa_pairs)) {
      testQAList.innerHTML = "";
      qaCounter = 0;
      for (const pair of data.qa_pairs) addQAPair(pair.question || "", pair.expected || "");
    }
    setTestMeta("Scenario generated.");
  } catch (error) {
    if (error.name === "AbortError") return;
    setTestMeta(error.message, true);
  } finally {
    generateController = null;
    generateAllBtn.innerHTML = generateBtnDefaultHTML;
  }
}

export function renderTestLabModels() {
  testModelGrid.innerHTML = "";
  const allMusicians = getMusicians();
  const groups = {};
  for (const musician of allMusicians) {
    groups[musician.provider] = groups[musician.provider] || [];
    groups[musician.provider].push(musician);
  }

  for (const [provider, models] of Object.entries(groups)) {
    const group = document.createElement("div");
    group.className = "provider-group";

    const header = document.createElement("div");
    header.className = "provider-group-header";
    header.innerHTML = `<input type="checkbox" class="provider-toggle" data-provider="${provider}" checked> ${provider}`;
    header.querySelector("input").addEventListener("change", (e) => {
      toggleProvider(provider, e.target.checked);
    });
    group.appendChild(header);

    const grid = document.createElement("div");
    grid.className = "provider-group-models";
    for (const model of models) {
      const id = `test-model-${provider}-${model.model}`;
      const label = document.createElement("label");
      label.innerHTML = `<input type="checkbox" id="${id}" data-provider="${provider}" data-model="${model.model}" checked> ${model.model}`;
      label.querySelector("input").addEventListener("change", () => {
        syncProviderCheckbox(provider);
        syncSelectAllState();
      });
      grid.appendChild(label);
    }
    group.appendChild(grid);
    testModelGrid.appendChild(group);
  }
  syncSelectAllState();

  generateModelSelect.innerHTML = '<option value="auto">Auto (cheapest)</option>';
  for (const m of allMusicians) {
    if (m.ready) {
      const opt = document.createElement("option");
      opt.value = `${m.provider}/${m.model}`;
      opt.textContent = `${m.provider} / ${m.model}`;
      generateModelSelect.appendChild(opt);
    }
  }
}

async function testSingleModel(selected, workspace, story, qaPairs) {
  const row = getOrCreateRow(selected.provider, selected.model);
  try {
    const newData = await submitAndWaitScore({
      provider: selected.provider,
      model: selected.model,
      workspace_path: workspace,
      mode: "new",
      prompt: story,
    });
    const newOk = newData.status === "completed" && newData.exit_code === 0;
    updateCell(row, "new", newOk ? "OK" : "FAIL", newOk ? "ok" : "error");
    let passed = 0;
    if (newOk && newData.provider_session_ref) {
      const resumeCell = row.querySelector('[data-col="resume"]');
      resumeCell.innerHTML = `<div class="resume-progress"><span>${SPINNER_HTML} 0/${qaPairs.length}</span><div class="resume-bar-track"><div class="resume-bar-fill" style="width:0%"></div></div></div>`;
      let completed = 0;
      for (const qa of qaPairs) {
        const resumeData = await submitAndWaitScore({
          provider: selected.provider,
          model: selected.model,
          workspace_path: workspace,
          mode: "resume",
          provider_session_ref: newData.provider_session_ref,
          prompt: qa.question,
        });
        const text = (resumeData.final_text || "").toLowerCase();
        if (
          resumeData.status === "completed"
          && resumeData.exit_code === 0
          && qa.keywords.every((kw) => text.includes(kw.toLowerCase()))
        ) {
          passed += 1;
        }
        completed += 1;
        const pct = Math.round((completed / qaPairs.length) * 100);
        const stillRunning = completed < qaPairs.length;
        resumeCell.innerHTML = `<div class="resume-progress"><span>${stillRunning ? SPINNER_HTML + ' ' : ''}${passed}/${qaPairs.length}</span><div class="resume-bar-track"><div class="resume-bar-fill" style="width:${pct}%"></div></div></div>`;
      }
    }
    const isPass = newOk && passed === qaPairs.length;
    row.querySelector('[data-col="grade"]').innerHTML = `<span class="${isPass ? "grade-pass" : "grade-fail"}">${isPass ? "PASS" : "FAIL"}</span>`;
    return isPass;
  } catch {
    updateCell(row, "new", "FAIL", "error");
    updateCell(row, "resume", "FAIL", "error");
    row.querySelector('[data-col="grade"]').innerHTML = '<span class="grade-fail">FAIL</span>';
    return false;
  }
}

export async function runTestLab() {
  const story = testStoryEl.value.trim();
  const qaPairs = getQAPairs();
  const selectedModels = getSelectedTestModels();
  const workspace = getWorkspaceInput().value.trim();
  if (!story) return setTestMeta("Story is required.", true);
  if (!qaPairs.length) return setTestMeta("Add at least one question with keywords.", true);
  if (!selectedModels.length) return setTestMeta("Select at least one model.", true);

  runTestBtn.disabled = true;
  clearTestResults();
  for (const s of selectedModels) getOrCreateRow(s.provider, s.model);
  setTestMeta(`Running ${selectedModels.length} models in parallel...`);
  let doneCount = 0;
  const promises = selectedModels.map((selected) =>
    testSingleModel(selected, workspace, story, qaPairs).then((pass) => {
      doneCount += 1;
      setTestMeta(`Progress: ${doneCount}/${selectedModels.length} models completed...`);
      return pass;
    }),
  );

  const results = await Promise.allSettled(promises);
  const passCount = results.filter((r) => r.status === "fulfilled" && r.value).length;
  setTestMeta(`Done: ${passCount}/${selectedModels.length} PASS.`);
  runTestBtn.disabled = false;
}

addQABtn.addEventListener("click", () => addQAPair());
generateAllBtn.addEventListener("click", generateScenario);
runTestBtn.addEventListener("click", runTestLab);
selectAll.addEventListener("change", (event) => {
  for (const cb of testModelGrid.querySelectorAll("input[data-provider][data-model]")) {
    cb.checked = event.target.checked;
  }
  for (const toggle of testModelGrid.querySelectorAll("input.provider-toggle")) {
    toggle.checked = event.target.checked;
  }
  syncSelectAllState();
});
