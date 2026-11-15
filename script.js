/* =====================================================================
   1. Storm animation (canvas)
   ===================================================================== */
const cv = document.getElementById('cv');
const ctx = cv.getContext('2d');
let W, H, DPR, S, R, SEA;
let t, tw, tStart, last, flash, bolt, nextBolt, boat, splashes, foam, dark, swapped;
const cr1 = [0, 0, 0, 0], cr2 = [0, 0, 0, 0];

const OFF = [0, 0.035, 0.07, 0.105];              // layer offsets, fraction of H
const K1  = [0.6, 0.8, 0.95, 1.1];                // how tall wave 1 is on each layer
const COLORS = [['#8e9ea6', '#5f7a92'], ['#5b7892', '#3b5a79'], ['#3e5d7d', '#28425f'], ['#27415f', '#1b2f49']];
const SP1 = 0.55, SP2 = 0.65;                     // wave speeds (screen widths per second)

function fit() {
  DPR = Math.min(2, window.devicePixelRatio || 1);
  W = window.innerWidth; H = window.innerHeight;
  cv.width = W * DPR; cv.height = H * DPR;
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  SEA = H * 0.80;
  S = Math.max(0.9, Math.min(3, W / 1408 * 2.4));
  R = Math.max(0.8, Math.min(1.8, H / 450));
}
window.addEventListener('resize', fit);

function h(i, x) {
  let v = 0;
  const dx = x - cr1[i], wd = (dx > 0 ? 0.09 : 0.2) * W;                     // wave 1: big swell
  v += 0.17 * H * K1[i] * Math.exp(-Math.pow(dx / wd, 2));
  const sf = 1 / (1 + Math.exp((x - cr2[i]) / (0.045 * W)));                  // wave 2: floods the page
  const sb = 1 / (1 + Math.exp(-(x - (cr2[i] - 1.8 * W)) / (0.25 * W)));
  v += 1.1 * H * sf * sb;
  return v;
}
function rip(i, x) {
  const ch = 1 + Math.min(1, Math.max(0, tw) / 4) * 0.5;                      // sea gets rougher once it starts
  return (Math.sin(x * 0.012 + t * 1.6 + i * 1.7) * (4 - i * 0.4) +
          Math.sin(x * 0.034 - t * 2.4 + i) * 2 +
          Math.sin(x * 0.09 + t * 3.3)) * ch * R;
}
function layerSurf(i, x) { return SEA + OFF[i] * H - h(i, x) + rip(i, x); }
function seaLevel(x) { let m = 1e9; for (let i = 0; i < 4; i++) m = Math.min(m, layerSurf(i, x) - OFF[i] * H); return m; }
function topSurf(x)  { let m = 1e9; for (let i = 0; i < 4; i++) m = Math.min(m, layerSurf(i, x)); return m; }

const drops = Array.from({ length: 260 }, () => ({
  x: Math.random() * 2600, y: Math.random() * 1300,
  vy: 620 + Math.random() * 260, vx: -140 - Math.random() * 60
}));
function makeBolt() {
  const pts = [], branches = [], k = H / 450;
  let x = W * (0.1 + Math.random() * 0.8), y = 0;
  while (y < SEA * 0.97) {
    pts.push([x, y]);
    y += (14 + Math.random() * 16) * k;
    x += (Math.random() - 0.5) * 46 * k;
    if (Math.random() < 0.12 && pts.length > 3) {
      const br = [[x, y]]; let bx = x, by = y;
      for (let i = 0; i < 4; i++) { by += (14 + Math.random() * 12) * k; bx += (Math.random() - 0.3) * 40 * k; br.push([bx, by]); }
      branches.push(br);
    }
  }
  pts.push([x, SEA]);
  return { pts, branches, life: 0.45 };
}
function strokePath(p) {
  ctx.beginPath();
  p.forEach((q, i) => i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]));
  ctx.stroke();
}

function resetScene() {
  t = 0; tw = -10; tStart = null; last = null; flash = 0; bolt = null; nextBolt = 2;
  splashes = []; foam = [];
  dark = false; swapped = false;
  boat = { x: W * 0.84, y: SEA + 0.05 * H, a: 0 };
}

function step(dt) {
  t += dt;
  tw = tStart === null ? -10 : t - tStart;                    // waves wait until the pass is detected
  const c1 = W * SP1 * (tw - 0.3) - W * 0.30;
  const c2 = W * SP2 * (tw - 0.3) - W * 1.5;                  // wave 2 follows right behind wave 1
  for (let i = 0; i < 4; i++) { cr1[i] = c1 - i * 0.025 * W; cr2[i] = c2 - i * 0.03 * W; }

  nextBolt -= dt;
  if (nextBolt <= 0) { bolt = makeBolt(); flash = 1; nextBolt = 2.5 + Math.random() * 4; }
  if (bolt) { bolt.life -= dt; if (bolt.life <= 0) bolt = null; }
  flash = Math.max(0, flash - dt * 2.4);
  if (bolt && Math.random() < 0.25) flash = Math.min(1, flash + 0.4);

  [cr1, cr2].forEach(cr => {
    for (let i = 0; i < 4; i++) {
      const c = cr[i];
      if (c > 0 && c < W && Math.random() < 0.35) foam.push({
        x: c + (Math.random() - 0.5) * 20, y: layerSurf(i, c) + 4,
        vx: 40 + Math.random() * 200, vy: -(60 + Math.random() * 220), l: 1
      });
    }
  });

  // the big wave has covered the whole page: the game begins underneath it
  if (tStart !== null && !swapped && topSurf(0) < -4 && topSurf(W * 0.5) < -4 && topSurf(W) < -4) {
    swapped = true; dark = true; enterGame();
  }

  const b = boat;
  b.x = W * 0.84 + Math.sin(t * 0.9) * 4;
  const ref = x => seaLevel(x);
  const y = Math.max(ref(b.x) + 0.05 * H, H * 0.36);
  let target = Math.atan((ref(b.x + 24 * S) - ref(b.x - 24 * S)) / (48 * S));
  target = Math.max(-0.7, Math.min(0.7, target));
  if (ref(b.x) < H * 0.1) target += Math.sin(t * 2.2) * 0.18;
  b.y += (y - b.y) * Math.min(1, dt * 10);
  b.a += (target - b.a) * Math.min(1, dt * 7);

  drops.forEach(d => {
    d.x += d.vx * dt; d.y += d.vy * dt;
    const ts = topSurf(d.x);
    if (d.y > ts || d.y > H) {
      if (ts > 2 && Math.random() < 0.4) splashes.push({ x: d.x, y: ts, l: 1 });
      d.x = Math.random() * (W + 300); d.y = -10 - Math.random() * 60;
    }
  });
  splashes.forEach(s => s.l -= dt * 4);
  splashes = splashes.filter(s => s.l > 0);
  foam.forEach(p => { p.x += p.vx * dt; p.y += p.vy * dt; p.vy += 600 * dt; p.l -= dt * 0.9; });
  foam = foam.filter(p => p.l > 0);
}

function drawBolt() {
  if (!bolt) return;
  ctx.save();
  ctx.globalAlpha = Math.min(1, bolt.life / 0.25) * 0.4;
  ctx.shadowColor = dark ? '#bcd7ff' : '#7a93c4'; ctx.shadowBlur = 10;
  ctx.strokeStyle = dark ? '#dbe7ff' : '#5f78aa';
  ctx.lineWidth = 2.2 * R; ctx.lineJoin = 'round';
  strokePath(bolt.pts);
  ctx.lineWidth = 1.1 * R;
  bolt.branches.forEach(strokePath);
  ctx.restore();
}

function drawLayer(i) {
  const top = SEA + OFF[i] * H;
  ctx.beginPath();
  ctx.moveTo(0, H + 4);
  for (let x = 0; x <= W + 4; x += 4) ctx.lineTo(x, layerSurf(i, x));
  ctx.lineTo(W + 4, H + 4); ctx.closePath();
  const g = ctx.createLinearGradient(0, top, 0, H);
  g.addColorStop(0, COLORS[i][0]); g.addColorStop(1, COLORS[i][1]);
  ctx.globalAlpha = 0.97; ctx.fillStyle = g; ctx.fill(); ctx.globalAlpha = 1;

  ctx.beginPath();
  for (let x = 0; x <= W + 4; x += 4) { const y = layerSurf(i, x); x ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }
  ctx.strokeStyle = `rgba(235,243,250,${0.35 + flash * 0.15})`; ctx.lineWidth = 2 * R; ctx.stroke();

  ctx.fillStyle = '#f4fbff';
  for (let x = 0; x <= W; x += 5) {
    if (h(i, x) > H * 0.04) {
      const n = Math.sin(x * 12.9 + t * 18 + i);
      ctx.globalAlpha = 0.25 + 0.4 * Math.abs(n);
      ctx.beginPath(); ctx.arc(x, layerSurf(i, x) + 2 + n * 3 * R, (1.3 + Math.abs(n) * 1.5) * R, 0, 7); ctx.fill();
    }
  }
  ctx.globalAlpha = 1;
}

function drawBoat(b) {
  const flap = Math.sin(t * 12) * 4;
  ctx.save();
  ctx.translate(b.x, b.y);
  ctx.rotate(b.a);
  ctx.scale(S, S);

  ctx.beginPath();
  ctx.moveTo(-46, -8);
  ctx.bezierCurveTo(-20, -4, 20, -6, 52, -18);
  ctx.bezierCurveTo(48, 0, 32, 15, 10, 17);
  ctx.lineTo(-28, 17);
  ctx.bezierCurveTo(-40, 15, -48, 4, -46, -8);
  ctx.closePath();
  const hg = ctx.createLinearGradient(0, -18, 0, 17);
  hg.addColorStop(0, '#8b5a33'); hg.addColorStop(1, '#4a2a14');
  ctx.fillStyle = hg; ctx.fill();
  ctx.save(); ctx.clip();
  ctx.fillStyle = '#8c1f1f'; ctx.fillRect(-60, 8, 130, 14);
  ctx.strokeStyle = '#efe7d6'; ctx.lineWidth = 2.5;
  ctx.beginPath(); ctx.moveTo(-46, -3); ctx.bezierCurveTo(-20, 1, 20, -1, 52, -13); ctx.stroke();
  ctx.restore();
  ctx.strokeStyle = '#2d180b'; ctx.lineWidth = 1.2; ctx.stroke();

  ctx.fillStyle = '#e4dccb';
  ctx.beginPath(); ctx.roundRect(-24, -24, 30, 16, 3); ctx.fill();
  ctx.fillStyle = '#4d5560';
  ctx.beginPath(); ctx.roundRect(-26, -27, 34, 4, 2); ctx.fill();
  ctx.fillStyle = `rgba(255,214,107,${0.75 + flash * 0.25})`;
  ctx.fillRect(-20, -20, 7, 6); ctx.fillRect(-9, -20, 7, 6);

  ctx.strokeStyle = '#d9d2c2'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(10, -16); ctx.lineTo(48, -24); ctx.stroke();
  for (let x = 14; x <= 46; x += 8) {
    ctx.beginPath(); ctx.moveTo(x, -16 - (x - 10) * 0.2); ctx.lineTo(x, -21 - (x - 10) * 0.2); ctx.stroke();
  }

  ctx.strokeStyle = '#3a2a1c'; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(16, -14); ctx.lineTo(16, -92); ctx.stroke();
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(16, -28); ctx.lineTo(-28, -30 + flap * 0.3); ctx.stroke();

  ctx.fillStyle = 'rgba(236,231,217,0.97)';
  ctx.beginPath(); ctx.moveTo(15, -90);
  ctx.quadraticCurveTo(-8 - flap, -60, -28, -32 + flap * 0.3);
  ctx.lineTo(15, -30); ctx.closePath(); ctx.fill();
  ctx.beginPath(); ctx.moveTo(18, -90);
  ctx.quadraticCurveTo(40 + flap, -58, 50, -22);
  ctx.lineTo(18, -26); ctx.closePath(); ctx.fill();

  ctx.strokeStyle = 'rgba(80,90,105,0.7)'; ctx.lineWidth = 0.8;
  ctx.beginPath(); ctx.moveTo(16, -92); ctx.lineTo(52, -18);
  ctx.moveTo(16, -92); ctx.lineTo(-44, -10); ctx.stroke();

  const lg = ctx.createRadialGradient(16, -94, 0, 16, -94, 16);
  lg.addColorStop(0, 'rgba(255,230,150,0.9)'); lg.addColorStop(1, 'rgba(255,230,150,0)');
  ctx.fillStyle = lg; ctx.beginPath(); ctx.arc(16, -94, 16, 0, 7); ctx.fill();
  ctx.fillStyle = '#fff4c8'; ctx.beginPath(); ctx.arc(16, -94, 2.5, 0, 7); ctx.fill();
  ctx.restore();
}

function drawRain() {
  ctx.strokeStyle = dark ? 'rgba(190,210,235,0.16)' : 'rgba(40,66,100,0.16)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  drops.forEach(d => { ctx.moveTo(d.x, d.y); ctx.lineTo(d.x - d.vx * 0.022, d.y - d.vy * 0.022); });
  ctx.stroke();
  ctx.strokeStyle = 'rgba(235,243,250,0.35)';
  splashes.forEach(s => {
    ctx.globalAlpha = Math.max(0, s.l) * 0.6;
    ctx.beginPath(); ctx.ellipse(s.x, s.y, 4 * (1 - s.l) + 1, 1.2, 0, 0, 7); ctx.stroke();
  });
  ctx.fillStyle = '#fff';
  foam.forEach(p => { ctx.globalAlpha = Math.max(0, p.l) * 0.8; ctx.beginPath(); ctx.arc(p.x, p.y, 2.2, 0, 7); ctx.fill(); });
  ctx.globalAlpha = 1;
}

function draw() {
  ctx.clearRect(0, 0, W, H);
  drawBolt();
  drawLayer(0);
  drawLayer(1);
  drawBoat(boat);
  drawLayer(2);
  drawLayer(3);
  drawRain();
  if (flash > 0) {
    ctx.fillStyle = dark ? `rgba(200,220,255,${flash * 0.07})` : `rgba(255,255,255,${flash * 0.18})`;
    ctx.fillRect(0, 0, W, H);
  }
}

function loop(ts) {
  if (last === null) last = ts;
  const dt = Math.min(0.033, (ts - last) / 1000);
  last = ts;
  step(dt);
  draw();
  requestAnimationFrame(loop);
}

/* =====================================================================
   2. NFC detection -> waves -> game
   ===================================================================== */
const $ = id => document.getElementById(id);
const nfcBtn = $('nfc'), statusEl = $('status');
let scanning = false;

function setStatus(s) { statusEl.textContent = s; }

// Called once a pass is detected (or the visitor explores without one)
function begin(msg) {
  if (tStart !== null) return;
  tStart = t;
  nfcBtn.textContent = 'The fates answer…';
  setStatus(msg || 'Pass detected. The gods are stirring…');
}

async function listenForPass() {
  if (tStart !== null || scanning) return;
  if (!('NDEFReader' in window)) {
    begin("NFC isn't supported in this browser, so we began without a card.");
    return;
  }
  try {
    const reader = new NDEFReader();
    await reader.scan();                                   // needs HTTPS + a tap on the button + permission
    scanning = true;
    nfcBtn.textContent = 'Hold your pass to the device…';
    setStatus('Listening for your pass…');
    reader.onreading = () => { scanning = false; begin('Pass detected. The gods are stirring…'); };
    reader.onreadingerror = () => setStatus("Couldn't read that pass. Try again.");
  } catch (e) {
    if (e && e.name === 'NotAllowedError') {
      setStatus("NFC permission was blocked. Use 'Explore without a card' to continue.");
    } else {
      begin('NFC is not available here, so we began without a card.');
    }
  }
}
nfcBtn.addEventListener('click', listenForPass);
$('explore').addEventListener('click', e => { e.preventDefault(); begin('Exploring without a card…'); });

// Swap the page to the game while the big wave covers everything
function enterGame() {
  document.body.classList.add('night', 'playing');
  $('chapter').innerHTML = 'Chapter II &nbsp;/&nbsp; The Table';
  game.phase = 'bet';
  game.bank = 500; game.bet = 20;
  game.dealer = []; game.player = [];
  prev.d = 0; prev.p = 0;
  setMsg('Place your bet.');
  renderAll();
}

function backToStart() {
  document.body.classList.remove('night', 'playing');
  $('chapter').innerHTML = 'Chapter I &nbsp;/&nbsp; The Crossing';
  nfcBtn.textContent = 'Tap your NFC pass to begin';
  setStatus(''); scanning = false;
  resetScene();
}
$('replay').onclick = backToStart;

/* =====================================================================
   3. Blackjack
   ===================================================================== */
const SUITS = ['♠', '♥', '♦', '♣'];
const RANKS = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K'];
const CHIPS = [10, 20, 50, 100];
const game = { bank: 500, bet: 20, phase: 'bet', deck: [], dealer: [], player: [], reveal: false };
const prev = { d: 0, p: 0 };
const sleep = ms => new Promise(r => setTimeout(r, ms));

function newDeck() {
  const d = [];
  for (let n = 0; n < 4; n++) for (const s of SUITS) for (const r of RANKS) d.push({ r, s });
  for (let i = d.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [d[i], d[j]] = [d[j], d[i]]; }
  return d;
}
function drawCard() { if (game.deck.length < 20) game.deck = newDeck(); return game.deck.pop(); }
function value(hand) {
  let total = 0, aces = 0;
  for (const c of hand) {
    if (c.r === 'A') { aces++; total += 11; }
    else if ('JQK'.includes(c.r) || c.r === '10') total += 10;
    else total += +c.r;
  }
  while (total > 21 && aces) { total -= 10; aces--; }
  return total;
}
const isBJ = hand => hand.length === 2 && value(hand) === 21;

function setMsg(s) { $('msg').textContent = s; }

function cardEl(c, hidden, fresh) {
  const d = document.createElement('div');
  d.className = 'card' + (hidden ? ' back' : '') + (!hidden && (c.s === '♥' || c.s === '♦') ? ' red' : '') + (fresh ? ' new' : '');
  if (hidden) d.textContent = 'Ω';
  else d.innerHTML = `<span class="r">${c.r}</span><span class="s">${c.s}</span><span class="r2">${c.r}</span>`;
  return d;
}
function renderHands() {
  const dEl = $('dealer'), pEl = $('player');
  dEl.innerHTML = ''; pEl.innerHTML = '';
  game.dealer.forEach((c, i) => dEl.append(cardEl(c, i === 1 && !game.reveal, i >= prev.d)));
  game.player.forEach((c, i) => pEl.append(cardEl(c, false, i >= prev.p)));
  prev.d = game.dealer.length; prev.p = game.player.length;
  $('dtotal').textContent = game.dealer.length ? (game.reveal ? value(game.dealer) : '') : '';
  $('ptotal').textContent = game.player.length ? value(game.player) : '';
}
function renderBank() { $('bank').textContent = game.bank; $('bet').textContent = game.bet; }

function mk(cls, label, fn) {
  const b = document.createElement('button');
  b.className = cls + ' mono'; b.textContent = label; b.onclick = fn;
  return b;
}
function renderControls() {
  const c = $('controls'); c.innerHTML = '';
  if (game.phase === 'bet') {
    if (game.bet > game.bank) game.bet = CHIPS.filter(v => v <= game.bank).pop() || 0;
    CHIPS.filter(v => v <= game.bank).forEach(v =>
      c.append(mk('chip' + (game.bet === v ? ' on' : ''), v, () => { game.bet = v; renderBank(); renderControls(); })));
    c.append(mk('act', 'Deal', deal));
  } else if (game.phase === 'play') {
    c.append(mk('act', 'Hit', hit));
    c.append(mk('act', 'Stand', stand));
    if (game.player.length === 2 && game.bank >= game.bet) c.append(mk('act', 'Double', doubleDown));
  } else if (game.phase === 'done') {
    if (game.bank < 10) c.append(mk('act', 'Start over', () => { game.bank = 500; game.bet = 20; nextHand(); }));
    else c.append(mk('act', 'Next hand', nextHand));
  }
}
function renderAll() { renderHands(); renderBank(); renderControls(); }

function nextHand() {
  game.phase = 'bet'; game.dealer = []; game.player = []; game.reveal = false;
  prev.d = 0; prev.p = 0;
  setMsg('Place your bet.');
  renderAll();
}

async function deal() {
  if (game.bet <= 0 || game.bet > game.bank) return;
  game.bank -= game.bet;
  game.phase = 'dealing'; game.reveal = false;
  game.dealer = []; game.player = []; prev.d = 0; prev.p = 0;
  renderBank(); renderControls(); setMsg('');
  for (const who of ['player', 'dealer', 'player', 'dealer']) {
    game[who].push(drawCard()); renderHands(); await sleep(260);
  }
  if (isBJ(game.player) || isBJ(game.dealer)) return finish();
  game.phase = 'play';
  setMsg('Hit, stand, or double?');
  renderControls();
}
async function hit() {
  game.player.push(drawCard()); renderHands();
  const v = value(game.player);
  if (v > 21) return finish();
  if (v === 21) return stand();
}
async function doubleDown() {
  game.bank -= game.bet; game.bet *= 2; renderBank();
  game.player.push(drawCard()); renderHands();
  if (value(game.player) > 21) return finish();
  return stand();
}
async function stand() {
  game.phase = 'dealer'; renderControls();
  game.reveal = true; renderHands(); await sleep(500);
  while (value(game.dealer) < 17) { game.dealer.push(drawCard()); renderHands(); await sleep(600); }
  finish();
}
function finish() {
  game.reveal = true; renderHands();
  const p = value(game.player), d = value(game.dealer), b = game.bet;
  let text;
  if (isBJ(game.player) && isBJ(game.dealer)) { game.bank += b; text = 'Two blackjacks. The gods call it even.'; }
  else if (isBJ(game.player)) { game.bank += b * 2.5; text = `Blackjack! Athena blesses you with ${b * 1.5} drachmas.`; }
  else if (isBJ(game.dealer)) { text = 'The house has blackjack. The Fates were against you.'; }
  else if (p > 21) { text = 'Bust! Poseidon claims your hand.'; }
  else if (d > 21) { game.bank += b * 2; text = `The house busts. Zeus grants you ${b} drachmas.`; }
  else if (p > d) { game.bank += b * 2; text = `You win ${b} drachmas. The gods smile.`; }
  else if (p < d) { text = 'The house wins this round.'; }
  else { game.bank += b; text = 'A push. The gods are undecided.'; }
  if (game.bank < 10) text += ' Your purse is empty.';
  game.phase = 'done';
  setMsg(text);
  renderBank(); renderControls();
}

/* ---------- go ---------- */
fit();
resetScene();
requestAnimationFrame(loop);
