"use strict";

(() => {
  const root = document.querySelector("[data-qq-group]");
  if (!root) return;
  const form = document.getElementById("groupSettings");
  const select = document.getElementById("groupNumber");
  const save = document.getElementById("groupSave");
  const start = document.getElementById("scanStart");
  const cancel = document.getElementById("scanCancel");
  const frame = document.getElementById("qrFrame");
  const status = document.getElementById("scanStatus");
  const identity = document.getElementById("groupIdentity");
  const scanField = document.getElementById("scanId");
  const refresh = document.getElementById("groupsRefresh");
  const keyReady = !start.disabled;
  let generation = 0;
  let scanId = "";
  let timer;
  let edited = false;
  form.addEventListener("input", () => { edited = true; });

  function beginActivity() {
    clearTimeout(timer);
    start.disabled = !keyReady;
    if (refresh) refresh.disabled = false;
    select.removeAttribute("aria-busy");
    return ++generation;
  }

  async function request(path) {
    let response;
    try {
      response = await fetch(`${root.dataset.apiBase}${path}`, {
        method: "POST", credentials: "same-origin", cache: "no-store",
        signal: AbortSignal.timeout(15000),
        headers: { "X-CSRF-TOKEN": form.querySelector('[name="_csrf"]').value, "X-Requested-With": "XMLHttpRequest" }
      });
    } catch (error) { throw new Error("请求超时或连接中断，请重试扫码或刷新群列表。", { cause: error }); }
    if (!response.headers.get("Content-Type")?.includes("application/json")) {
      if (response.ok && response.status !== 200) return null;
      throw new Error("后台登录可能已过期，请刷新页面重试。");
    }
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || "请求未完成，请重试。");
    return result;
  }
  function groups(items, selected) {
    select.replaceChildren(new Option("请选择目标群", ""));
    items.forEach(group => select.add(new Option(`${group.name}（${group.number}）`, group.number)));
    select.value = items.some(group => group.number === selected) ? selected : "";
    select.disabled = items.length === 0;
    save.disabled = !select.value;
  }
  select.addEventListener("change", () => { save.disabled = !select.value; });
  async function currentGroups() {
    const current = beginActivity();
    const previousDisabled = select.disabled;
    const previousSaveDisabled = save.disabled;
    refresh.disabled = true;
    select.disabled = save.disabled = true;
    select.setAttribute("aria-busy", "true");
    frame.hidden = cancel.hidden = true;
    status.textContent = "正在读取当前账号的可管理群…";
    try {
      const result = await request("/groups");
      if (current !== generation) return;
      scanField.value = "";
      groups(result, root.dataset.group);
      identity.textContent = `当前群站账号：${root.dataset.qq}`;
      status.textContent = result.length ? "请选择目标群并保存。" : "当前 QQ 没有可管理的群，请更换账号扫码。";
      if (!edited) form.dispatchEvent(new Event("ui:reset-form"));
    } catch (error) {
      if (current === generation) {
        select.disabled = previousDisabled;
        save.disabled = previousSaveDisabled;
        status.textContent = (select.options.length > 1 ? "群列表刷新未完成，保留上次内容。" : "") + error.message;
      }
    }
    finally { if (current === generation) { refresh.disabled = false; select.removeAttribute("aria-busy"); } }
  }
  async function poll(current) {
    if (current !== generation) return;
    try {
      const result = await request(`/qr/${scanId}/poll`);
      if (current !== generation) return;
      status.textContent = result.message;
      if (result.state === "READY") {
        frame.hidden = true;
        scanField.value = result.id;
        identity.textContent = `${result.nickname}（${result.qqNumber}）· 10 分钟内选群保存`;
        groups(result.groups, root.dataset.group);
      } else if (result.state === "WAITING" || result.state === "SCANNED") {
        timer = setTimeout(() => poll(current), 3000);
      } else { frame.hidden = true; }
    } catch (error) { if (current === generation) { status.textContent = error.message; frame.hidden = true; } }
  }
  start.addEventListener("click", async () => {
    const current = beginActivity();
    start.disabled = true;
    frame.hidden = cancel.hidden = true;
    scanField.value = "";
    groups([], "");
    status.textContent = "正在生成二维码…";
    try {
      const result = await request("/qr");
      if (current !== generation) return;
      scanId = result.id;
      document.getElementById("qrImage").src = `${root.dataset.apiBase}/qr/${encodeURIComponent(scanId)}/image`;
      frame.hidden = cancel.hidden = false;
      status.textContent = result.message;
      timer = setTimeout(() => poll(current), 3000);
    } catch (error) { if (current === generation) status.textContent = error.message; }
    finally { if (current === generation) start.disabled = false; }
  });
  cancel.addEventListener("click", async () => {
    const current = beginActivity();
    frame.hidden = cancel.hidden = true;
    scanField.value = "";
    groups([], "");
    try { await request(`/qr/${scanId}/cancel`); if (current === generation) status.textContent = "扫码已取消，原配置保持不变。"; }
    catch (error) { if (current === generation) status.textContent = error.message; }
    if (current === generation && root.dataset.qq) await currentGroups();
  });
  refresh?.addEventListener("click", currentGroups);
  window.addEventListener("pagehide", () => { beginActivity(); frame.hidden = cancel.hidden = true; });
  window.addEventListener("pageshow", event => {
    if (!event.persisted) return;
    scanField.value = "";
    groups([], "");
    if (root.dataset.qq) currentGroups();
    else status.textContent = "请重新扫码载入群列表。";
  });
  if (root.dataset.qq) currentGroups();
})();
