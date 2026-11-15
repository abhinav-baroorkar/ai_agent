// Turns the simple formatting models use into DOM nodes: bold, italics, inline
// code, headings, bullet and numbered lists, and fenced code blocks.
//
// It is a small, safe subset, not a Markdown implementation. Every node is made
// with createElement and filled with text; nothing from the model is ever
// treated as markup. Links are not made clickable (the address is shown as
// text), images are not loaded, and HTML in the text stays visible as text.
// Anything outside the subset is shown exactly as written.
const el = (tag, cls) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  return node;
};

const INLINE = new RegExp([
  '(`[^`\\n]+`)', // 1 code
  '(\\*\\*[^*\\n]+?\\*\\*)', // 2 bold
  '((?<![A-Za-z0-9*])\\*[^*\\s][^*\\n]*?\\*(?![A-Za-z0-9*]))', // 3 italic with *
  '((?<![A-Za-z0-9_])_[^_\\s][^_\\n]*?_(?![A-Za-z0-9_]))', // 4 italic with _
  '(\\[[^\\]\\n]+\\]\\([^)\\s]+\\))', // 5 link
].join('|'), 'g');

// Appends `text` to `parent`, with bold, italics, code and links marked up.
function inline(parent, text, depth = 0) {
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) parent.append(text.slice(last, m.index));
    const [whole, code, bold, star, under, link] = m;
    if (code) {
      const node = el('code');
      node.textContent = code.slice(1, -1);
      parent.append(node);
    } else if (bold || star || under) {
      const node = el(bold ? 'strong' : 'em');
      const inner = bold ? bold.slice(2, -2) : (star || under).slice(1, -1);
      if (depth < 2) inline(node, inner, depth + 1); else node.textContent = inner;
      parent.append(node);
    } else if (link) {
      const split = link.indexOf('](');
      parent.append(link.slice(1, split));
      const where = el('span', 'md-url'); // shown, never clickable
      where.textContent = ` (${link.slice(split + 2, -1)})`;
      parent.append(where);
    } else {
      parent.append(whole);
    }
    last = m.index + whole.length;
  }
  if (last < text.length) parent.append(text.slice(last));
}

const HEADING = /^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$/;
const BULLET = /^\s{0,6}[-*+]\s+(.*)$/;
const NUMBER = /^\s{0,6}(\d{1,4})[.)]\s+(.*)$/;
const FENCE = /^ {0,3}```/;

export function renderMarkdown(text) {
  const out = document.createDocumentFragment();
  const lines = String(text).replace(/\r\n?/g, '\n').split('\n');
  let para = [];
  let list = null;

  const endPara = () => {
    if (!para.length) return;
    const p = el('p');
    inline(p, para.join('\n'));
    out.append(p);
    para = [];
  };
  const endList = () => { list = null; };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (FENCE.test(line)) { // a code block runs to the closing fence, or to the end while it streams
      endPara();
      endList();
      const body = [];
      for (i += 1; i < lines.length && !FENCE.test(lines[i]); i++) body.push(lines[i]);
      const pre = el('pre');
      const code = el('code');
      code.textContent = body.join('\n');
      pre.append(code);
      out.append(pre);
      continue;
    }
    const heading = HEADING.exec(line);
    const bullet = BULLET.exec(line);
    const number = NUMBER.exec(line);
    if (heading) {
      endPara();
      endList();
      const h = el(heading[1].length <= 2 ? 'h3' : 'h4');
      inline(h, heading[2]);
      out.append(h);
    } else if (bullet || number) {
      endPara();
      const tag = bullet ? 'ul' : 'ol';
      if (!list || list.tagName.toLowerCase() !== tag) {
        list = el(tag);
        if (number) list.start = Number(number[1]);
        out.append(list);
      }
      const item = el('li');
      inline(item, bullet ? bullet[1] : number[2]);
      list.append(item);
    } else if (!line.trim()) {
      endPara();
      endList();
    } else if (list && /^\s{2,}\S/.test(line)) { // an indented line continues the last list item
      list.lastChild.append(' ');
      inline(list.lastChild, line.trim());
    } else {
      endList();
      para.push(line);
    }
  }
  endPara();
  return out;
}
