"use strict";

document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
      event.preventDefault();
      return;
    }
    form.querySelectorAll('button[type="submit"]').forEach((button) => {
      button.disabled = true;
    });
  });
});

window.addEventListener("pagehide", () => {
  document.querySelectorAll("[data-secret]").forEach((element) => {
    element.textContent = "密码已显示，请返回账户目录。";
  });
});

window.addEventListener("pageshow", () => {
  document.querySelectorAll('button[type="submit"]').forEach((button) => {
    button.disabled = false;
  });
});
