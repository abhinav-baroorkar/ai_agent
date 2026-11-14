// Concept 2 artwork: an original illustrated character, built from layered
// shapes. Deliberately stylised (pearl-lavender skin, indigo hair, simple
// dark eyes with a coloured glow) so it never drifts toward realism.
// The state colour arrives as the CSS variable --glow.
const eye = (cls, x) => `
  <g class="eye ${cls}" transform="translate(${x} 188)">
    <g class="ball">
      <ellipse rx="14.5" ry="17" fill="#1c1736"/>
      <g class="pupil">
        <ellipse cy="3" rx="11.5" ry="13" fill="url(#c-iris)"/>
        <circle cx="-5" cy="-7" r="4.6" fill="#fff"/>
        <circle cx="5.5" cy="6.5" r="2" fill="#fff" opacity=".75"/>
      </g>
    </g>
    <path class="lid" d="M-18 -5Q-2 -24 17 -9" fill="none" stroke="#1c1736" stroke-width="5" stroke-linecap="round"/>
  </g>`;

export const ART = `
<svg viewBox="0 0 400 400" aria-hidden="true">
  <defs>
    <radialGradient id="c-back"><stop offset="0" stop-color="var(--glow)" stop-opacity=".3"/><stop offset=".55" stop-color="var(--glow)" stop-opacity=".08"/><stop offset="1" stop-color="var(--glow)" stop-opacity="0"/></radialGradient>
    <linearGradient id="c-skin" x1=".2" y1="0" x2=".85" y2="1"><stop offset="0" stop-color="#f6f1ff"/><stop offset=".55" stop-color="#d6cdee"/><stop offset="1" stop-color="#a99dd0"/></linearGradient>
    <radialGradient id="c-shade" cx=".32" cy=".3" r=".8"><stop offset=".55" stop-color="#5a4a9a" stop-opacity="0"/><stop offset="1" stop-color="#4a3a8a" stop-opacity=".5"/></radialGradient>
    <linearGradient id="c-hair" x1=".2" y1="0" x2=".7" y2="1"><stop offset="0" stop-color="#3b3270"/><stop offset=".5" stop-color="#241e4a"/><stop offset="1" stop-color="#15112e"/></linearGradient>
    <linearGradient id="c-top" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#232a40"/><stop offset="1" stop-color="#131826" stop-opacity="0"/></linearGradient>
    <radialGradient id="c-iris" cx=".5" cy=".85" r=".8"><stop offset="0" stop-color="var(--glow)"/><stop offset=".6" stop-color="var(--glow)" stop-opacity=".5"/><stop offset="1" stop-color="var(--glow)" stop-opacity="0"/></radialGradient>
    <filter id="c-soft" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="5"/></filter>
    <filter id="c-rim" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="3.5"/></filter>
    <clipPath id="c-face"><path d="M200 84C251 84 283 124 283 180C283 238 249 291 200 299C151 291 117 238 117 180C117 124 149 84 200 84Z"/></clipPath>
  </defs>
  <circle class="back" cx="200" cy="190" r="205" fill="url(#c-back)"/>
  <g class="body">
    <path d="M52 400C60 352 122 338 176 326C190 342 210 342 224 326C278 338 340 352 348 400Z" fill="url(#c-top)"/>
    <path d="M176 326C190 342 210 342 224 326" fill="none" stroke="var(--glow)" stroke-opacity=".35" stroke-width="2"/>
    <path d="M178 270L178 322C184 338 216 338 222 322L222 270Z" fill="#9b8fc6"/>
    <path d="M178 286C192 300 208 300 222 286L222 270L178 270Z" fill="#6f62a8" opacity=".7"/>
  </g>
  <g class="head">
    <path class="rim" d="M92 196C82 92 146 38 200 38C254 38 318 92 308 196C304 254 316 296 300 326C276 340 246 326 240 300L160 300C154 326 124 340 100 326C84 296 96 254 92 196Z" fill="none" stroke="var(--glow)" stroke-width="5" opacity=".55" filter="url(#c-rim)"/>
    <path d="M92 196C82 92 146 38 200 38C254 38 318 92 308 196C304 254 316 296 300 326C276 340 246 326 240 300L160 300C154 326 124 340 100 326C84 296 96 254 92 196Z" fill="url(#c-hair)"/>
    <path d="M200 84C251 84 283 124 283 180C283 238 249 291 200 299C151 291 117 238 117 180C117 124 149 84 200 84Z" fill="url(#c-skin)"/>
    <g clip-path="url(#c-face)">
      <rect x="110" y="80" width="180" height="225" fill="url(#c-shade)"/>
      <path d="M110 80L290 80L290 150C262 128 236 122 214 122C180 150 146 158 110 160Z" fill="#4a3a8a" opacity=".3" filter="url(#c-soft)"/>
      <ellipse cx="150" cy="226" rx="21" ry="12" fill="#ff8fb0" opacity=".3" filter="url(#c-soft)"/>
      <ellipse cx="250" cy="226" rx="21" ry="12" fill="#ff8fb0" opacity=".3" filter="url(#c-soft)"/>
    </g>
    <g class="brows" fill="none" stroke="#2b2456" stroke-width="4.5" stroke-linecap="round">
      <path class="brow l" d="M146 161Q162 152 181 158"/>
      <path class="brow r" d="M219 158Q238 152 254 161"/>
    </g>
    ${eye('l', 165)}${eye('r', 235)}
    <path d="M196 226Q201 233 207 227" fill="none" stroke="#8a7cba" stroke-width="2.6" stroke-linecap="round"/>
    <path class="mouth" d="" fill="#3d1f45" stroke="#b25f8e" stroke-width="4.2" stroke-linejoin="round" stroke-linecap="round"/>
    <path d="M113 190C104 108 150 62 206 64C262 64 300 108 288 192C282 156 262 124 232 110C236 124 230 138 220 146C222 130 214 116 204 110C182 136 150 150 128 150C120 162 116 176 113 190Z" fill="url(#c-hair)"/>
    <path d="M113 186C104 232 110 268 124 296C128 266 122 226 127 180Z" fill="url(#c-hair)"/>
    <path d="M288 188C296 232 290 268 276 296C272 266 278 226 273 180Z" fill="url(#c-hair)"/>
    <path d="M140 92Q196 54 262 92" fill="none" stroke="#8c80d6" stroke-width="5" stroke-linecap="round" opacity=".4" filter="url(#c-rim)"/>
    <path d="M150 118Q182 96 214 92" fill="none" stroke="#9d92e0" stroke-width="2" stroke-linecap="round" opacity=".45"/>
  </g>
</svg>`;
