// ImplantTrace UI behaviour. No framework: forms post JSON to the API, pages re-render on the server.
(() => {
  "use strict";

  const API = "/api/v1";

  function cookie(name) {
    const hit = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
    return hit ? decodeURIComponent(hit.slice(name.length + 1)) : "";
  }

  async function call(method, url, body, isForm) {
    const headers = { "X-CSRF-Token": cookie("it_csrf"), Accept: "application/json" };
    let payload;
    if (body !== undefined) {
      if (isForm) payload = body;
      else { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
    }
    const res = await fetch(url, { method, headers, body: payload, credentials: "same-origin" });
    let data = null;
    const type = res.headers.get("content-type") || "";
    if (type.includes("application/json")) data = await res.json();
    if (res.status === 401 && !url.endsWith("/auth/login")) {
      location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
      throw new Error("Session expired");
    }
    if (!res.ok) {
      const err = new Error((data && data.detail) || res.statusText || "Request failed");
      err.details = (data && data.errors) || [];
      throw err;
    }
    return data;
  }

  function toast(text) {
    let t = document.getElementById("toast");
    if (!t) { t = document.createElement("div"); t.id = "toast"; t.setAttribute("role", "status"); document.body.append(t); }
    t.textContent = text; t.hidden = false;
    clearTimeout(t._timer); t._timer = setTimeout(() => { t.hidden = true; }, 3500);
  }

  // Survive the reload that follows a successful action.
  function flash(text) { try { sessionStorage.setItem("it_flash", text); } catch { /* storage off */ } }
  function showFlash() {
    try {
      const text = sessionStorage.getItem("it_flash");
      if (text) { sessionStorage.removeItem("it_flash"); toast(text); }
    } catch { /* storage off */ }
  }

  function showError(container, err) {
    let box = container.querySelector(".form-error");
    if (!box) { box = document.createElement("div"); box.className = "form-error"; box.setAttribute("role", "alert"); container.append(box); }
    box.replaceChildren(document.createTextNode(err.message));
    if (err.details && err.details.length) {
      const ul = document.createElement("ul");
      err.details.forEach((d) => { const li = document.createElement("li"); li.textContent = d; ul.append(li); });
      box.append(ul);
    }
    box.hidden = false;
  }

  function clearError(container) {
    const box = container.querySelector(".form-error");
    if (box) box.hidden = true;
  }

  function formBody(form) {
    const body = {};
    for (const el of form.elements) {
      if (!el.name || el.disabled || el.type === "submit") continue;
      const type = el.dataset.type;
      if (el.type === "checkbox") { body[el.name] = el.checked; continue; }
      const raw = el.value.trim();
      if (raw === "" && !el.required) continue;
      if (type === "int") body[el.name] = Number.parseInt(raw, 10);
      else if (type === "lines") body[el.name] = raw.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
      else body[el.name] = el.type === "password" ? el.value : raw;
    }
    return body;
  }

  function fill(template, data, encode) {
    return template.replace(/\{(\w+)\}/g, (_, k) => {
      const v = String((data && data[k]) ?? "");
      return encode ? encodeURIComponent(v) : v;
    });
  }

  async function submitForm(form) {
    const submit = form.querySelector("[type=submit]");
    clearError(form);
    if (submit) submit.disabled = true;
    try {
      const method = (form.dataset.method || "POST").toUpperCase();
      let data;
      if (form.dataset.upload !== undefined) {
        data = await call(method, form.dataset.api, new FormData(form), true);
      } else {
        const body = form.dataset.bodyKey ? formBody(form)[form.dataset.bodyKey] : formBody(form);
        data = await call(method, form.dataset.api, body);
      }
      const message = form.dataset.success ? fill(form.dataset.success, data, false) : "Saved";
      if (form.dataset.result) { renderResult(form, data); toast(message); return; }
      flash(message);
      if (form.dataset.redirect) location.href = fill(form.dataset.redirect, data, true);
      else location.reload();
    } catch (err) {
      showError(form, err);
    } finally {
      if (submit) submit.disabled = false;
    }
  }

  // Import summary shown inline on the Quality page.
  function renderResult(form, data) {
    const out = document.getElementById(form.dataset.result);
    if (!out) return;
    out.hidden = false;
    out.className = "notice " + (data.status === "duplicate" ? "warn" : data.failed_rows ? "bad" : "good");
    if (data.status === "duplicate") out.textContent = `${data.filename} was already imported; nothing changed.`;
    else out.textContent = `${data.filename}: ${data.rows} results imported, ${data.failed_rows} out of tolerance.` +
      (data.orders_on_hold.length ? ` On hold: ${data.orders_on_hold.join(", ")}. Reload to see the hold queue.` : "");
  }

  async function runAction(btn) {
    btn.disabled = true;
    try {
      const body = btn.dataset.body ? JSON.parse(btn.dataset.body) : undefined;
      await call((btn.dataset.method || "POST").toUpperCase(), btn.dataset.api, body);
      flash(btn.dataset.success || "Done");
      if (btn.dataset.redirect) location.href = btn.dataset.redirect; else location.reload();
    } catch (err) {
      toast(err.message);
      btn.disabled = false;
    }
  }

  // Hover / focus tooltips for chart marks
  function tooltips() {
    let tip;
    const show = (el) => {
      if (!tip) { tip = document.createElement("div"); tip.id = "tip"; document.body.append(tip); }
      tip.textContent = el.dataset.tip; tip.hidden = false;
      const r = el.getBoundingClientRect();
      const w = tip.offsetWidth;
      tip.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + "px";
      tip.style.top = Math.max(8, r.top - tip.offsetHeight - 6) + "px";
    };
    const hide = () => { if (tip) tip.hidden = true; };
    document.querySelectorAll("[data-tip]").forEach((el) => {
      el.addEventListener("mouseenter", () => show(el));
      el.addEventListener("focus", () => show(el));
      el.addEventListener("mouseleave", hide);
      el.addEventListener("blur", hide);
    });
  }

  // Chart bar sizes come from data attributes (no inline styles, so CSP stays strict).
  function sizeBars() {
    document.querySelectorAll("[data-h]").forEach((el) => { el.style.height = el.dataset.h + "%"; });
    document.querySelectorAll("[data-w]").forEach((el) => { el.style.width = el.dataset.w + "%"; });
  }

  document.addEventListener("submit", (e) => {
    const form = e.target.closest("form[data-api]");
    if (!form) return;
    e.preventDefault();
    submitForm(form);
  });

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-api]:not(form button)");
    if (btn) { e.preventDefault(); runAction(btn); return; }
    const out = e.target.closest("[data-logout]");
    if (out) {
      e.preventDefault();
      call("POST", API + "/auth/logout").catch(() => {}).finally(() => { location.href = "/login"; });
      return;
    }
    const print = e.target.closest("[data-print]");
    if (print) { e.preventDefault(); window.print(); }
  });

  document.addEventListener("DOMContentLoaded", () => {
    showFlash();
    sizeBars();
    tooltips();
    const focus = document.querySelector("[data-autofocus]");
    if (focus) { focus.focus(); if (focus.select) focus.select(); }
  });
})();
