// Minimal service worker: keeps the toolbar badge in sync with how many
// flags the content script found on the active tab's page.
chrome.runtime.onMessage.addListener((message, sender) => {
  if (message.type === "FLAGS_UPDATED" && sender.tab) {
    chrome.action.setBadgeText({
      tabId: sender.tab.id,
      text: message.count > 0 ? String(message.count) : "",
    });
    chrome.action.setBadgeBackgroundColor({ tabId: sender.tab.id, color: "#e94560" });
  }
});
