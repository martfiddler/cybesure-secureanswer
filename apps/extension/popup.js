const apiUrlInput = document.getElementById("apiUrl");
const tokenInput = document.getElementById("token");
const saveButton = document.getElementById("save");
const fillButton = document.getElementById("fill");
const status = document.getElementById("status");

chrome.storage.sync.get(["apiUrl", "token"], (settings) => {
  apiUrlInput.value = settings.apiUrl || "http://localhost:4000";
  tokenInput.value = settings.token || "";
});

saveButton.addEventListener("click", () => {
  chrome.storage.sync.set(
    {
      apiUrl: apiUrlInput.value.trim(),
      token: tokenInput.value.trim()
    },
    () => {
      status.textContent = "Settings saved.";
    }
  );
});

fillButton.addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return;

  chrome.tabs.sendMessage(tab.id, { type: "CYBERSURE_AUTOFILL" }, (response) => {
    status.textContent = response?.ok ? "Autofill complete." : response?.error || "Autofill failed.";
  });
});
