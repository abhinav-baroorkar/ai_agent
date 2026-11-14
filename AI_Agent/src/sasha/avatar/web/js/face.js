// Picks the character that is drawn on Home. Every face module exports
// create(root) and returns { setState(state), setLevelSource(fn) }.
// ?face=1|2|3 switches between the concepts under review.
const KINDS = { 1: 'orb', 2: 'character', 3: 'glass' };
const DEFAULT = '1';

export async function createFace(root) {
  const asked = new URLSearchParams(location.search).get('face');
  const kind = KINDS[asked] || KINDS[DEFAULT];
  const mod = await import(`./faces/${kind}.js`);
  root.dataset.face = kind;
  return mod.create(root);
}
