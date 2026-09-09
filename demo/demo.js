/**
 * Same-origin by construction: this page is served by the API that audits for
 * it, so there is no base URL to configure and CORS never enters into it.
 */
const $ = (id) => document.getElementById(id);

/**
 * SECURITY — everything rendered below originates from an audited website.
 * An audited site is untrusted by definition and, here, actively adversarial:
 * we are pointing at its deceptive design. A site operator who wanted to
 * attack an auditor would put markup in a button label. So: build nodes and
 * set textContent. Never innerHTML with captured content.
 */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const TIERS = ["provable", "corroborated", "indicative"];

function renderFindings(violations) {
  const box = $("findings");
  box.replaceChildren();

  const counts = {provable: 0, corroborated: 0, indicative: 0};
  violations.forEach((v) => {
    const tier = (v.evidence && v.evidence.evidence_tier) || "indicative";
    if (counts[tier] !== undefined) counts[tier]++;
  });
  $("k-total").textContent = violations.length;
  $("k-prov").textContent = counts.provable;
  $("k-corr").textContent = counts.corroborated;
  $("k-ind").textContent = counts.indicative;

  if (!violations.length) {
    const ok = el("div", "empty");
    ok.append(
      "No findings on this storefront. That is the correct result for the compliant control — "
      + "a tool that only ever finds violations, and never clears a clean page, is not measuring anything."
    );
    box.append(ok);
    return;
  }

  violations.forEach((v) => {
    const tier = (v.evidence && v.evidence.evidence_tier) || "indicative";
    const card = el("div", "finding");
    const top = el("div", "top");
    top.append(
      el("span", "code", v.pattern_code),
      el("span", "name", v.pattern_name),
      el("span", "tier tier-" + (TIERS.includes(tier) ? tier : "indicative"), tier),
      el("span", "step", v.step_name + " · " + Math.round(v.confidence * 100) + "%")
    );
    card.append(top, el("p", null, v.explanation));

    // The evidence object is the point of the whole tool: a reader can check
    // the claim without trusting the tool. Showing it is not a debug affordance.
    if (v.evidence && Object.keys(v.evidence).length) {
      const shown = Object.assign({}, v.evidence);
      delete shown.evidence_tier;   // already shown as the badge above
      if (Object.keys(shown).length) {
        card.append(el("div", "evidence", JSON.stringify(shown, null, 1)));
      }
    }
    box.append(card);
  });
}

function setStatus(text, isError) {
  const node = $("status");
  node.textContent = text;
  node.classList.toggle("is-error", Boolean(isError));
}

function setRunning(on) {
  $("progress").classList.toggle("on", on);
  $("run").disabled = on;
}

async function runAudit() {
  const adapter = $("target").value;
  setRunning(true);
  setStatus("Opening a browser\u2026", false);
  $("findings").replaceChildren(el("div", "empty", "Walking the checkout funnel\u2026"));

  try {
    // Read the status before the body. A 429 is valid JSON, so calling
    // .json() blindly turned "you are going too fast" into an object that
    // failed the id check below and surfaced as "Could not start the audit" --
    // true, but useless to someone who only had to wait a minute.
    const response = await fetch("/demo-audit", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({adapter_name: adapter}),
    });
    if (response.status === 429) {
      throw new Error("Too many demo runs from this address \u2014 they are "
                      + "limited to a few per minute. Wait a moment and try again.");
    }
    const created = await response.json().catch(() => null);

    if (!created || created.id === undefined) {
      throw new Error(created && created.detail ? created.detail : "Could not start the audit.");
    }

    const started = Date.now();
    const poll = setInterval(async () => {
      // fetch() only rejects on a NETWORK failure -- the server going away,
      // the wifi dropping. An HTTP error still resolves, which is why both
      // are handled here. Either one escaping this callback would reject
      // inside setInterval, where nothing catches it and nothing clears the
      // interval: the page would sit at "Walking the funnel... 47s" with the
      // button disabled until it was reloaded.
      let job;
      try {
        const pollResponse = await fetch("/demo-audit/" + created.id);
        if (!pollResponse.ok) {
          clearInterval(poll);
          setRunning(false);
          setStatus(`Lost contact with the server (${pollResponse.status}).`, true);
          return;
        }
        job = await pollResponse.json();
      } catch (pollErr) {
        clearInterval(poll);
        setRunning(false);
        setStatus("Lost contact with the server \u2014 is it still running in "
                  + "the terminal?", true);
        return;
      }
      const seconds = Math.round((Date.now() - started) / 1000);
      if (job.status === "running") setStatus(`Walking the funnel\u2026 ${seconds}s`, false);

      if (job.status === "done" || job.status === "failed") {
        clearInterval(poll);
        setRunning(false);
        // Show WHAT failed, not just that it did. error_message already
        // carries the exception type and job id -- the server deliberately
        // withholds the traceback (it can contain a DB password) but the type
        // is safe and is usually enough to name the cause. Telling someone to
        // "see server logs" when the reason is one field away in the response
        // they already have is a dead end, especially for a teammate running
        // this for the first time who has no idea where those logs are.
        if (job.status === "done") {
          setStatus(`Done in ${seconds}s`, false);
        } else {
          setStatus(job.error_message
            || "Failed \u2014 see the terminal running the server", true);
        }
        renderFindings(job.violations || []);
      }
      // A funnel walk with two reloads takes ~10s; 90s means something is
      // genuinely wrong and the page should say so rather than spin forever.
      if (seconds > 90) {
        clearInterval(poll);
        setRunning(false);
        setStatus("Timed out after 90s \u2014 check the terminal running the server", true);
      }
    }, 900);
  } catch (err) {
    setRunning(false);
    setStatus(String(err.message || err), true);
    $("findings").replaceChildren(el("div", "empty", String(err.message || err)));
  }
}

$("run").addEventListener("click", runAudit);
$("target").addEventListener("change", () => {
  const dark = $("target").value === "hosted_dark_demo";
  const path = dark ? "/storefront/cart.html" : "/storefront-clean/cart.html";
  $("frame").src = path;
  $("frame-label").textContent = dark ? "ShopMart checkout" : "HonestCart checkout";
  // The address field shows the real URL being framed, so the panel cannot
  // drift into looking like a mockup of a shop rather than a shop.
  $("frame-url").textContent = location.host + path;
});

$("frame-url").textContent = location.host + "/storefront/cart.html";
