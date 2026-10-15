"use strict";

// Keep the supported syntax aligned with Desktop/Views/MarkdownDocument.cs.
globalThis.T7RAnnouncementPreview = (() => {
  const escape = text => text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
  function inline(text) {
    const pattern = /\\([\\`*_])|(`+)(.+?)\2|(\*\*|__)(.+?)\4|(\*|_)(.+?)\6/g;
    let result = "";
    let offset = 0;
    for (const match of text.matchAll(pattern)) {
      result += escape(text.slice(offset, match.index));
      if (match[1]) result += escape(match[1]);
      else if (match[3]) result += `<code>${escape(match[3])}</code>`;
      else if (match[5]) result += `<strong>${inline(match[5])}</strong>`;
      else result += `<em>${inline(match[7])}</em>`;
      offset = match.index + match[0].length;
    }
    return result + escape(text.slice(offset));
  }
  return markdown => {
    let html = "";
    let block = "";
    const close = () => { if (block) html += `</${block}>`; block = ""; };
    for (const line of (markdown || "").replace(/\r\n?/g, "\n").split("\n")) {
      if (!line.trim()) { close(); continue; }
      const heading = /^ {0,3}(#{1,6})(?:[ \t]+(.*))?$/.exec(line);
      const item = /^ {0,3}(?:([-+*])|([1-9][0-9]{0,8})[.)])[ \t]+(.*)$/.exec(line);
      if (heading) {
        close();
        const level = heading[1].length;
        html += `<h${level}>${inline((heading[2] || "").trim().replace(/[ \t]+#+$/, ""))}</h${level}>`;
      } else if (item) {
        const type = item[2] ? "ol" : "ul";
        if (block !== type) {
          close();
          block = type;
          html += item[2] ? `<ol start="${item[2]}">` : "<ul>";
        }
        html += `<li>${inline(item[3])}</li>`;
      } else {
        if (block !== "p") { close(); block = "p"; html += "<p>"; }
        else html += " ";
        html += inline(line.trim());
      }
    }
    close();
    return html;
  };
})();
