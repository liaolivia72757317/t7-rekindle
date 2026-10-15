"use strict";

(() => {
  const shell = document.querySelector(".ui-shell");
  const navigation = document.getElementById("primary-navigation");
  const desktop = window.matchMedia("(min-width: 992px)");

  function arrangeNavigation() {
    if (!navigation) return;
    shell.classList.remove("admin-nav-compact");
    shell.classList.toggle("admin-nav-compact", desktop.matches
      && navigation.scrollWidth > navigation.parentElement.clientWidth);
  }
  function measureShell() {
    document.documentElement.style.setProperty("--ui-shell-height", `${shell?.getBoundingClientRect().height || 0}px`);
  }
  arrangeNavigation();
  measureShell();
  if (shell) new ResizeObserver(measureShell).observe(shell);
  window.addEventListener("resize", arrangeNavigation);
  document.fonts.ready.then(arrangeNavigation);
  const toggle = document.querySelector(".admin-nav-toggle");
  const collapse = document.getElementById("main-navigation");
  for (const event of ["shown.bs.collapse", "hidden.bs.collapse"]) {
    collapse?.addEventListener(event, () => {
      const label = collapse.classList.contains("show") ? "收起管理导航" : "展开管理导航";
      toggle.setAttribute("aria-label", label);
      toggle.title = label;
    });
  }

  const navGroups = Array.from(document.querySelectorAll(".admin-nav-group"));
  navGroups.forEach(group => {
    const button = group.querySelector('[data-bs-toggle="dropdown"]');
    const dropdown = tabler.Dropdown.getOrCreateInstance(button);
    let closeTimer;
    group.addEventListener("pointerenter", event => {
      if (event.pointerType !== "mouse") return;
      clearTimeout(closeTimer);
      navGroups.filter(other => other !== group).forEach(other => {
        tabler.Dropdown.getInstance(other.querySelector('[data-bs-toggle="dropdown"]'))?.hide();
      });
      dropdown.show();
    });
    group.addEventListener("pointerleave", event => {
      if (event.pointerType !== "mouse") return;
      closeTimer = setTimeout(() => dropdown.hide(), 150);
    });
    group.addEventListener("focusout", event => {
      if (!group.contains(event.relatedTarget) && !group.matches(":hover")) dropdown.hide();
    });
    button.addEventListener("hide.bs.dropdown", () => clearTimeout(closeTimer));
  });

  function initialize(root = document) {
    const times = Array.from(root.querySelectorAll("time[data-local-time]"));
    if (root.matches?.("time[data-local-time]")) times.unshift(root);
    const dateTimeFormat = new Intl.DateTimeFormat(navigator.languages, { dateStyle: "medium", timeStyle: "medium" });
    times.forEach(element => {
      const value = element.getAttribute("datetime") || "";
      const date = new Date(value);
      element.textContent = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) && !Number.isNaN(date.getTime())
        ? dateTimeFormat.format(date) : "—";
    });
    root.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(element => {
      tabler.Tooltip.getOrCreateInstance(element, {
        container: element.closest(".modal, .offcanvas") || document.body,
        delay: { show: 300, hide: 0 }, trigger: "hover focus"
      });
    });
  }
  function dispose(root) {
    root.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(element => tabler.Tooltip.getInstance(element)?.dispose());
  }
  initialize();
  document.addEventListener("ui:content-added", event => initialize(event.target));
  document.addEventListener("ui:content-removed", event => dispose(event.target));
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(element => tabler.Tooltip.getInstance(element)?.hide());
  });
  // Keep Tab at either boundary inside the modal, including before focus can move to browser chrome.
  document.addEventListener("keydown", event => {
    if (event.key !== "Tab") return;
    const modal = document.querySelector(".modal.show, .offcanvas.show");
    if (!modal) return;
    const focusable = Array.from(modal.querySelectorAll("a[href], button, input, select, textarea, [tabindex]"))
      .filter(element => !element.disabled && element.tabIndex >= 0 && element.getClientRects().length);
    const first = focusable[0];
    const last = focusable.at(-1);
    if (!first || !modal.contains(document.activeElement) || (event.shiftKey && document.activeElement === first)
        || (!event.shiftKey && document.activeElement === last)) {
      event.preventDefault();
      (event.shiftKey ? last || modal : first || modal).focus();
    }
  }, true);

  const dialog = document.getElementById("ui-confirm");
  const accept = document.getElementById("ui-confirm-accept");
  const cancel = document.getElementById("ui-confirm-cancel");
  let pendingConfirmation = false;
  async function confirm(message, action = "继续操作", danger = true) {
    if (pendingConfirmation) return Promise.resolve(false);
    if (!dialog) return Promise.resolve(window.confirm(message));
    pendingConfirmation = true;
    const trigger = document.activeElement;
    // Bootstrap only supports one active modal; preserve the editor while confirming.
    const editor = document.querySelector("[data-form-modal].show");
    if (editor) {
      await new Promise(resolve => {
        editor.addEventListener("hidden.bs.modal", resolve, { once: true });
        tabler.Modal.getInstance(editor).hide();
      });
    }
    document.querySelectorAll('[data-bs-toggle="dropdown"]').forEach(element => tabler.Dropdown.getInstance(element)?.hide());
    document.getElementById("ui-confirm-message").textContent = message;
    accept.textContent = action;
    accept.className = `btn ${danger ? "btn-danger" : "btn-primary"}`;
    const modal = tabler.Modal.getOrCreateInstance(dialog);
    return new Promise(resolve => {
      let accepted = false;
      function onAccept() { accepted = true; modal.hide(); }
      accept.addEventListener("click", onAccept);
      dialog.addEventListener("shown.bs.modal", () => cancel.focus(), { once: true });
      dialog.addEventListener("hidden.bs.modal", () => {
        accept.removeEventListener("click", onAccept);
        pendingConfirmation = false;
        if (editor) {
          editor.addEventListener("shown.bs.modal", () => {
            if (trigger?.isConnected) trigger.focus();
            resolve(accepted);
          }, { once: true });
          tabler.Modal.getInstance(editor).show();
        } else {
          if (trigger?.isConnected) trigger.focus();
          resolve(accepted);
        }
      }, { once: true });
      modal.show();
    });
  }

  const stack = document.getElementById("ui-toast-stack");
  const announcer = document.getElementById("ui-announcer");
  const messages = new Map();
  const waiting = [];
  let visible = 0;
  function toast(message, kind = "success") {
    if (!stack) return;
    const key = `${kind}:${message}`;
    const existing = messages.get(key);
    if (existing) {
      existing.count += 1;
      if (existing.body) existing.body.textContent = `${message}（${existing.count} 次）`;
      if (existing.body) announcer.textContent = existing.body.textContent;
      existing.restart?.();
      return;
    }
    const item = { key, message, kind, count: 1 };
    messages.set(key, item);
    waiting.push(item);
    drain();
  }
  function drain() {
    while (visible < 3 && waiting.length) show(waiting.shift());
  }
  function show(item) {
    visible += 1;
    const element = document.createElement("div");
    element.className = "toast";
    const header = document.createElement("div");
    header.className = "toast-header";
    const title = document.createElement("strong");
    title.className = "me-auto";
    title.textContent = { success: "操作成功", info: "提示", warning: "请留意", danger: "操作未完成" }[item.kind] || "提示";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "btn btn-link btn-sm";
    close.textContent = "关闭";
    close.setAttribute("aria-label", "关闭通知");
    header.append(title, close);
    const body = document.createElement("div");
    body.className = "toast-body";
    const text = document.createElement("span");
    text.textContent = item.message + (item.count > 1 ? `（${item.count} 次）` : "");
    item.body = text;
    body.append(text);
    element.append(header, body);
    stack.append(element);
    const instance = tabler.Toast.getOrCreateInstance(element, { autohide: false });
    const duration = item.kind === "success" ? 3000 : item.kind === "info" ? 5000 : 0;
    let remaining = duration;
    let started;
    let timer;
    function pause() {
      if (timer) { clearTimeout(timer); timer = undefined; remaining = Math.max(0, remaining - (performance.now() - started)); }
    }
    function resume() {
      if (!duration || timer || element.matches(":hover") || element.contains(document.activeElement)) return;
      started = performance.now();
      timer = setTimeout(() => instance.hide(), remaining);
    }
    item.restart = () => { pause(); remaining = duration; resume(); };
    close.addEventListener("click", () => instance.hide());
    element.addEventListener("mouseenter", pause);
    element.addEventListener("mouseleave", resume);
    element.addEventListener("focusin", pause);
    element.addEventListener("focusout", () => queueMicrotask(resume));
    element.addEventListener("shown.bs.toast", resume, { once: true });
    element.addEventListener("hidden.bs.toast", () => {
      pause();
      instance.dispose();
      element.remove();
      messages.delete(item.key);
      visible -= 1;
      drain();
    }, { once: true });
    announcer.textContent = text.textContent;
    instance.show();
  }
  window.T7RUi = Object.freeze({ confirm, toast });
})();
