(function () {
  "use strict";

  if (window.__astorChatLoaded) return;
  window.__astorChatLoaded = true;

  // Derive the App Proxy base from this script's own src.
  // (defer scripts run after parsing, so document.currentScript is null — query the tag.)
  var scriptEl = document.querySelector('script[src*="widget.js"]');
  var src = scriptEl ? scriptEl.getAttribute("src") : "/apps/astor/widget.js";
  var base = src.replace(/\/widget\.js.*$/, "");
  var CHAT_URL = base + "/chat";

  var EXAMPLES = [
    "I need to run a Western blot — what do I need?",
    "What products does a BCA protein assay require?",
    "Find protocols that use Trypsin-EDTA",
  ];
  var ERROR_MSG = "I'm having trouble reaching the assistant right now — please try again.";

  var messages = []; // running history: {role, content}
  var busy = false;

  // ---- styles (scoped under #astor-chat) ----
  var style = document.createElement("style");
  style.textContent = [
    "#astor-chat{position:fixed;bottom:20px;right:20px;z-index:2147483000;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}",
    "#astor-chat *{box-sizing:border-box}",
    "#astor-chat .astor-bubble{width:56px;height:56px;border-radius:50%;background:#111;color:#fff;border:none;cursor:pointer;font-size:24px;box-shadow:0 4px 14px rgba(0,0,0,.25)}",
    "#astor-chat .astor-panel{display:none;flex-direction:column;width:360px;max-width:90vw;height:520px;max-height:75vh;background:#fff;border:1px solid #e5e5e5;border-radius:12px;box-shadow:0 10px 40px rgba(0,0,0,.18);overflow:hidden}",
    "#astor-chat.astor-open .astor-panel{display:flex}",
    "#astor-chat.astor-open .astor-bubble{display:none}",
    "#astor-chat .astor-head{padding:12px 14px;background:#111;color:#fff;font-weight:600;display:flex;justify-content:space-between;align-items:center}",
    "#astor-chat .astor-close{background:none;border:none;color:#fff;font-size:18px;cursor:pointer;line-height:1}",
    "#astor-chat .astor-log{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:10px;background:#fafafa}",
    "#astor-chat .astor-msg{max-width:85%;padding:8px 11px;border-radius:12px;font-size:14px;line-height:1.4;white-space:pre-wrap;word-wrap:break-word}",
    "#astor-chat .astor-user{align-self:flex-end;background:#111;color:#fff}",
    "#astor-chat .astor-bot{align-self:flex-start;background:#fff;border:1px solid #e5e5e5;color:#111}",
    "#astor-chat .astor-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}",
    "#astor-chat .astor-chip{font-size:12px;font-family:inherit;padding:3px 8px;border:1px solid #ddd;border-radius:999px;background:#f3f3f3;color:#333;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:pointer}",
    "#astor-chat .astor-chip:hover{background:#e7e7e7;border-color:#bbb}",
    "#astor-chat .astor-chip-more{background:#111;color:#fff;border-color:#111}",
    "#astor-chat .astor-ex{align-self:flex-start;text-align:left;font-size:13px;padding:7px 10px;border:1px solid #ddd;border-radius:10px;background:#fff;color:#111;cursor:pointer}",
    "#astor-chat .astor-foot{display:flex;gap:6px;padding:10px;border-top:1px solid #eee;background:#fff}",
    "#astor-chat .astor-input{flex:1;padding:9px 11px;border:1px solid #ddd;border-radius:8px;font-size:14px;outline:none}",
    "#astor-chat .astor-send{padding:0 14px;border:none;border-radius:8px;background:#111;color:#fff;cursor:pointer;font-size:14px}",
    "#astor-chat .astor-send:disabled{opacity:.5;cursor:default}",
  ].join("");
  document.head.appendChild(style);

  // ---- mount ----
  var root = document.getElementById("astor-chat");
  if (!root) {
    root = document.createElement("div");
    root.id = "astor-chat";
    document.body.appendChild(root);
  }
  root.innerHTML =
    '<button class="astor-bubble" aria-label="Open chat">&#128172;</button>' +
    '<div class="astor-panel" role="dialog" aria-label="Astor assistant">' +
      '<div class="astor-head"><span>Astor Assistant</span>' +
        '<button class="astor-close" aria-label="Close">&times;</button></div>' +
      '<div class="astor-log"></div>' +
      '<div class="astor-foot">' +
        '<input class="astor-input" type="text" placeholder="Ask about a protocol or product…" />' +
        '<button class="astor-send">Send</button>' +
      '</div>' +
    '</div>';

  var log = root.querySelector(".astor-log");
  var input = root.querySelector(".astor-input");
  var sendBtn = root.querySelector(".astor-send");
  var greeted = false;

  root.querySelector(".astor-bubble").addEventListener("click", openPanel);
  root.querySelector(".astor-close").addEventListener("click", function () {
    root.classList.remove("astor-open");
  });
  sendBtn.addEventListener("click", function () { submit(input.value); });
  input.addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.isComposing) submit(input.value); });

  function openPanel() {
    root.classList.add("astor-open");
    if (!greeted) { greeted = true; renderGreeting(); }
    input.focus();
  }

  function el(cls, text) {
    var d = document.createElement("div");
    d.className = cls;
    if (text != null) d.textContent = text;
    return d;
  }

  function escHtml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function formatReply(text) {
    // The model is told to write plain text, but markdown still slips through;
    // render the common bits instead of showing shoppers raw asterisks.
    // Escape FIRST — the reply may quote user/tool text, never trust it as HTML.
    var html = escHtml(text);
    html = html.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
    html = html.split("\n").map(function (line) {
      return line.replace(/^(\s*)[*•-]\s+/, "$1• ");
    }).join("\n");
    return html;
  }
  function scrollLog() { log.scrollTop = log.scrollHeight; }

  function renderGreeting() {
    log.appendChild(el("astor-msg astor-bot",
      "Hi! Tell me the experiment or product you need, and I'll find the protocol and the Astor products for it."));
    EXAMPLES.forEach(function (ex) {
      var b = document.createElement("button");
      b.className = "astor-ex";
      b.textContent = ex;
      b.addEventListener("click", function () { submit(ex); });
      log.appendChild(b);
    });
    scrollLog();
  }

  function addMsg(role, text) {
    var m = el("astor-msg " + (role === "user" ? "astor-user" : "astor-bot"), text);
    log.appendChild(m);
    scrollLog();
    return m;
  }

  var MAX_CHIPS = 6;
  function chipFor(it) {
    // Clicking a chip stays in the chat: ask the assistant about that exact item.
    // The id grounds the follow-up (titles alone are ambiguous — many protocols share one).
    var b = document.createElement("button");
    b.type = "button";
    b.className = "astor-chip";
    b.textContent = it.name;
    b.addEventListener("click", function () {
      submit('Tell me more about "' + it.name + '" (' + it.type + ' id: ' + it.id + ')');
    });
    return b;
  }
  function addChips(container, items) {
    if (!items || !items.length) return;
    var chips = el("astor-chips");
    items.slice(0, MAX_CHIPS).forEach(function (it) { chips.appendChild(chipFor(it)); });
    if (items.length > MAX_CHIPS) {
      var more = document.createElement("button");
      more.type = "button";
      more.className = "astor-chip astor-chip-more";
      more.textContent = "+" + (items.length - MAX_CHIPS) + " more";
      more.addEventListener("click", function expandMore() {
        more.remove();
        items.slice(MAX_CHIPS).forEach(function (it) { chips.appendChild(chipFor(it)); });
        scrollLog();
      });
      chips.appendChild(more);
    }
    container.appendChild(chips);
    scrollLog();
  }

  function setBusy(b) { busy = b; sendBtn.disabled = b; input.disabled = b; }

  function submit(text) {
    var q = (text || "").trim();
    if (!q || busy) return;
    Array.prototype.slice.call(log.querySelectorAll(".astor-ex")).forEach(function (n) { n.remove(); });
    input.value = "";
    addMsg("user", q);
    messages.push({ role: "user", content: q });
    setBusy(true);
    var typing = addMsg("assistant", "…");
    send(typing);
  }

  function send(typingEl) {
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, 30000);
    fetch(CHAT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: messages }),
      signal: ctrl.signal,
    }).then(function (r) {
      clearTimeout(timer);
      if (!r.ok) throw new Error("http " + r.status);
      return r.json();
    }).then(function (data) {
      typingEl.innerHTML = formatReply(data.reply || "");
      messages.push({ role: "assistant", content: data.reply || "" });
      addChips(typingEl, data.items);
      setBusy(false);
      input.focus();
    }).catch(function () {
      clearTimeout(timer);
      typingEl.textContent = ERROR_MSG;
      // drop the failed user turn so history stays alternating and nothing is silently resent
      messages.pop();
      setBusy(false);
      input.focus();
    });
  }
})();
