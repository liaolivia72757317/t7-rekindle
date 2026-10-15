"use strict";

(() => {
  const forms = Array.from(document.forms);
  const baselines = new WeakMap();
  const confirmed = new WeakSet();
  const busyButtons = new Map();
  let submitting = false;
  let asyncPending = false;
  let leaving = false;
  let errorId = 0;
  const fields = form => Array.from(form.querySelectorAll("input:not([type=hidden]), textarea, select"))
    .filter(field => !field.hasAttribute("data-editor-input"));
  const snapshot = form => JSON.stringify(fields(form).map(field => [field.name, field.value, field.checked]));
  const dirty = form => form.hasAttribute("data-dirty") && baselines.get(form) !== snapshot(form);
  const hasChanges = except => forms.some(form => form !== except && dirty(form));

  function validate(field) {
    if (field.disabled || field.readOnly) return true;
    if (field.dataset.match) {
      field.setCustomValidity(field.value === document.getElementById(field.dataset.match).value ? "" : "两次新密码不一致，请重新输入。");
    }
    const valid = field.validity.valid;
    let feedback = field.parentElement.querySelector(`[data-error-for="${field.id}"]`);
    if (!valid && !feedback) {
      feedback = document.createElement("div");
      feedback.className = "invalid-feedback admin-field-error";
      feedback.id = `field-error-${++errorId}`;
      feedback.dataset.errorFor = field.id;
      field.after(feedback);
      field.setAttribute("aria-describedby", [field.getAttribute("aria-describedby"), feedback.id].filter(Boolean).join(" "));
    }
    if (feedback) {
      feedback.textContent = valid ? "" : field.validity.patternMismatch ? "格式不正确，请按字段下方的规则填写。" : field.validationMessage;
      feedback.hidden = valid;
    }
    field.classList.toggle("is-invalid", !valid);
    if (valid) field.removeAttribute("aria-invalid");
    else field.setAttribute("aria-invalid", "true");
    return valid;
  }
  function setBusy(form, submitter) {
    form.setAttribute("aria-busy", "true");
    form.querySelectorAll('button[type="submit"]:not(:disabled)').forEach(button => {
      busyButtons.set(button, { nodes: Array.from(button.childNodes), width: button.style.width });
      if (button === submitter) {
        button.style.width = `${button.getBoundingClientRect().width}px`;
        button.classList.add("admin-submitting");
        button.textContent = "提交中";
        button.setAttribute("aria-busy", "true");
      }
      button.disabled = true;
    });
  }
  function restoreButtons() {
    forms.forEach(form => form.removeAttribute("aria-busy"));
    busyButtons.forEach((original, button) => {
      button.replaceChildren(...original.nodes);
      button.style.width = original.width;
      button.classList.remove("admin-submitting");
      button.removeAttribute("aria-busy");
      button.disabled = false;
    });
    busyButtons.clear();
  }
  async function submitEditor(form, button) {
    const body = new URLSearchParams(new FormData(form));
    const editable = fields(form).filter(field => !field.disabled);
    const controls = Array.from(document.querySelectorAll("[data-qq-group] button[type=button]:not(:disabled)"));
    setBusy(form, button);
    [...editable, ...controls].forEach(control => { control.disabled = true; });
    asyncPending = true;
    let message;
    try {
      const response = await fetch(button?.hasAttribute("formaction") ? button.formAction : form.action, {
        method: "POST", body, credentials: "same-origin", cache: "no-store",
        headers: { "X-Requested-With": "XMLHttpRequest" }, signal: AbortSignal.timeout(20000)
      });
      if (response.ok && response.redirected && !new URL(response.url).pathname.endsWith("/login")) {
        baselines.set(form, snapshot(form));
        [...editable, ...controls].forEach(control => { control.disabled = false; });
        restoreButtons();
        submitting = asyncPending = false;
        leaving = true;
        location.assign(response.url);
        return;
      }
      if (response.redirected && new URL(response.url).pathname.endsWith("/login")) {
        message = "登录已过期，输入仍保留。请先复制未保存内容，再重新登录并刷新本页。";
      } else if (response.headers.get("Content-Type")?.includes("application/json")) {
        message = (await response.json()).message;
      } else {
        const html = new DOMParser().parseFromString(await response.text(), "text/html");
        message = html.querySelector("[data-error-summary]")?.textContent;
      }
      message ||= `请求未完成（${response.status}），请检查输入或登录状态后重试。`;
    } catch (error) {
      message = "连接中断或请求超时，输入已保留。请先检查是否已保存，再决定是否重新提交。";
    }
    [...editable, ...controls].forEach(control => { control.disabled = false; });
    restoreButtons();
    submitting = asyncPending = false;
    let summary = form.querySelector("[data-error-summary]");
    if (!summary) {
      summary = document.createElement("div");
      summary.className = "alert alert-danger";
      summary.dataset.errorSummary = "";
      summary.tabIndex = -1;
      summary.setAttribute("role", "alert");
      form.prepend(summary);
    }
    summary.textContent = message;
    summary.focus();
  }
  forms.forEach(form => {
    baselines.set(form, snapshot(form));
    form.noValidate = true;
    fields(form).forEach(field => {
      field.addEventListener("blur", () => validate(field));
      field.addEventListener("input", () => { if (field.hasAttribute("aria-invalid")) validate(field); });
    });
    form.addEventListener("ui:reset-form", () => baselines.set(form, snapshot(form)));
    form.addEventListener("reset", () => queueMicrotask(() => {
      fields(form).forEach(field => { field.classList.remove("is-invalid"); field.removeAttribute("aria-invalid"); field.setCustomValidity(""); });
      form.querySelectorAll(".admin-field-error").forEach(error => { error.hidden = true; });
    }));
    form.addEventListener("submit", async event => {
      if (submitting) { event.preventDefault(); return; }
      const invalid = fields(form).filter(field => !validate(field));
      if (invalid.length) {
        event.preventDefault();
        (document.getElementById(invalid[0].dataset.focusTarget) || invalid[0]).focus();
        return;
      }
      if (form.hasAttribute("data-demo")) {
        event.preventDefault();
        baselines.set(form, snapshot(form));
        window.T7RUi.toast("表单校验通过。验收页不会保存业务数据。");
        return;
      }
      const button = event.submitter;
      const message = button?.dataset.confirm || form.dataset.confirm;
      const otherChanges = hasChanges(form);
      if (!confirmed.has(form) && (message || otherChanges)) {
        event.preventDefault();
        const text = [message, otherChanges ? "此页面还有其他未保存的修改，继续将丢弃这些修改。" : ""].filter(Boolean).join("\n");
        const danger = otherChanges || (!!message && (button?.dataset.confirmDanger || form.dataset.confirmDanger) !== "false");
        if (await window.T7RUi.confirm(text, button?.textContent.trim() || "继续操作", danger)) {
          confirmed.add(form);
          form.requestSubmit(button || undefined);
        }
        return;
      }
      confirmed.delete(form);
      submitting = true;
      if (form.hasAttribute("data-async")) {
        event.preventDefault();
        await submitEditor(form, button);
        return;
      }
      setBusy(form, button);
    });
  });

  document.addEventListener("click", async event => {
    const link = event.target.closest("a[href]");
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target === "_blank" || link.hasAttribute("download")) return;
    const url = new URL(link.href);
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
    if (!hasChanges()) return;
    event.preventDefault();
    if (await window.T7RUi.confirm("有未保存的修改。离开将丢弃这些修改。", "放弃修改并离开", true)) {
      leaving = true;
      location.assign(url.href);
    }
  });
  window.addEventListener("beforeunload", event => {
    if ((!submitting || asyncPending) && !leaving && hasChanges()) { event.preventDefault(); event.returnValue = ""; }
  });
  window.addEventListener("pagehide", () => {
    document.querySelectorAll("[data-secret]").forEach(element => { element.textContent = "密码已显示，请返回账户目录。"; });
  });
  window.addEventListener("pageshow", () => {
    submitting = asyncPending = leaving = false;
    restoreButtons();
  });
  document.querySelectorAll("[data-form-modal]").forEach(dialog => {
    const modal = tabler.Modal.getOrCreateInstance(dialog);
    let trigger;
    dialog.addEventListener("show.bs.modal", event => { trigger = event.relatedTarget || document.activeElement; });
    dialog.addEventListener("shown.bs.modal", () => {
      (dialog.querySelector("[data-error-summary], input:not([type=hidden]):not([readonly]), textarea, select") || dialog).focus();
    });
    dialog.addEventListener("hide.bs.modal", event => {
      if (dialog.querySelector('form[aria-busy="true"]')) event.preventDefault();
    });
    dialog.addEventListener("hidden.bs.modal", () => {
      if (trigger?.isConnected && !dialog.contains(trigger)) trigger.focus();
    });
    function openFromHash() {
      if (location.hash === `#${dialog.id}`) modal.show();
    }
    document.addEventListener("click", event => {
      const link = event.target.closest("a[href]");
      if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const url = new URL(link.href);
      if (url.pathname === location.pathname && url.search === location.search && url.hash === `#${dialog.id}`) {
        event.preventDefault();
        modal.show(link);
      }
    });
    window.addEventListener("hashchange", openFromHash);
    if (dialog.dataset.modalAuto === "true") modal.show();
    else openFromHash();
  });
  document.querySelector("[data-error-summary]:not(.modal [data-error-summary])")?.focus();

  // Only list URLs and scroll positions are retained; forms and credentials stay in memory.
  function readList(key) {
    try { return JSON.parse(sessionStorage.getItem(key)); }
    catch (error) { if (!(error instanceof DOMException || error instanceof SyntaxError)) throw error; return null; }
  }
  const list = document.querySelector("[data-list]");
  const storageKey = path => `t7r.admin.list:${path}`;
  if (list) {
    const key = storageKey(location.pathname);
    const saved = readList(key);
    if (saved?.url === location.href && !document.querySelector("[data-error-summary]")) {
      window.addEventListener("pageshow", () => requestAnimationFrame(() => window.scrollTo({ top: saved.scroll || 0, behavior: "instant" })));
    }
    window.addEventListener("pagehide", () => {
      try { sessionStorage.setItem(key, JSON.stringify({ url: location.href, scroll: window.scrollY })); }
      catch (error) { if (!(error instanceof DOMException)) throw error; }
    });
  }
  document.querySelectorAll("[data-back-list]").forEach(link => {
    const saved = readList(storageKey(new URL(link.href).pathname));
    if (!saved?.url) return;
    try {
      const url = new URL(saved.url);
      if (url.origin === location.origin && url.pathname === new URL(link.href).pathname) link.href = url.href;
    } catch (error) { if (!(error instanceof TypeError)) throw error; }
  });
})();
