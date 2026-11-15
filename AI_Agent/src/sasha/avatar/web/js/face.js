// Loads the character drawn on Home: a small being of light.
// It returns { setState(state), setLevelSource(fn) }.
export async function createFace(root) {
  const mod = await import('./faces/spirit.js');
  return mod.create(root);
}
