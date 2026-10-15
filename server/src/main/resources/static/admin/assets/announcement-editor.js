"use strict";

(() => {
  const field = document.querySelector("[data-markdown-editor]");
  if (!field || !window.EasyMDE) return;
  const button = (name, title, action, noDisable = false) => ({ name, title, action, noDisable, icon: title });
  const editor = new EasyMDE({
    element: field,
    forceSync: true,
    autoDownloadFontAwesome: false,
    spellChecker: false,
    nativeSpellcheck: false,
    inputStyle: "textarea",
    autosave: { enabled: false },
    status: false,
    minHeight: "320px",
    uploadImage: false,
    previewImagesInEditor: false,
    previewRender: window.T7RAnnouncementPreview,
    shortcuts: { toggleBlockquote: null, toggleCodeBlock: null, drawLink: null, drawImage: null,
      toggleFullScreen: null, toggleSideBySide: null, drawHorizontalRule: null },
    toolbar: [
      button("heading", "标题", EasyMDE.toggleHeadingSmaller),
      button("bold", "粗体", EasyMDE.toggleBold),
      button("italic", "斜体", EasyMDE.toggleItalic),
      button("unordered-list", "无序列表", EasyMDE.toggleUnorderedList),
      button("ordered-list", "有序列表", EasyMDE.toggleOrderedList),
      button("inline-code", "行内代码", instance => {
        const cm = instance.codemirror;
        cm.replaceSelection("`" + (cm.getSelection() || "代码") + "`", "around");
        cm.focus();
      }),
      button("undo", "撤销", EasyMDE.undo),
      button("redo", "重做", EasyMDE.redo),
      button("preview", "预览", EasyMDE.togglePreview, true)
    ]
  });
  const cm = editor.codemirror;
  const input = cm.getInputField();
  const container = editor.gui.toolbar.parentElement;
  const count = document.createElement("div");
  count.className = "admin-markdown-count";
  container.append(count);
  input.id = `${field.id}-editor`;
  input.dataset.editorInput = "";
  input.setAttribute("role", "textbox");
  input.setAttribute("aria-multiline", "true");
  input.setAttribute("aria-label", "正文（Markdown）");
  input.setAttribute("aria-required", "true");
  field.dataset.focusTarget = input.id;
  document.querySelector(`label[for="${field.id}"]`).addEventListener("click", event => {
    event.preventDefault();
    if (editor.isPreviewActive()) editor.togglePreview();
    cm.focus();
  });
  const toolbarButtons = Array.from(editor.gui.toolbar.querySelectorAll("button"));
  toolbarButtons.forEach(control => { control.tabIndex = 0; });
  cm.setOption("extraKeys", { ...cm.getOption("extraKeys"), Tab: false, "Shift-Tab": false });
  function syncState() {
    const busy = field.disabled || field.readOnly;
    const previewing = editor.toolbarElements.preview.classList.contains("active");
    cm.setOption("readOnly", busy || previewing);
    toolbarButtons.forEach(control => { control.disabled = busy || (previewing && control !== editor.toolbarElements.preview); });
    container.classList.toggle("is-invalid", field.getAttribute("aria-invalid") === "true");
    input.setAttribute("aria-invalid", field.getAttribute("aria-invalid") || "false");
    input.setAttribute("aria-describedby", field.getAttribute("aria-describedby") || "");
    input.setAttribute("aria-disabled", String(busy));
    editor.toolbarElements.preview.setAttribute("aria-pressed", String(previewing));
    const preview = container.querySelector(".editor-preview-full");
    if (preview) {
      preview.setAttribute("role", "region");
      preview.setAttribute("aria-label", "公告正文预览");
      preview.tabIndex = 0;
    }
  }
  function syncValue() {
    cm.save();
    const length = field.value.length;
    field.setCustomValidity(length > field.maxLength ? `正文最多 ${field.maxLength} 个字符，当前 ${length} 个。` : "");
    count.textContent = `${length} / ${field.maxLength} 字符`;
    field.dispatchEvent(new Event("input", { bubbles: true }));
  }
  cm.on("change", syncValue);
  cm.on("blur", () => field.dispatchEvent(new Event("blur")));
  cm.on("beforeChange", (_, change) => { if (field.disabled || field.readOnly) change.cancel(); });
  new MutationObserver(syncState).observe(field, { attributes: true,
    attributeFilter: ["disabled", "readonly", "aria-invalid", "aria-describedby"] });
  new MutationObserver(syncState).observe(editor.toolbarElements.preview, { attributes: true, attributeFilter: ["class"] });
  input.addEventListener("focus", () => { if (editor.isPreviewActive()) editor.togglePreview(); });
  field.form.addEventListener("reset", () => queueMicrotask(() => { editor.value(field.value); syncValue(); }));
  syncValue();
  syncState();
})();
