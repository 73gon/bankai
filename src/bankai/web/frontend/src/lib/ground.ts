/**
 * zon's page ground -- styles/zon/expressive.css: the grain, two layers blended onto
 * --background, under a faint light from above -- baked into one opaque tile for
 * `page-ground`, so the app's scrolling area can paint the ground itself. See-through,
 * that area let body's ground show, but Chrome then painted the whole page again on
 * every scroll frame and blended both grain layers anew (20-30 fps on the manga review,
 * 60 opaque); flat --background instead hid the ground (Nov 2026).
 */
export async function bakeGround(): Promise<void> {
  const root = document.documentElement;
  const under = getComputedStyle(document.body, '::before');
  const over = getComputedStyle(document.body, '::after');
  const noise = /url\(["']?(.*?)["']?\)$/.exec(getComputedStyle(root).getPropertyValue('--grain-noise').trim())?.[1];
  // No grain (zon's opt-out), nothing to bake: the area shows --background and the light.
  if (!noise || under.content === 'none') return;
  const image = new Image();
  image.src = noise;
  await image.decode();
  // The grain layers' background-size: 200px, a seamless tile.
  const size = 200;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const context = canvas.getContext('2d');
  if (!context) return;
  // The ground under the grain: html::before since zon/ui 0.3.0 (body has none of its own any more,
  // and grain blended onto nothing showed as bare, bright noise); body's before it.
  const ground = getComputedStyle(root, '::before').backgroundColor;
  context.fillStyle = ground && ground !== 'rgba(0, 0, 0, 0)' ? ground : getComputedStyle(document.body).backgroundColor;
  context.fillRect(0, 0, size, size);
  const layer = (style: CSSStyleDeclaration) => {
    const [dx = 0, dy = 0] = style.backgroundPosition.split(' ').map((part) => parseFloat(part) % size || 0);
    context.globalCompositeOperation = style.mixBlendMode as GlobalCompositeOperation;
    context.globalAlpha = Number(style.opacity);
    for (const x of [dx - size, dx]) for (const y of [dy - size, dy]) context.drawImage(image, x, y, size, size);
  };
  layer(under);
  layer(over);
  root.style.setProperty('--page-ground-tile', `url(${canvas.toDataURL('image/png')})`);
}
