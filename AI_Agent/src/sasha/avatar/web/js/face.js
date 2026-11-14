// Loads the character that is drawn on Home: a small being of light.
// It returns { setState(state), setLevelSource(fn) }.
// ?face=1 is the pure spirit (no eyes); ?face=2 adds two eyes of light.
const DEFAULT = '1';

export async function createFace(root) {
  const asked = new URLSearchParams(location.search).get('face');
  const variant = asked === '1' || asked === '2' ? asked : DEFAULT;
  const mod = await import('./faces/spirit.js');
  root.dataset.face = variant;
  return mod.create(root, { eyes: variant === '2' });
}
