
const $ = id => document.getElementById(id);

async function getSettings() {
  return chrome.storage.sync.get({
    backendUrl: "http://127.0.0.1:8000",
    maxJobs: 20
  });
}

async function saveSettings() {
  const backendUrl = $("backendUrl").value.trim().replace(/\/$/, "");
  const maxJobs = Math.max(1, Number($("maxJobs").value) || 20);
  await chrome.storage.sync.set({ backendUrl, maxJobs });
  return { backendUrl, maxJobs };
}

async function renderProgress() {
  const { scanProgress = {
    current: 0, total: 0, saved: 0, priority: 0, status: "Ready"
  }} = await chrome.storage.local.get("scanProgress");

  $("status").textContent = scanProgress.status || "Ready";
  $("bar").style.width = scanProgress.total
    ? `${Math.round((scanProgress.current / scanProgress.total) * 100)}%`
    : "0%";
  $("summary").textContent =
    `Saved: ${scanProgress.saved || 0} · Under 100: ${scanProgress.priority || 0}`;
}

$("start").onclick = async () => {
  const settings = await saveSettings();
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  await chrome.storage.local.set({
    scanProgress: {
      current: 0, total: 0, saved: 0, priority: 0, status: "Starting scan…"
    }
  });

  chrome.tabs.sendMessage(tab.id, {
    type: "START_SCAN",
    backendUrl: settings.backendUrl,
    maxJobs: settings.maxJobs
  }, response => {
    if (chrome.runtime.lastError) {
      $("status").textContent = "Reload the LinkedIn search-results page, then retry.";
      return;
    }
    $("status").textContent = response?.ok
      ? "Scan started"
      : (response?.error || "Could not start scan");
  });
};

$("stop").onclick = async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  chrome.tabs.sendMessage(tab.id, { type: "STOP_SCAN" });
};

$("recordApplication").onclick = async () => {
  const settings = await saveSettings();
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  $("status").textContent = "Recording selected application…";
  chrome.tabs.sendMessage(tab.id, {
    type: "RECORD_SELECTED_APPLICATION",
    backendUrl: settings.backendUrl
  }, response => {
    if (chrome.runtime.lastError) {
      $("status").textContent = "Open a LinkedIn job page, then retry.";
      return;
    }
    if (!response?.ok) {
      $("status").textContent = response?.error || "Could not record the application.";
      return;
    }
    $("status").textContent = response.created
      ? "Application recorded."
      : "This application was already recorded.";
  });
};

$("dashboard").onclick = async () => {
  const settings = await getSettings();
  chrome.tabs.create({ url: settings.backendUrl });
};

$("backendUrl").onchange = saveSettings;
$("maxJobs").onchange = saveSettings;

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.scanProgress) renderProgress();
});

(async () => {
  const settings = await getSettings();
  $("backendUrl").value = settings.backendUrl;
  $("maxJobs").value = settings.maxJobs;
  renderProgress();
})();
