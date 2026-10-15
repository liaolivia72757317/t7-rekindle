const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../../main/resources/static/admin/assets/announcement-preview.js'), 'utf8'), context);
const render = context.T7RAnnouncementPreview;

test('headings and paragraphs follow the launcher renderer', () => {
  assert.equal(render('# 标题 ##\r\n第一行\r第二行\n\n### 小标题'), '<h1>标题</h1><p>第一行 第二行</p><h3>小标题</h3>');
  assert.equal(render('#\n   \n普通文本'), '<h1></h1><p>普通文本</p>');
});

test('lists retain their starting index and switch marker type', () => {
  assert.equal(render('3. 三\n4) 四\n- 五\n+ 六\n正文'), '<ol start="3"><li>三</li><li>四</li></ol><ul><li>五</li><li>六</li></ul><p>正文</p>');
});

test('inline emphasis, code and escapes match supported client syntax', () => {
  assert.equal(render('**粗体 _斜体_** 与 __粗体__、_斜体_、`<code>`、\\*原样\\*'), '<p><strong>粗体 <em>斜体</em></strong> 与 <strong>粗体</strong>、<em>斜体</em>、<code>&lt;code&gt;</code>、*原样*</p>');
  assert.equal(render('``含 ` 的代码``'), '<p><code>含 ` 的代码</code></p>');
});

test('HTML, URLs and images stay inert text', () => {
  const html = render('<img src=x onerror="alert(1)">\n[链接](javascript:alert(1)) ![图](https://example.com/image.png) &');
  assert.ok(html.includes('&lt;img src=x onerror="alert(1)"&gt;'));
  assert.ok(html.includes('[链接](javascript:alert(1)) ![图](https://example.com/image.png) &amp;'));
  assert.doesNotMatch(html, /<(img|script|a|iframe)\b/);
});

test('unsupported blocks stay text and empty input stays empty', () => {
  assert.equal(render('> 引用\n---\n| 表格 |'), '<p>&gt; 引用 --- | 表格 |</p>');
  assert.equal(render('```'), '<p><code>`</code></p>');
  assert.equal(render(' \n\r\n'), '');
  assert.equal(render(null), '');
});
