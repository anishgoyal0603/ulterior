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
    const safeTier = TIERS.includes(tier) ? tier : "indicative";
    const card = el("div", "finding is-" + safeTier);
    const top = el("div", "top");
    top.append(
      el("span", "code", v.pattern_code),
      el("span", "name", v.pattern_name),
      el("span", "tier tier-" + safeTier, tier)
    );
    card.append(
      top,
      el("p", "step", v.step_name + " · " + Math.round(v.confidence * 100) + "% confidence"),
      el("p", "expl", v.explanation)
    );

    // The evidence object is the point of the whole tool: a reader can check
    // the claim without trusting the tool. Showing it is not a debug affordance.
    //
    // One row per field rather than a JSON dump: "disclosed_price 299.0" next
    // to "final_total 457.0" is something a judge reads in a glance, where the
    // same data as a brace-and-quote blob reads as a log file. Every value is
    // still set with textContent -- it came from the audited site.
    if (v.evidence && Object.keys(v.evidence).length) {
      const shown = Object.assign({}, v.evidence);
      delete shown.evidence_tier;   // already shown as the badge above
      const keys = Object.keys(shown);
      if (keys.length) {
        const ev = el("div", "evidence");
        keys.forEach((k) => {
          const row = el("div", "ev-row");
          // "disclosed price", not "disclosed_price": the same field, readable, and
          // it wraps between words instead of mid-identifier.
          row.append(el("span", "ev-key", k.replace(/_/g, " ")),
                     el("span", "ev-val", evidenceText(shown[k])));
          ev.append(row);
        });
        card.append(ev);
      }
    }
    box.append(card);
  });
}

/** A field's value as plain text: lists of words read as a list, anything
 *  structured falls back to compact JSON so nothing is ever hidden. */
function evidenceText(value) {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value) && value.every((x) => typeof x !== "object" || x === null)) {
    return value.join(", ");
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
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
          // Say what actually happened. "Lost contact with the server (429)"
          // was shown while the server was answering perfectly well: a 429 is
          // a clear, deliberate reply meaning "slow down". Describing it as a
          // lost connection sends the reader to check the terminal, the
          // network and the firewall -- none of which are the problem, and
          // none of which they have time for mid-demo.
          if (pollResponse.status === 429) {
            setStatus("Too many requests from this address in the last minute. "
                      + "The audit is probably still running — wait a few "
                      + "seconds and press Run the audit again.", true);
          } else if (pollResponse.status === 404) {
            setStatus("That audit is no longer on the server. Press Run the "
                      + "audit to start a new one.", true);
          } else {
            setStatus(`The server refused the status request (HTTP `
                      + `${pollResponse.status}). The terminal running the `
                      + `server will have the detail.`, true);
          }
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

/** Point the frame, its label and its address bar at one shop. All three move
 *  together: a stale address under a swapped page is how a demo accidentally
 *  claims to have audited the wrong site. */
function showShop(dark) {
  const path = dark ? "/storefront/cart.html" : "/storefront-clean/cart.html";
  if ($("frame").getAttribute("src") !== path) $("frame").src = path;
  $("frame-label").textContent = dark ? "ShopMart checkout" : "HonestCart checkout";
  // The address field shows the real URL being framed, so the panel cannot
  // drift into looking like a mockup of a shop rather than a shop.
  $("frame-url").textContent = location.host + path;
}

$("run").addEventListener("click", runAudit);
$("target").addEventListener("change", () => {
  showShop($("target").value === "hosted_dark_demo");
});

// The landing page links here as /demo/?shop=clean from its "HonestCart"
// chip. The parameter only PRESELECTS one of the two server-defined shops --
// it is compared against a fixed word, never used as a value, and it never
// starts an audit: a link should not launch a browser on someone's behalf.
const wantsClean = new URLSearchParams(location.search).get("shop") === "clean";
if (wantsClean) $("target").value = "hosted_clean_demo";
showShop($("target").value === "hosted_dark_demo");
