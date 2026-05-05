chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.sync.get(["apiUrl"], (items) => {
    if (!items.apiUrl) {
      chrome.storage.sync.set({ apiUrl: "https://your-api-url" });
    }
  });
});
