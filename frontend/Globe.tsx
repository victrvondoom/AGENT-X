/**
 * Globe.tsx — a dot-matrix Earth for React + Three.js.
 *
 * This is the portable, framework-agnostic twin of the globe running on the
 * Agent X landing page. That page is plain HTML served by FastAPI with no build
 * step, so the version living in templates/landing.html is vanilla three.js.
 * This file is the same implementation packaged for a React/TypeScript project.
 *
 * Deliberately NOT using react-three-fiber: the whole scene is a handful of
 * static buffers plus one animation loop, so a raw three.js scene held in a ref
 * is both smaller and faster here — no reconciler, no per-frame React work, and
 * nothing re-renders while the globe spins.
 *
 *   npm i three
 *   npm i -D @types/three
 *
 * Usage:
 *   <Globe size={500} autoRotate rotationSpeed={0.15} interactive />
 */
import { useEffect, useRef } from 'react';
import * as THREE from 'three';

/* ── land outlines, [lon, lat], coarse on purpose ──
   Point-in-polygon against these decides which grid cells get a dot. Keeping
   the data inline means no map image to fetch: no CORS, no CDN, works offline. */
const LAND: number[][][] = [
  [[-168,65],[-160,71],[-140,70],[-125,70],[-110,68],[-95,70],[-85,73],[-75,68],[-64,60],[-56,51],
   [-66,45],[-70,42],[-75,35],[-81,25],[-90,29],[-97,26],[-105,22],[-112,29],[-118,33],[-125,40],
   [-124,48],[-130,55],[-140,60],[-150,59],[-160,56],[-168,65]],                       // N America
  [[-58,83],[-30,83],[-20,76],[-22,70],[-38,66],[-50,60],[-55,66],[-60,76],[-58,83]],  // Greenland
  [[-81,8],[-72,11],[-62,10],[-52,5],[-44,-2],[-35,-6],[-38,-13],[-42,-23],[-48,-28],[-58,-34],
   [-62,-40],[-65,-45],[-68,-52],[-74,-52],[-73,-44],[-72,-35],[-71,-25],[-70,-18],[-76,-10],
   [-81,-4],[-81,8]],                                                                  // S America
  [[-17,15],[-6,36],[10,37],[25,32],[34,31],[43,12],[51,12],[42,-2],[40,-15],[35,-24],[26,-34],
   [18,-35],[12,-18],[9,-1],[3,6],[-8,5],[-16,12],[-17,15]],                            // Africa
  [[-10,36],[-9,44],[-2,49],[2,51],[5,58],[11,58],[18,55],[24,60],[30,62],[40,64],[45,55],[40,46],
   [30,45],[22,40],[15,38],[8,44],[0,40],[-10,36]],                                     // Europe
  [[45,55],[55,68],[70,72],[85,74],[100,76],[115,74],[130,71],[145,70],[160,68],[170,66],[178,65],
   [170,60],[160,58],[150,52],[140,45],[130,42],[125,35],[120,30],[110,20],[105,10],[100,5],
   [95,15],[88,21],[80,12],[75,8],[70,20],[62,25],[55,25],[48,30],[45,38],[42,45],[45,55]], // Asia
  [[70,20],[75,8],[80,12],[88,21],[80,26],[72,24],[70,20]],                             // India
  [[95,5],[105,2],[115,-2],[125,-3],[135,-4],[130,-8],[118,-9],[108,-7],[100,0],[95,5]], // SE Asia
  [[113,-22],[121,-19],[130,-12],[137,-12],[142,-11],[146,-19],[150,-25],[153,-28],[150,-37],
   [144,-38],[136,-35],[129,-32],[121,-34],[115,-34],[113,-22]],                        // Australia
  [[172,-34],[178,-38],[174,-42],[168,-46],[166,-45],[170,-40],[172,-34]],              // NZ
  [[130,31],[136,34],[141,40],[145,44],[141,45],[136,37],[130,31]],                     // Japan
  [[43,-12],[50,-16],[48,-25],[44,-22],[43,-12]],                                       // Madagascar
  [[-10,51],[-5,58],[-2,58],[0,53],[-5,50],[-10,51]],                                   // British Isles
  [[-180,-70],[180,-70],[180,-85],[-180,-85],[-180,-70]],                               // Antarctica
];

function inRing(lon: number, lat: number, ring: number[][]) {
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if (((yi > lat) !== (yj > lat)) && (lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi)) hit = !hit;
  }
  return hit;
}
const isLand = (lon: number, lat: number) => LAND.some(r => inRing(lon, lat, r));

const D2R = Math.PI / 180;
const toVec = (lat: number, lon: number, r: number) => {
  const p = (90 - lat) * D2R, t = (lon + 180) * D2R;
  return new THREE.Vector3(-r * Math.sin(p) * Math.cos(t), r * Math.cos(p), r * Math.sin(p) * Math.sin(t));
};

export interface GlobeProps {
  /** rendered square size in px (or '100%' via className) */
  size?: number;
  autoRotate?: boolean;
  /** radians per second */
  rotationSpeed?: number;
  /** allow drag-to-spin */
  interactive?: boolean;
  /** dot rows pole-to-pole; higher = denser. 180 ≈ 12k dots */
  density?: number;
  dotColor?: string;
  gridColor?: string;
  markerColor?: string;
  atmosphereColor?: string;
  /** null keeps the canvas transparent so it can sit over an existing hero */
  background?: string | null;
  className?: string;
}

export default function Globe({
  size = 500,
  autoRotate = true,
  rotationSpeed = 0.15,
  interactive = true,
  density = 180,
  dotColor = '#ffffff',
  gridColor = '#ffffff',
  markerColor = '#ffffff',
  atmosphereColor = '#dce9ff',
  background = null,
  className,
}: GlobeProps) {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: background === null });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));   // cap: retina at 3x is wasted here
    renderer.setSize(size, size, false);
    if (background !== null) renderer.setClearColor(new THREE.Color(background), 1);
    host.appendChild(renderer.domElement);
    Object.assign(renderer.domElement.style, { width: '100%', height: '100%', display: 'block' });

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
    camera.position.set(0, 0.35, 4.4);
    camera.lookAt(0, 0, 0);

    const R = 1.35;
    const globe = new THREE.Group();
    globe.rotation.z = -23.4 * D2R;                                  // axial tilt
    scene.add(globe);

    /* ── land dots. Rings of constant latitude, dot count scaled by cos(lat)
       so spacing stays even rather than bunching at the poles. ── */
    const pos: number[] = [], rnd: number[] = [];
    for (let i = 0; i < density; i++) {
      const lat = 90 - (i + 0.5) * (180 / density);
      const n = Math.max(1, Math.round(density * 2 * Math.cos(lat * D2R)));
      for (let j = 0; j < n; j++) {
        const lon = -180 + (j + 0.5) * (360 / n);
        if (!isLand(lon, lat)) continue;
        const v = toVec(lat, lon, R);
        pos.push(v.x, v.y, v.z);
        rnd.push(Math.random());
      }
    }
    const dotGeo = new THREE.BufferGeometry();
    dotGeo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    dotGeo.setAttribute('aRnd', new THREE.Float32BufferAttribute(rnd, 1));

    /* Depth is the whole trick: compare each dot's world normal with the view
       direction, then dim and shrink the ones facing away. Without this a point
       cloud reads as a flat disc, not a sphere. */
    const dotMat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false,
      uniforms: {
        uColor: { value: new THREE.Color(dotColor) },
        uTime: { value: 0 },
        uPR: { value: renderer.getPixelRatio() },
        uSize: { value: 2.9 },
      },
      vertexShader: `
        attribute float aRnd;
        uniform float uSize, uPR;
        varying float vFace, vRnd;
        void main(){
          vec4 world = modelMatrix * vec4(position,1.0);
          vec3 nrm = normalize(mat3(modelMatrix) * normalize(position));
          vFace = dot(nrm, normalize(cameraPosition - world.xyz));
          vRnd = aRnd;
          vec4 mv = viewMatrix * world;
          float depth = smoothstep(-0.25, 0.85, vFace);
          gl_PointSize = uSize * uPR * (0.55 + 0.45*depth) * (2.9 / -mv.z);
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: `
        uniform vec3 uColor; uniform float uTime;
        varying float vFace; varying float vRnd;
        void main(){
          float m = 1.0 - smoothstep(0.30, 0.5, length(gl_PointCoord - vec2(0.5)));
          if(m <= 0.001) discard;
          if(vFace < -0.28) discard;
          float depth = smoothstep(-0.28, 0.75, vFace);
          float tw = 0.88 + 0.12*sin(uTime*1.6 + vRnd*6.283);
          gl_FragColor = vec4(uColor, m * pow(depth,1.6) * tw);
        }`,
    });
    globe.add(new THREE.Points(dotGeo, dotMat));

    /* ── graticule ── */
    const seg: number[] = [];
    for (let lat = -60; lat <= 60; lat += 30)
      for (let k = 0; k < 120; k++) {
        const a = toVec(lat, -180 + k * 3, R * 1.001), b = toVec(lat, -180 + (k + 1) * 3, R * 1.001);
        seg.push(a.x, a.y, a.z, b.x, b.y, b.z);
      }
    for (let lon = -180; lon < 180; lon += 30)
      for (let k = 0; k < 60; k++) {
        const a = toVec(-90 + k * 3, lon, R * 1.001), b = toVec(-90 + (k + 1) * 3, lon, R * 1.001);
        seg.push(a.x, a.y, a.z, b.x, b.y, b.z);
      }
    const gridGeo = new THREE.BufferGeometry();
    gridGeo.setAttribute('position', new THREE.Float32BufferAttribute(seg, 3));
    const gridMat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false,
      uniforms: { uColor: { value: new THREE.Color(gridColor) }, uOpacity: { value: 0.1 } },
      vertexShader: `
        varying float vFace;
        void main(){
          vec4 w = modelMatrix * vec4(position,1.0);
          vFace = dot(normalize(mat3(modelMatrix)*normalize(position)), normalize(cameraPosition - w.xyz));
          gl_Position = projectionMatrix * viewMatrix * w;
        }`,
      fragmentShader: `
        uniform vec3 uColor; uniform float uOpacity; varying float vFace;
        void main(){ if(vFace < 0.0) discard;
          gl_FragColor = vec4(uColor, uOpacity * smoothstep(0.0,0.6,vFace)); }`,
    });
    globe.add(new THREE.LineSegments(gridGeo, gridMat));

    /* ── opaque body, so far-side dots are occluded rather than showing through ── */
    const bodyGeo = new THREE.SphereGeometry(R * 0.985, 64, 48);
    const bodyMat = new THREE.MeshBasicMaterial({ color: 0x0a0a0d });
    globe.add(new THREE.Mesh(bodyGeo, bodyMat));

    /* ── atmosphere: back-side shell lit only at the rim ── */
    const atmoGeo = new THREE.SphereGeometry(R * 1.1, 64, 48);
    const atmoMat = new THREE.ShaderMaterial({
      transparent: true, side: THREE.BackSide, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uColor: { value: new THREE.Color(atmosphereColor) }, uStrength: { value: 0.38 } },
      vertexShader: `
        varying vec3 vN; varying vec3 vW;
        void main(){ vN = normalize(mat3(modelMatrix)*normal);
          vec4 w = modelMatrix*vec4(position,1.0); vW = w.xyz;
          gl_Position = projectionMatrix*viewMatrix*w; }`,
      fragmentShader: `
        uniform vec3 uColor; uniform float uStrength; varying vec3 vN; varying vec3 vW;
        void main(){
          float rim = 1.0 - abs(dot(normalize(vN), normalize(cameraPosition - vW)));
          gl_FragColor = vec4(uColor, pow(rim,3.2) * uStrength); }`,
    });
    scene.add(new THREE.Mesh(atmoGeo, atmoMat));

    /* ── drag: keep residual velocity on release and let friction hand control
       back to the auto-spin, so there is no snap ── */
    const drag = { on: false, vx: 0, vy: 0, lx: 0, ly: 0 };
    const el = renderer.domElement;
    const down = (e: PointerEvent) => { if (!interactive) return; drag.on = true; drag.lx = e.clientX; drag.ly = e.clientY; };
    const move = (e: PointerEvent) => {
      if (!drag.on) return;
      drag.vx += (e.clientX - drag.lx) * 0.0004;
      drag.vy += (e.clientY - drag.ly) * 0.0003;
      drag.lx = e.clientX; drag.ly = e.clientY;
    };
    const up = () => { drag.on = false; };
    if (interactive) {
      el.style.touchAction = 'none';
      el.addEventListener('pointerdown', down);
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
      window.addEventListener('pointercancel', up);
    }

    /* ── loop. delta-time based, so speed is identical at 30 / 60 / 144 Hz ── */
    const clock = new THREE.Clock();
    let raf = 0;
    const tick = () => {
      const dt = Math.min(clock.getDelta(), 0.05);   // clamp, so a tab-switch cannot jump
      const t = clock.getElapsedTime();
      dotMat.uniforms.uTime.value = t;
      if (drag.on) {
        globe.rotation.y += drag.vx;
        globe.rotation.x = Math.max(-0.5, Math.min(0.5, globe.rotation.x + drag.vy));
        drag.vx *= 0.86; drag.vy *= 0.86;
      } else {
        drag.vx *= 0.94; drag.vy *= 0.94;
        globe.rotation.y += drag.vx;
        globe.rotation.x += (0 - globe.rotation.x) * 0.02;
        if (autoRotate) globe.rotation.y += rotationSpeed * dt;
      }
      globe.position.y = Math.sin(t * 0.5) * 0.02;    // faint breathing drift
      renderer.render(scene, camera);
      raf = requestAnimationFrame(tick);
    };
    tick();

    /* ── responsive: follow the host box, not the window ── */
    const ro = new ResizeObserver(([entry]) => {
      const w = entry.contentRect.width || size;
      const h = entry.contentRect.height || size;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    });
    ro.observe(host);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      if (interactive) {
        el.removeEventListener('pointerdown', down);
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
        window.removeEventListener('pointercancel', up);
      }
      // three.js holds GPU memory outside the JS heap; without this a remount leaks.
      [dotGeo, gridGeo, bodyGeo, atmoGeo].forEach(g => g.dispose());
      [dotMat, gridMat, bodyMat, atmoMat].forEach(m => m.dispose());
      renderer.dispose();
      host.removeChild(el);
    };
    // Rebuilding on prop change is intentional: geometry is baked from density/size.
  }, [size, autoRotate, rotationSpeed, interactive, density,
      dotColor, gridColor, markerColor, atmosphereColor, background]);

  return (
    <div
      ref={hostRef}
      className={className}
      style={{ width: size, height: size, maxWidth: '100%', aspectRatio: '1 / 1' }}
    />
  );
}
