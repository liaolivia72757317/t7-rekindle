"use strict";

document.querySelectorAll("[data-demo-toast]").forEach(button => {
  button.addEventListener("click", () => {
    const kind = button.dataset.demoToast;
    const message = { success: "示例操作已完成。", info: "这是普通信息通知。", warning: "这条示例提醒会保留，直到手动关闭。" }[kind];
    window.T7RUi.toast(message, kind);
  });
});
document.querySelectorAll("[data-demo-confirm]").forEach(button => {
  button.addEventListener("click", async () => {
    if (await window.T7RUi.confirm("这是确认交互示例，不会修改业务数据。", "执行示例")) {
      window.T7RUi.toast("示例操作已确认。");
    }
  });
});
document.querySelector("[data-demo-retry]").addEventListener("click", () => {
  document.getElementById("sample-retry-status").textContent = "示例重试完成，实际页面应重新请求数据。";
});
const table = document.getElementById("sample-table");
const rows = Array.from(table.tBodies[0].rows);
const boxes = rows.map(row => row.querySelector("input"));
const all = document.getElementById("sample-all");
function selection() {
  const count = boxes.filter(box => box.checked).length;
  all.checked = count === boxes.length;
  all.indeterminate = count > 0 && count < boxes.length;
  document.getElementById("sample-selection").hidden = count === 0;
  document.getElementById("sample-selection-count").textContent = `已选本页 ${count} 条`;
  rows.forEach(row => row.classList.toggle("is-selected", row.querySelector("input").checked));
}
all.addEventListener("change", () => { boxes.forEach(box => { box.checked = all.checked; }); selection(); });
boxes.forEach(box => box.addEventListener("change", selection));
document.getElementById("sample-clear").addEventListener("click", () => {
  boxes.forEach(box => { box.checked = false; }); selection(); all.focus();
});
let direction = 0;
document.getElementById("sample-sort").addEventListener("click", () => {
  direction = (direction + 1) % 3;
  const sorted = direction ? rows.toSorted((a, b) => a.cells[1].textContent.localeCompare(b.cells[1].textContent, "zh-CN") * (direction === 1 ? 1 : -1)) : rows;
  sorted.forEach(row => table.tBodies[0].append(row));
  document.getElementById("sample-sort-header").setAttribute("aria-sort", ["none", "ascending", "descending"][direction]);
  document.getElementById("sample-sort-direction").textContent = ["↕", "↑", "↓"][direction];
});
