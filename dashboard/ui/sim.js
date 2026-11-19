// Factory Sim: replays recorded Renode runs (sim_trace events) on a 3D model of the board.
// Each recording is a set of clips, one per Robot test, holding LED, button and console events
// in virtual time. The page follows the selected run live and auto-plays new recordings.
import * as THREE from "three";
import { OrbitControls } from "three/addons/OrbitControls.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtT = (t) => `${t.toFixed(3)} s`;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

// =========================================================================================
// Board models. Millimetres; x = width, z = depth (towards the viewer), PCB top at y = 0.8.
// Only `leds`/`buttons` ids from factory/boards.yaml are driven by recordings; `decor` parts
// are drawn for recognisability and never light up.
// =========================================================================================
const BOARDS = {
  stm32f4_disco: {
    name: "STM32F4 Discovery", w: 97, d: 66, pcb: 0x1d4a8c, silk: "#e9eef5",
    chips: [{ x: 4, z: 4, s: 14, label: "STM32F407", sub: "Cortex-M4", pins: 25 },
            { x: -6, z: -21, s: 7, label: "ST-LINK", sub: "V2", pins: 12 }],
    leds: { led0: { x: 6, z: 26, color: 0x22e06a, silk: "LD4" } },
    decorLeds: [{ x: 1, z: 26, color: 0xff9a1f, silk: "LD3" }, { x: 11, z: 26, color: 0xff3030, silk: "LD5" },
                { x: 16, z: 26, color: 0x3f7bff, silk: "LD6" }],
    power: { x: -30, z: -27, silk: "PWR" },
    tx: { x: -24, z: -27, silk: "COM" },
    buttons: { sw0: { x: -24, z: 22, cap: 0x2f6be0, silk: "B1 USER" } },
    decorButtons: [{ x: 28, z: 22, cap: 0x202020, silk: "B2 RESET" }],
    usb: [{ x: -6, z: -33, w: 8, d: 7, h: 4 }, { x: -6, z: 33, w: 7, d: 6, h: 3 }],
    headers: [{ x: -43, z: 0, cols: 2, rows: 25, axis: "z" }, { x: 43, z: 0, cols: 2, rows: 25, axis: "z" }],
    crystals: [{ x: -10, z: 6 }, { x: -16, z: -16 }],
  },
  nrf52840dk: {
    name: "nRF52840 DK", w: 136, d: 63, pcb: 0x175fa8, silk: "#eaf2fb",
    chips: [{ x: 18, z: -4, s: 8, label: "nRF52840", sub: "QIAA", pins: 18 },
            { x: -36, z: -6, s: 8, label: "IF MCU", sub: "J-Link OB", pins: 16 }],
    leds: { led0: { x: 46, z: 22, color: 0x22e06a, silk: "LED1" } },
    decorLeds: [{ x: 52, z: 22, color: 0x22e06a, silk: "LED2" }, { x: 58, z: 22, color: 0x22e06a, silk: "LED3" },
                { x: 64, z: 22, color: 0x22e06a, silk: "LED4" }],
    power: { x: -56, z: 14, silk: "PWR" },
    tx: { x: -50, z: 14, silk: "IF" },
    buttons: { sw0: { x: 46, z: 12, cap: 0x2a2a2a, silk: "BUTTON1" } },
    decorButtons: [{ x: 54, z: 12, cap: 0x2a2a2a, silk: "BUTTON2" }, { x: 62, z: 12, cap: 0x2a2a2a, silk: "BUTTON3" },
                   { x: -20, z: 22, cap: 0x2a2a2a, silk: "RESET" }],
    usb: [{ x: -66, z: -14, w: 8, d: 8, h: 3, axis: "x" }],
    headers: [{ x: 18, z: -28, cols: 1, rows: 32, axis: "x" }, { x: 18, z: 28, cols: 1, rows: 24, axis: "x" }],
    crystals: [{ x: 30, z: -10 }],
    antenna: { x: 60, z: -18 },
  },
  hifive1: {
    name: "HiFive1", w: 68.6, d: 53.3, pcb: 0x23262b, silk: "#f2f2f2",
    chips: [{ x: 6, z: 2, s: 7, label: "FE310", sub: "RISC-V", pins: 12 },
            { x: -17, z: -8, s: 6, label: "J-Link", sub: "OB", pins: 10 }],
    leds: { led0: { x: 22, z: -14, color: 0x22e06a, silk: "LED G" } },
    decorLeds: [{ x: 26, z: -14, color: 0xff3030, silk: "R" }, { x: 30, z: -14, color: 0x3f7bff, silk: "B" }],
    power: { x: -28, z: 14, silk: "PWR" },
    tx: { x: -22, z: 14, silk: "UART" },
    buttons: {},
    decorButtons: [{ x: -24, z: 4, cap: 0x202020, silk: "RESET" }],
    usb: [{ x: -34, z: -14, w: 7, d: 6, h: 3, axis: "x" }],
    headers: [{ x: 8, z: -24, cols: 1, rows: 18, axis: "x" }, { x: 10, z: 24, cols: 1, rows: 14, axis: "x" }],
    crystals: [{ x: 16, z: 6 }],
  },
  rpi_pico: {
    name: "Raspberry Pi Pico", w: 51, d: 21, pcb: 0x1f6b3a, silk: "#f4f4ee",
    chips: [{ x: 2, z: 0, s: 7, label: "RP2040", sub: "", pins: 14 }, { x: 12, z: 0, s: 4, label: "QSPI", sub: "", pins: 4 }],
    leds: { led0: { x: -19, z: -6, color: 0x22e06a, silk: "LED" } },
    decorLeds: [],
    buttons: {},
    decorButtons: [{ x: -13, z: 4, cap: 0xe8e8e8, silk: "BOOTSEL" }],
    usb: [{ x: -24, z: 0, w: 6, d: 8, h: 3, axis: "x" }],
    headers: [{ x: 0, z: -9.5, cols: 1, rows: 20, axis: "x" }, { x: 0, z: 9.5, cols: 1, rows: 20, axis: "x" }],
    crystals: [{ x: -5, z: 5 }],
  },
};

/** A plain board for ids without a model, with one LED/button per id seen in the recording. */
function genericBoard(id, ledIds, buttonIds) {
  const leds = {}, buttons = {};
  ledIds.forEach((l, i) => { leds[l] = { x: 6 + i * 6, z: 16, color: 0x22e06a, silk: l }; });
  buttonIds.forEach((b, i) => { buttons[b] = { x: -20 + i * 9, z: 16, cap: 0x2f6be0, silk: b }; });
  return {
    name: id || "board", w: 76, d: 50, pcb: 0x2a5a3a, silk: "#eef3ee",
    chips: [{ x: 0, z: -4, s: 10, label: id || "MCU", sub: "", pins: 16 }],
    leds, decorLeds: [], buttons, decorButtons: [], power: { x: -30, z: -18, silk: "PWR" }, tx: { x: -24, z: -18, silk: "TX" },
    usb: [{ x: -38, z: 0, w: 7, d: 8, h: 3, axis: "x" }], headers: [], crystals: [],
  };
}

// =========================================================================================
// Scene
// =========================================================================================
const TOP = 0.8;

const view = (() => {
  const canvas = $("scene");
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  } catch (e) {
    $("stage").insertAdjacentHTML("beforeend", `<p class="waiting">WebGL is unavailable in this browser, so only the timeline and console are shown.</p>`);
    return null;
  }
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 1, 1, 2000);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = !reducedMotion;
  controls.maxPolarAngle = Math.PI * 0.47;

  scene.add(new THREE.HemisphereLight(0xffffff, 0x3a4450, 1.5));
  const sun = new THREE.DirectionalLight(0xffffff, 1.9);
  sun.position.set(-50, 140, 70);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -110, right: 110, top: 110, bottom: -110, near: 10, far: 400 });
  sun.shadow.bias = -0.0004;
  scene.add(sun);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(800, 800), new THREE.ShadowMaterial({ opacity: 0.2 }));
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -6;
  ground.receiveShadow = true;
  scene.add(ground);

  const halo = haloTexture();
  let model = null;

  function resize() {
    const { clientWidth: w, clientHeight: h } = canvas.parentElement;
    renderer.setSize(w, h, false);
    camera.aspect = w / Math.max(h, 1);
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(canvas.parentElement);
  resize();

  function setBoard(spec, key) {
    if (model && model.key === key) return model;
    if (model) {
      scene.remove(model.group);
      model.group.traverse((o) => { o.geometry?.dispose(); [].concat(o.material || []).forEach((m) => { m.map?.dispose(); m.dispose(); }); });
    }
    model = buildBoard(spec, halo);
    model.key = key;
    scene.add(model.group);
    const span = Math.max(spec.w, spec.d * 1.5);
    camera.position.set(span * 0.15, span * 1.05, span * 1.05);
    controls.target.set(0, 0, 2);
    controls.minDistance = span * 0.4;
    controls.maxDistance = span * 3;
    controls.update();
    return model;
  }

  const v = new THREE.Vector3();
  function place(tags) {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    for (const { el, pos } of tags) {
      v.copy(pos).project(camera);
      const hidden = v.z > 1;
      el.style.display = hidden ? "none" : "";
      el.style.left = `${((v.x + 1) / 2) * w}px`;
      el.style.top = `${((1 - v.y) / 2) * h}px`;
    }
  }

  function frame() {
    controls.update();
    renderer.render(scene, camera);
  }

  return { setBoard, frame, place, get model() { return model; } };
})();

function haloTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.25, "rgba(255,255,255,0.55)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function mulberry32(a) {
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function textTexture(w, h, draw) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  draw(c.getContext("2d"), w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

function instanced(geom, mat, positions, rotY = []) {
  const mesh = new THREE.InstancedMesh(geom, mat, Math.max(positions.length, 1));
  const o = new THREE.Object3D();
  positions.forEach((p, i) => {
    o.position.set(p[0], p[1], p[2]);
    o.rotation.set(0, rotY[i] || 0, 0);
    o.updateMatrix();
    mesh.setMatrixAt(i, o.matrix);
  });
  mesh.count = positions.length;
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

function buildBoard(spec, halo) {
  const group = new THREE.Group();
  const keepOut = [];
  const block = (x, z, w, d) => keepOut.push([x - w / 2 - 1, z - d / 2 - 1, x + w / 2 + 1, z + d / 2 + 1]);
  const metal = new THREE.MeshStandardMaterial({ color: 0xc9ccd1, metalness: 0.9, roughness: 0.32 });
  const gold = new THREE.MeshStandardMaterial({ color: 0xd8b25a, metalness: 0.95, roughness: 0.3 });
  const black = new THREE.MeshStandardMaterial({ color: 0x18191b, roughness: 0.55 });

  // PCB with rounded-ish look via a slightly inset top layer.
  const pcbMat = new THREE.MeshStandardMaterial({ color: spec.pcb, roughness: 0.55, metalness: 0.05 });
  const pcb = new THREE.Mesh(new THREE.BoxGeometry(spec.w, 1.6, spec.d), pcbMat);
  pcb.receiveShadow = pcb.castShadow = true;
  group.add(pcb);

  // Silkscreen: outline, mounting holes, board name and part designators, drawn once to a texture.
  const K = 10;
  const silkParts = [];
  const silk = textTexture(Math.round(spec.w * K), Math.round(spec.d * K), (g, W, H) => {
    const cx = (x) => (x + spec.w / 2) * K, cz = (z) => (z + spec.d / 2) * K;
    g.strokeStyle = spec.silk; g.fillStyle = spec.silk; g.lineWidth = 3;
    g.strokeRect(14, 14, W - 28, H - 28);
    for (const [hx, hy] of [[34, 34], [W - 34, 34], [34, H - 34], [W - 34, H - 34]]) {
      g.beginPath(); g.arc(hx, hy, 17, 0, Math.PI * 2); g.fillStyle = "#c8a24e"; g.fill();
      g.beginPath(); g.arc(hx, hy, 10, 0, Math.PI * 2); g.fillStyle = "#0b0b0b"; g.fill();
    }
    g.fillStyle = spec.silk;
    // Bottom-right, stopping short of any header along the right edge, shrunk to fit 38% of the width.
    const rightHeaders = (spec.headers || []).filter((h) => h.axis === "z" && h.x > 0).map((h) => h.x - h.cols * 1.27 - 2);
    const right = cx(Math.min(spec.w / 2 - 5, ...rightHeaders));
    let size = Math.round(Math.min(spec.d, 60) * 0.55);
    const nameFont = (px) => `600 ${px}px "IBM Plex Sans Condensed", "Arial Narrow", sans-serif`;
    g.font = nameFont(size);
    while (size > 10 && g.measureText(spec.name).width > W * 0.38) g.font = nameFont(--size);
    g.textAlign = "right"; g.textBaseline = "bottom";
    g.fillText(spec.name, right, H - 24);
    g.font = `500 22px "JetBrains Mono", monospace`;
    g.textAlign = "center"; g.textBaseline = "top";
    for (const p of silkParts) g.fillText(p.text, cx(p.x), cz(p.z) + p.dy * K);
  });

  // Chips with pins and a printed label.
  for (const c of spec.chips || []) {
    const body = new THREE.Mesh(new THREE.BoxGeometry(c.s, 1.3, c.s), black);
    body.position.set(c.x, TOP + 0.65, c.z);
    body.castShadow = body.receiveShadow = true;
    group.add(body);
    const lbl = new THREE.Mesh(new THREE.PlaneGeometry(c.s * 0.86, c.s * 0.86), new THREE.MeshStandardMaterial({
      transparent: true, roughness: 0.7,
      map: textTexture(256, 256, (g) => {
        g.fillStyle = "#9aa0a6"; g.textAlign = "center";
        g.font = `600 ${c.label.length > 7 ? 34 : 42}px "JetBrains Mono", monospace`; g.fillText(c.label, 128, 118);
        g.font = `500 26px "JetBrains Mono", monospace`; g.fillText(c.sub || "", 128, 160);
        g.beginPath(); g.arc(30, 30, 10, 0, Math.PI * 2); g.fillStyle = "#3a3d41"; g.fill();
      }),
    }));
    lbl.rotation.x = -Math.PI / 2;
    lbl.position.set(c.x, TOP + 1.31, c.z);
    group.add(lbl);
    const pins = [], rot = [], n = c.pins, pitch = (c.s * 0.84) / n;
    for (let side = 0; side < 4; side++) {
      for (let i = 0; i < n; i++) {
        const along = (i - (n - 1) / 2) * pitch, out = c.s / 2 + 0.45;
        const [px, pz] = [[along, -out], [out, along], [along, out], [-out, along]][side];
        pins.push([c.x + px, TOP + 0.12, c.z + pz]);
        rot.push(side % 2 ? Math.PI / 2 : 0);
      }
    }
    group.add(instanced(new THREE.BoxGeometry(Math.min(0.28, pitch * 0.6), 0.22, 1.0), metal, pins, rot));
    block(c.x, c.z, c.s + 2, c.s + 2);
  }

  // Pin headers: a black plastic strip with gold pins.
  for (const h of spec.headers || []) {
    const along = h.rows * 2.54, across = h.cols * 2.54;
    const [w, d] = h.axis === "x" ? [along, across] : [across, along];
    const base = new THREE.Mesh(new THREE.BoxGeometry(w, 2.5, d), black);
    base.position.set(h.x, TOP + 1.25, h.z);
    base.castShadow = base.receiveShadow = true;
    group.add(base);
    const pins = [];
    for (let r = 0; r < h.rows; r++) {
      for (let c = 0; c < h.cols; c++) {
        const a = (r - (h.rows - 1) / 2) * 2.54, b = (c - (h.cols - 1) / 2) * 2.54;
        pins.push(h.axis === "x" ? [h.x + a, TOP + 4.2, h.z + b] : [h.x + b, TOP + 4.2, h.z + a]);
      }
    }
    group.add(instanced(new THREE.BoxGeometry(0.64, 6, 0.64), gold, pins));
    block(h.x, h.z, w, d);
  }

  for (const u of spec.usb || []) {
    const m = new THREE.Mesh(new THREE.BoxGeometry(u.w, u.h, u.d), metal);
    m.position.set(u.x, TOP + u.h / 2, u.z);
    m.castShadow = true;
    group.add(m);
    block(u.x, u.z, u.w, u.d);
  }
  for (const x of spec.crystals || []) {
    const m = new THREE.Mesh(new THREE.CapsuleGeometry(1.2, 2.8, 4, 12), metal);
    m.rotation.z = Math.PI / 2;
    m.scale.set(1, 1, 0.55);
    m.position.set(x.x, TOP + 0.75, x.z);
    m.castShadow = true;
    group.add(m);
    block(x.x, x.z, 6, 3);
  }
  if (spec.antenna) {
    const m = new THREE.Mesh(new THREE.BoxGeometry(16, 0.1, 6), gold);
    m.position.set(spec.antenna.x, TOP + 0.05, spec.antenna.z);
    group.add(m);
    block(spec.antenna.x, spec.antenna.z, 16, 6);
  }

  // LEDs: emissive body + halo sprite + point light, driven by setLed().
  const leds = {};
  const addLed = (p, id) => {
    // Tinted lens, like a real indicator LED: coloured when off, glowing its colour when on.
    const lens = new THREE.Color(p.color).lerp(new THREE.Color(0xffffff), 0.35);
    const mat = new THREE.MeshStandardMaterial({ color: lens, emissive: p.color, emissiveIntensity: 0, roughness: 0.3 });
    const body = new THREE.Mesh(new THREE.BoxGeometry(1.8, 0.9, 1.0), mat);
    body.position.set(p.x, TOP + 0.45, p.z);
    body.castShadow = true;
    group.add(body);
    const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: halo, color: p.color, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0 }));
    glow.scale.set(11, 11, 1);
    glow.position.set(p.x, TOP + 1.4, p.z);
    group.add(glow);
    const light = new THREE.PointLight(p.color, 0, 30, 1.6);
    light.position.set(p.x, TOP + 3, p.z);
    group.add(light);
    block(p.x, p.z, 3, 2);
    silkParts.push({ x: p.x, z: p.z, dy: 1.2, text: p.silk || id || "" });
    return { mat, glow, light, pos: new THREE.Vector3(p.x, TOP + 2.2, p.z) };
  };
  for (const [id, p] of Object.entries(spec.leds || {})) leds[id] = addLed(p, id);
  for (const p of spec.decorLeds || []) addLed(p);
  const power = spec.power ? addLed({ ...spec.power, color: 0xff2a1a }) : null;
  const tx = spec.tx ? addLed({ ...spec.tx, color: 0xffa51f }) : null;

  // Buttons: square metal body with a coloured cap that travels down when pressed.
  const buttons = {};
  const addButton = (p, id) => {
    const base = new THREE.Mesh(new THREE.BoxGeometry(6, 1.8, 6), metal);
    base.position.set(p.x, TOP + 0.9, p.z);
    base.castShadow = true;
    group.add(base);
    const cap = new THREE.Mesh(new THREE.CylinderGeometry(1.9, 1.9, 1.6, 28), new THREE.MeshStandardMaterial({ color: p.cap, roughness: 0.45 }));
    cap.position.set(p.x, TOP + 2.5, p.z);
    cap.castShadow = true;
    group.add(cap);
    const ring = new THREE.Mesh(new THREE.RingGeometry(3.6, 4.4, 40), new THREE.MeshBasicMaterial({ color: 0x5aa0ff, transparent: true, opacity: 0, depthWrite: false }));
    ring.rotation.x = -Math.PI / 2;
    ring.position.set(p.x, TOP + 1.85, p.z);
    group.add(ring);
    block(p.x, p.z, 7, 7);
    silkParts.push({ x: p.x, z: p.z, dy: 3.6, text: p.silk || id || "" });
    return { cap, ring, upY: TOP + 2.5, pos: new THREE.Vector3(p.x, TOP + 4, p.z) };
  };
  for (const [id, p] of Object.entries(spec.buttons || {})) buttons[id] = addButton(p, id);
  for (const p of spec.decorButtons || []) addButton(p);
  if (spec.power) silkParts.push({ x: spec.power.x, z: spec.power.z, dy: 1.2, text: spec.power.silk });

  // Scatter passives (resistors/capacitors) outside every part's footprint, seeded per board.
  const rnd = mulberry32(spec.w * 1000 + spec.d);
  const caps = [], capRot = [];
  for (let tries = 0; tries < 900 && caps.length < Math.round((spec.w * spec.d) / 55); tries++) {
    const x = (rnd() - 0.5) * (spec.w - 10), z = (rnd() - 0.5) * (spec.d - 10);
    if (keepOut.some(([a, b, c, d]) => x > a && x < c && z > b && z < d)) continue;
    caps.push([x, TOP + 0.3, z]);
    capRot.push(rnd() < 0.5 ? 0 : Math.PI / 2);
    keepOut.push([x - 1.2, z - 1.2, x + 1.2, z + 1.2]);
  }
  const passive = instanced(new THREE.BoxGeometry(1.6, 0.6, 0.8), new THREE.MeshStandardMaterial({ color: 0x8a6b4a, roughness: 0.6 }), caps, capRot);
  passive.castShadow = false;
  group.add(passive);

  const silkMesh = new THREE.Mesh(new THREE.PlaneGeometry(spec.w, spec.d), new THREE.MeshStandardMaterial({ map: silk, transparent: true, roughness: 0.8 }));
  silkMesh.rotation.x = -Math.PI / 2;
  silkMesh.position.y = TOP + 0.01;
  group.add(silkMesh);

  const setLed = (l, on) => {
    // Moderate values: ACES tone mapping bleaches strongly over-driven colours to white.
    l.mat.emissiveIntensity = on ? 1.6 : 0.0;
    l.glow.material.opacity = on ? 0.8 : 0;
    l.light.intensity = on ? 350 : 0;
  };
  return {
    group, leds, buttons, power, tx,
    setLed: (id, on) => leds[id] && setLed(leds[id], on),
    setPower: (on) => power && setLed(power, on),
    setTx: (on) => tx && setLed(tx, on),
    setButton: (id, pressed) => {
      const b = buttons[id];
      if (!b) return;
      b.cap.position.y = pressed ? b.upY - 1.0 : b.upY;
      b.ring.material.opacity = pressed ? 0.85 : 0;
    },
  };
}

// =========================================================================================
// Recording model: per-signal step functions over virtual time.
// =========================================================================================
function prep(clip) {
  if (clip._p) return clip._p;
  const leds = new Map(), buttons = new Map(), uart = [];
  let last = 0;
  for (const e of clip.events || []) {
    last = Math.max(last, e.t);
    if (e.kind === "led") (leds.get(e.id) || leds.set(e.id, []).get(e.id)).push([e.t, e.value ? 1 : 0]);
    else if (e.kind === "button") (buttons.get(e.id) || buttons.set(e.id, []).get(e.id)).push([e.t, e.value ? 1 : 0]);
    else if (e.kind === "uart") uart.push({ t: e.t, text: String(e.value) });
  }
  const presses = new Map([...buttons].map(([id, a]) => [id, a.filter(([, v]) => v).map(([t]) => t)]));
  clip._p = { leds, buttons, presses, uart, dur: Math.max(clip.duration || 0, last, 0.02) };
  return clip._p;
}

/** Index of the last element with key <= t, or -1. */
function lastAtOrBefore(arr, t, key = (x) => x[0]) {
  let lo = 0, hi = arr.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (key(arr[mid]) <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}
const valueAt = (arr, t) => { const i = lastAtOrBefore(arr, t); return i < 0 ? 0 : arr[i][1]; };

// =========================================================================================
// Page state
// =========================================================================================
const S = {
  runs: [], runId: null, runES: null,
  traces: [], ti: -1, ci: 0,
  t: 0, playing: false, hold: 0, speed: 1,
  uartIdx: -2, tags: [],
};
const END_HOLD = 1.2;          // seconds (wall) to linger at the end of a test before moving on
const PRESS_HOLD_WALL = 0.18;  // a Robot press lasts microseconds; show it for this long (wall)
const TX_FLASH_WALL = 0.09;

const trace = () => S.traces[S.ti] || null;
const clip = () => trace()?.clips?.[S.ci] || null;

// ---------- connection + run list ----------
function setConn(state, text) { $("conn-led").className = "led " + state; $("conn").textContent = text; }

function connectAll() {
  const es = new EventSource("/api/stream");
  es.addEventListener("runs", (e) => {
    S.runs = JSON.parse(e.data);
    setConn("live", "live");
    renderRunList();
    if (!S.runId) {
      const pick = fromHash() || listed()[0]?.id;
      if (pick) selectRun(pick);
      else showEmpty();
    }
    renderHead();
  });
  es.onerror = () => setConn("down", "disconnected, retrying…");
  es.onopen = () => setConn("live", "live");
}

const listed = () => S.runs.filter((r) => r.sim_traces > 0 || r.status === "running" || r.id === S.runId);

function ago(ts) {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function renderRunList() {
  const rs = listed();
  $("run-count").textContent = rs.length ? String(rs.length) : "";
  $("run-list").innerHTML = rs.map((r) => `
    <li><button type="button" class="run-item${r.sim_traces ? "" : " waiting"}" data-id="${esc(r.id)}" aria-current="${r.id === S.runId}">
      <span class="r1"><span class="dot ${esc(r.status)}"></span><span class="t">${esc(r.title || r.id)}</span></span>
      <span class="r2"><span>${esc(r.board)}</span>${r.sim_traces ? `<span class="rec">● ${r.sim_traces} rec</span>` : `<span>no recording yet</span>`}<span>${ago(r.last_ts)}</span></span>
    </button></li>`).join("");
}

$("run-list").addEventListener("click", (e) => { const b = e.target.closest(".run-item"); if (b) selectRun(b.dataset.id); });

function fromHash() {
  const h = decodeURIComponent(location.hash.slice(1));
  return S.runs.some((r) => r.id === h) ? h : null;
}
addEventListener("hashchange", () => { const h = fromHash(); if (h && h !== S.runId) selectRun(h); });

function showEmpty() { $("empty").hidden = false; $("sim").hidden = true; }

// ---------- selected run ----------
function selectRun(id) {
  S.runES?.close();
  S.runId = id;
  S.traces = []; S.ti = -1; S.ci = 0; S.t = 0; S.playing = false;
  history.replaceState(null, "", "#" + encodeURIComponent(id));
  $("empty").hidden = true; $("sim").hidden = false;
  renderRunList();
  renderHead();
  renderAll();
  const es = new EventSource(`/api/runs/${encodeURIComponent(id)}/stream`);
  S.runES = es;
  es.addEventListener("snapshot", (e) => {
    const snap = JSON.parse(e.data);
    S.traces = snap.events.filter((x) => x.type === "sim_trace");
    if (S.traces.length) selectTrace(S.traces.length - 1, true);
    else renderAll();
  });
  es.addEventListener("event", (e) => {
    const ev = JSON.parse(e.data);
    if (ev.type !== "sim_trace") return;
    S.traces.push(ev);
    if ($("follow").checked || S.ti < 0) selectTrace(S.traces.length - 1, true);
    else renderTraces();
  });
  es.addEventListener("resync", () => selectRun(id));
}

function renderHead() {
  const r = S.runs.find((x) => x.id === S.runId);
  if (!r) return;
  $("run-title").textContent = r.title || r.id;
  $("run-meta").textContent = `${r.board} · ${r.mode} · ${r.status}`;
}

function selectTrace(i, play) {
  S.ti = i;
  selectClip(0, play);
}

function selectClip(i, play) {
  const tr = trace();
  if (!tr) return;
  S.ci = Math.max(0, Math.min(i, (tr.clips?.length || 1) - 1));
  S.t = 0; S.hold = 0; S.uartIdx = -2;
  S.playing = !!play && !!clip();
  renderAll();
}

// ---------- rendering ----------
function renderAll() {
  const tr = trace();
  $("waiting").hidden = !!tr;
  if (!tr) {
    const r = S.runs.find((x) => x.id === S.runId);
    $("waiting-text").textContent = r && r.status !== "running"
      ? "This run has no simulation recordings (no G5 Robot run, or it ran before recording existed)."
      : "Waiting for G5 to record this run…";
  }
  setupBoard();
  renderTraces();
  renderClips();
  renderUart();
  renderWave();
  renderPlayButton();
}

function boardSpec() {
  const tr = trace();
  const id = tr?.board || S.runs.find((x) => x.id === S.runId)?.board || "";
  if (BOARDS[id]) return { key: id, spec: BOARDS[id] };
  const leds = new Set(), btns = new Set();
  for (const c of tr?.clips || []) for (const e of c.events || []) (e.kind === "led" ? leds : e.kind === "button" ? btns : new Set()).add(e.id);
  return { key: `generic:${id}:${[...leds]}:${[...btns]}`, spec: genericBoard(id, [...leds], [...btns]) };
}

function setupBoard() {
  const { key, spec } = boardSpec();
  $("hud-board").textContent = spec.name;
  if (!view) return;
  const m = view.setBoard(spec, key);
  const labels = $("labels");
  labels.innerHTML = "";
  S.tags = [];
  const tag = (text, pos, kind, id) => {
    const el = document.createElement("span");
    el.className = "tag";
    el.textContent = text;
    labels.appendChild(el);
    S.tags.push({ el, pos, kind, id });
  };
  for (const [id, l] of Object.entries(m.leds)) tag(id, l.pos, "led", id);
  for (const [id, b] of Object.entries(m.buttons)) tag(id, b.pos, "button", id);
  if (m.tx) tag("uart tx", m.tx.pos, "tx");
}

function renderTraces() {
  $("traces").innerHTML = S.traces.map((tr, i) => {
    const clips = tr.clips || [];
    const pass = clips.filter((c) => c.passed).length;
    const ok = pass === clips.length;
    return `<button type="button" class="chip" role="tab" data-i="${i}" aria-selected="${i === S.ti}">
      iter ${esc(tr.iteration)} · ${esc(String(tr.gate).toUpperCase())} <span class="${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"} ${pass}/${clips.length}</span></button>`;
  }).join("");
}
$("traces").addEventListener("click", (e) => { const b = e.target.closest(".chip"); if (b) selectTrace(+b.dataset.i, true); });

function renderClips() {
  const clips = trace()?.clips || [];
  $("clip-count").textContent = clips.length ? `${clips.filter((c) => c.passed).length}/${clips.length} passed` : "";
  $("clips").innerHTML = clips.map((c, i) => {
    const msg = c.message ? `<span class="msg">${esc(c.message)}</span>` : "";
    const warn = c.errors?.length ? `<span class="msg warn">tracer: ${esc(c.errors[0])}</span>` : (!c.events?.length ? `<span class="msg warn">no emulation recorded</span>` : "");
    return `<li><button type="button" class="clip-row" data-i="${i}" aria-current="${i === S.ci}">
      <span class="mark ${c.passed ? "p" : "f"}">${c.passed ? "✓" : "✗"}</span>
      <span class="name">${esc(c.test)}${msg}${warn}</span>
      <span class="dur">${fmtT(prep(c).dur)}</span></button></li>`;
  }).join("");
  const c = clip();
  $("hud-test").textContent = c ? c.test : "";
  const m = $("hud-msg");
  if (!c) { m.textContent = ""; m.className = "hud-msg"; return; }
  if (!c.passed) { m.textContent = `✗ ${c.message || "failed"}`; m.className = "hud-msg fail"; }
  else if (c.errors?.length) { m.textContent = `tracer: ${c.errors[0]}`; m.className = "hud-msg warn"; }
  else { m.textContent = "✓ passed"; m.className = "hud-msg ok"; }
}
$("clips").addEventListener("click", (e) => { const b = e.target.closest(".clip-row"); if (b) selectClip(+b.dataset.i, true); });

function renderUart() {
  const c = clip();
  const lines = c ? prep(c).uart : [];
  $("uart-id").textContent = c ? `${lines.length} line${lines.length === 1 ? "" : "s"}` : "";
  $("uart").innerHTML = lines.length
    ? lines.map((l) => `<li class="future"><span class="ts">${l.t.toFixed(4)}</span><span>${esc(l.text) || " "}</span></li>`).join("")
    : `<li class="none">${c ? "No console output in this test." : ""}</li>`;
  S.uartIdx = -2;
}

function updateUart(t) {
  const c = clip();
  if (!c) return;
  const lines = prep(c).uart;
  const idx = lastAtOrBefore(lines, t, (l) => l.t);
  if (idx === S.uartIdx) return;
  const box = $("uart");
  const items = box.children;
  for (let i = 0; i < lines.length && i < items.length; i++) {
    items[i].classList.toggle("future", i > idx);
    items[i].classList.toggle("now", i === idx);
  }
  S.uartIdx = idx;
  const cur = items[Math.max(idx, 0)];
  if (cur) {
    const top = cur.offsetTop - box.offsetTop;
    if (top < box.scrollTop || top + cur.offsetHeight > box.scrollTop + box.clientHeight) box.scrollTop = top - box.clientHeight / 2;
  }
}

// ---------- waveform ----------
const WAVE = { lane: 24, pad: 8, labelW: 70, axis: 18 };
let waveGeom = null;

function niceStep(x) {
  const p = 10 ** Math.floor(Math.log10(x));
  return [1, 2, 5, 10].map((m) => m * p).find((s) => s >= x) || p * 10;
}

function renderWave() {
  const svg = $("wave");
  const c = clip();
  if (!c) { svg.style.height = "0px"; svg.innerHTML = ""; waveGeom = null; return; }
  const p = prep(c);
  const lanes = [
    ...[...p.leds.keys()].map((id) => ({ kind: "led", id, data: p.leds.get(id) })),
    ...[...p.buttons.keys()].map((id) => ({ kind: "button", id, data: p.buttons.get(id) })),
    { kind: "uart", id: "uart tx", data: p.uart },
  ];
  const W = svg.clientWidth || 800;
  const H = WAVE.pad * 2 + lanes.length * WAVE.lane + WAVE.axis;
  svg.style.height = `${H}px`;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const x0 = WAVE.labelW, x1 = W - 14, dur = p.dur;
  const X = (t) => x0 + (Math.min(t, dur) / dur) * (x1 - x0);
  let out = "";
  const step = niceStep(dur / Math.max(2, Math.floor((x1 - x0) / 90)));
  const axisY = H - WAVE.axis + 4;
  for (let t = 0; t <= dur + 1e-9; t += step) {
    out += `<line class="grid" x1="${X(t)}" x2="${X(t)}" y1="${WAVE.pad}" y2="${axisY - 4}"/>`;
    out += `<text x="${X(t)}" y="${axisY + 9}" text-anchor="middle">${+t.toFixed(4)}s</text>`;
  }
  out += `<rect class="past" id="wave-past" x="${x0}" y="${WAVE.pad}" width="0" height="${lanes.length * WAVE.lane}"/>`;
  lanes.forEach((ln, i) => {
    const top = WAVE.pad + i * WAVE.lane, hi = top + 5, lo = top + WAVE.lane - 6;
    out += `<text x="8" y="${top + WAVE.lane / 2 + 4}">${esc(ln.id)}</text>`;
    if (ln.kind === "uart") {
      out += `<line class="lane" x1="${x0}" x2="${x1}" y1="${lo}" y2="${lo}" stroke-dasharray="2 3"/>`;
      for (const l of ln.data) out += `<line class="tx" x1="${X(l.t)}" x2="${X(l.t)}" y1="${hi}" y2="${lo}"><title>${l.t.toFixed(4)} s  ${esc(l.text)}</title></line>`;
      return;
    }
    let d = "", v = 0, rects = "";
    let start = null;
    const pts = ln.data;
    d = `M${x0},${valueAt(pts, 0) ? hi : lo}`;
    v = valueAt(pts, 0);
    if (v) start = x0;
    for (const [t, nv] of pts) {
      if (nv === v) continue;
      const x = X(t);
      d += `L${x},${v ? hi : lo}L${x},${nv ? hi : lo}`;
      if (nv) start = x; else if (start != null) { rects += `<rect class="hi" x="${start}" y="${hi}" width="${Math.max(x - start, 1)}" height="${lo - hi}"/>`; start = null; }
      v = nv;
    }
    d += `L${x1},${v ? hi : lo}`;
    if (v && start != null) rects += `<rect class="hi" x="${start}" y="${hi}" width="${x1 - start}" height="${lo - hi}"/>`;
    out += ln.kind === "led" ? rects : "";
    out += `<path class="lane ${ln.kind}" d="${d}"/>`;
    if (ln.kind === "button") {
      for (const t of p.presses.get(ln.id) || []) out += `<line class="btn" x1="${X(t)}" x2="${X(t)}" y1="${hi - 2}" y2="${lo}"><title>press at ${t.toFixed(4)} s</title></line>`;
    }
  });
  out += `<line class="head" id="wave-head" x1="${x0}" x2="${x0}" y1="${WAVE.pad - 4}" y2="${axisY - 2}"/>`;
  out += `<circle class="head-knob" id="wave-knob" cx="${x0}" cy="${WAVE.pad - 4}" r="4"/>`;
  svg.innerHTML = out;
  waveGeom = { x0, x1, dur, head: svg.querySelector("#wave-head"), knob: svg.querySelector("#wave-knob"), past: svg.querySelector("#wave-past") };
}
new ResizeObserver(() => renderWave()).observe($("wave"));

function updateWave(t) {
  if (!waveGeom) return;
  const { x0, x1, dur, head, knob, past } = waveGeom;
  const x = x0 + (Math.min(t, dur) / dur) * (x1 - x0);
  head.setAttribute("x1", x); head.setAttribute("x2", x);
  knob.setAttribute("cx", x);
  past.setAttribute("width", Math.max(0, x - x0));
}

function seekFromPointer(e) {
  if (!waveGeom) return;
  const r = $("wave").getBoundingClientRect();
  const { x0, x1, dur } = waveGeom;
  S.t = Math.max(0, Math.min(dur, ((e.clientX - r.left - x0) / (x1 - x0)) * dur));
  S.hold = 0;
}
$("wave").addEventListener("pointerdown", (e) => { $("wave").setPointerCapture(e.pointerId); seekFromPointer(e); });
$("wave").addEventListener("pointermove", (e) => { if (e.buttons) seekFromPointer(e); });

// ---------- transport ----------
function renderPlayButton() {
  const b = $("btn-play");
  b.textContent = S.playing ? "❚❚" : "▶";
  b.setAttribute("aria-label", S.playing ? "Pause" : "Play");
}
function togglePlay() {
  if (!clip()) return;
  if (!S.playing && S.t >= prep(clip()).dur) S.t = 0;
  S.playing = !S.playing;
  S.hold = 0;
  renderPlayButton();
}
function step(dir) {
  const n = trace()?.clips?.length || 0;
  if (!n) return;
  selectClip((S.ci + dir + n) % n, S.playing);
}
$("btn-play").addEventListener("click", togglePlay);
$("btn-prev").addEventListener("click", () => step(-1));
$("btn-next").addEventListener("click", () => step(1));
$("speed").addEventListener("change", (e) => { S.speed = +e.target.value || 1; });
addEventListener("keydown", (e) => {
  if (e.target.closest("input, select, textarea") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === " " && !e.target.closest("button")) { e.preventDefault(); togglePlay(); }
  else if (e.key === "[") step(-1);
  else if (e.key === "]") step(1);
  else if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && clip()) {
    const d = (e.shiftKey ? 1 : 0.1) * (e.key === "ArrowLeft" ? -1 : 1);
    S.t = Math.max(0, Math.min(prep(clip()).dur, S.t + d));
    S.hold = 0;
  }
});

function advance() {
  const n = trace()?.clips?.length || 0;
  if ($("autoplay").checked && n > 1) selectClip((S.ci + 1) % n, true);
  else { S.playing = false; renderPlayButton(); }
}

// ---------- frame loop ----------
let lastFrame = performance.now();
function frame(now) {
  const dt = Math.min(0.1, (now - lastFrame) / 1000);
  lastFrame = now;
  const c = clip();
  if (c) {
    const p = prep(c);
    if (S.playing) {
      if (S.hold > 0) { S.hold -= dt; if (S.hold <= 0) advance(); }
      else { S.t += dt * S.speed; if (S.t >= p.dur) { S.t = p.dur; S.hold = END_HOLD; } }
    }
    applyState(p, S.t);
  }
  if (view) {
    view.frame();
    view.place(S.tags);
  }
  requestAnimationFrame(frame);
}

function applyState(p, t) {
  const m = view?.model;
  const pressHold = PRESS_HOLD_WALL * S.speed;
  const lit = {};
  for (const [id, arr] of p.leds) { lit[id] = valueAt(arr, t) === 1; m?.setLed(id, lit[id]); }
  for (const [id, arr] of p.buttons) {
    const presses = p.presses.get(id);
    const i = lastAtOrBefore(presses, t, (x) => x);
    lit[id] = valueAt(arr, t) === 1 || (i >= 0 && t - presses[i] < pressHold);
    m?.setButton(id, lit[id]);
  }
  const u = lastAtOrBefore(p.uart, t, (l) => l.t);
  const txOn = u >= 0 && t - p.uart[u].t < TX_FLASH_WALL * S.speed;
  if (m) {
    for (const id of Object.keys(m.leds)) if (!p.leds.has(id)) m.setLed(id, false);
    m.setPower(true);
    m.setTx(txOn);
  }
  for (const tg of S.tags) tg.el.classList.toggle("on", tg.kind === "tx" ? txOn : !!lit[tg.id]);
  $("hud-time").textContent = fmtT(t);
  $("tpos").textContent = `test ${S.ci + 1}/${trace()?.clips?.length || 0} · ${fmtT(t)} / ${fmtT(p.dur)}`;
  updateWave(t);
  updateUart(t);
}

connectAll();
requestAnimationFrame(frame);
