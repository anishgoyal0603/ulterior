/**
 * SECURITY — stored XSS defence (higher severity than the dashboard).
 *
 * Flag details contain VERBATIM TEXT FROM WHATEVER PAGE THE USER VISITED.
 * A malicious site can put an HTML payload in a decline-button label.
 * Rendering that with innerHTML would execute attacker script inside the
 * EXTENSION's context — which has access to chrome.storage and, depending on
 * permissions, cross-origin data. That is a materially worse outcome than
 * XSS in an ordinary page.
 *
 * Fix: build DOM nodes and assign via textContent, which cannot execute
 * markup. No innerHTML is used with untrusted data anywhere in this file.
 */

chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  const container = document.getElementById("flags");
  container.replaceChildren();

  let hostname;
  try {
    hostname = new URL(tabs[0].url).hostname;
  } catch (e) {
    const msg = document.createElement("div");
    msg.className = "empty";
    msg.textContent = "No page to analyse.";
    container.appendChild(msg);
    return;
  }

  const key = `flags_${hostname}`;
  chrome.storage.local.get([key], (result) => {
    const flags = Array.isArray(result[key]) ? result[key] : [];

    if (flags.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "No dark patterns detected on this page.";
      container.appendChild(empty);
      return;
    }

    for (const f of flags) {
      const wrap = document.createElement("div");
      wrap.className = "flag";

      const code = document.createElement("span");
      code.className = "code";
      code.textContent = String(f.pattern ?? "");
      wrap.appendChild(code);

      // textContent on every untrusted value: pattern name and detail both
      // originate from page content.
      wrap.appendChild(document.createTextNode(" " + String(f.name ?? "")));
      wrap.appendChild(document.createElement("br"));
      wrap.appendChild(document.createTextNode(String(f.detail ?? "")));

      container.appendChild(wrap);
    }
  });
});
