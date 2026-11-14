// Tiny DOM helpers. All text goes in as text content, never as markup:
// replies, device names and connector details are untrusted.
export const $ = (sel, root = document) => root.querySelector(sel);

export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k in el && k !== 'list' && k !== 'form') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  el.append(...kids.flat().filter((k) => k != null && k !== false));
  return el;
}

const SVG = 'http://www.w3.org/2000/svg';
export function icon(name) {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('class', 'ico');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(SVG, 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}

export function clear(el) {
  el.replaceChildren();
  return el;
}

// Devanagari needs its own lang tag so the browser picks the right font and shaping.
export function langOf(text) {
  return /[ऀ-ॿ]/.test(text) ? 'mr' : 'en';
}

export function setText(el, text) {
  el.textContent = text;
  el.lang = langOf(text);
}

export const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
