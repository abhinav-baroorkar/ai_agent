// The "Microphone is on" sign, shared by everything that can open the
// microphone. It shows while any owner says its microphone is open or opening,
// so one part of the page can never hide it for another.
import { $ } from './dom.js';

const owners = new Set();

export function micSign(owner, on) {
  if (on) owners.add(owner); else owners.delete(owner);
  $('#mic-on').hidden = owners.size === 0;
}
