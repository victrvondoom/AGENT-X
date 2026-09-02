"use client";

import { useRef, useMemo, useState, useEffect, memo } from "react";
import { Canvas, useFrame, type ThreeEvent } from "@react-three/fiber";
import { Line } from "@react-three/drei";
import { EffectComposer, Bloom } from "@react-three/postprocessing";
import * as THREE from "three";
import { FLEET, EDGES, FOCUS_INDEX, COLORS, type FleetNode, type GeometryKind } from "./fleet";

/**
 * The agent fleet as a navigable constellation.
 *
 * The scene is a literal diagram of the pipeline rather than an abstract
 * particle field: a single finding travels GitHub -> Hunter -> Analyst ->
 * Verification Lab -> Patch Forge -> Re-Verifier -> Evidence Agent ->
 * Vault, and the node it is currently at is the only thing lit. Someone
 * who never reads the headline still learns what the product does.
 */

// One full lap of the pipeline. Slow enough to read a stage before it
// moves on, quick enough that a judge sees a whole loop without waiting.
const LOOP_SECONDS = 14;
// The constellation is offset right so it never sits under the headline,
// where nodes are both unhoverable and bad for type contrast. The camera
// looks at the same offset so the scene stays centred in its own half.
const GROUP_X = 3.9;
const CAMERA_X = 3.2;
const TRAIL_COUNT = 7;

function geometryFor(kind: GeometryKind, size: number) {
  switch (kind) {
    case "box":
      return <boxGeometry args={[size * 1.5, size * 1.5, size * 1.5]} />;
    case "icosahedron":
      return <icosahedronGeometry args={[size, 0]} />;
    case "octahedron":
      return <octahedronGeometry args={[size * 1.15, 0]} />;
    case "tetrahedron":
      return <tetrahedronGeometry args={[size * 1.3, 0]} />;
    case "dodecahedron":
      return <dodecahedronGeometry args={[size, 0]} />;
    case "cone":
      return <coneGeometry args={[size, size * 1.9, 6]} />;
    case "cylinder":
      return <cylinderGeometry args={[size * 0.8, size * 0.8, size * 1.5, 6]} />;
  }
}

function AgentNode({
  node,
  active,
  dimmed,
  animate,
  onHover,
  onSelect,
}: {
  node: FleetNode;
  active: boolean;
  dimmed: boolean;
  animate: boolean;
  onHover: (n: FleetNode | null, screen: { x: number; y: number } | null) => void;
  onSelect: (n: FleetNode) => void;
}) {
  const mesh = useRef<THREE.Mesh>(null);
  const scale = useRef(1);

  useFrame((state, delta) => {
    if (!mesh.current) return;
    if (animate) {
      // Each form tumbles a little differently so the constellation never
      // looks like one rigid object being spun.
      mesh.current.rotation.x += delta * 0.12;
      mesh.current.rotation.y += delta * 0.17;
    }
    // Damped scale rather than a hard swap, so the handover between stages
    // reads as a pulse moving through rather than nodes blinking.
    const target = active ? 1.42 : 1;
    scale.current += (target - scale.current) * Math.min(1, delta * 6);
    mesh.current.scale.setScalar(scale.current);
    if (active && animate) {
      const t = state.clock.elapsedTime;
      mesh.current.position.y = node.position[1] + Math.sin(t * 2) * 0.04;
    }
  });

  const color = active ? COLORS.amber : node.kind === "sink" ? COLORS.verified : COLORS.idle;

  return (
    <mesh
      ref={mesh}
      position={node.position}
      onPointerOver={(e: ThreeEvent<PointerEvent>) => {
        e.stopPropagation();
        onHover(node, { x: e.nativeEvent.clientX, y: e.nativeEvent.clientY });
        document.body.style.cursor = "pointer";
      }}
      onPointerOut={() => {
        onHover(null, null);
        document.body.style.cursor = "";
      }}
      onClick={(e: ThreeEvent<MouseEvent>) => {
        e.stopPropagation();
        onSelect(node);
      }}
    >
      {geometryFor(node.geometry, node.size)}
      <meshStandardMaterial
        color={color}
        // Only the active node is emissive, which is what keeps bloom
        // confined to it. Bloom on everything is the fastest way to make a
        // 3D scene look amateur.
        // Idle nodes carry a faint emissive of their own so they stay
        // legible against #0A0C10 without crossing the bloom threshold -
        // the constellation has to read as eight nodes, not one lit node
        // floating in the dark.
        emissive={active ? COLORS.amber : color}
        emissiveIntensity={active ? 2.2 : 0.22}
        roughness={0.4}
        metalness={0.2}
        transparent
        opacity={dimmed ? 0.3 : active ? 1 : 0.95}
        flatShading
      />
    </mesh>
  );
}

/** The finding travelling the pipeline, plus a short instanced trail. */
function Pulse({ activeEdge, t, animate }: { activeEdge: number; t: number; animate: boolean }) {
  const lead = useRef<THREE.Mesh>(null);
  const trail = useRef<THREE.InstancedMesh>(null);
  const dummy = useMemo(() => new THREE.Object3D(), []);

  const points = useMemo(
    () => FLEET.map((n) => new THREE.Vector3(...n.position)),
    []
  );

  useFrame(() => {
    const at = (edge: number, u: number) => {
      const [a, b] = EDGES[Math.max(0, Math.min(EDGES.length - 1, edge))];
      return points[a].clone().lerp(points[b], Math.max(0, Math.min(1, u)));
    };

    if (lead.current) lead.current.position.copy(at(activeEdge, t));

    if (trail.current) {
      // Trail particles lag the lead along the same path, so the pulse
      // reads as directional rather than as a floating dot.
      for (let i = 0; i < TRAIL_COUNT; i++) {
        const lag = (i + 1) * 0.055;
        let e = activeEdge;
        let u = t - lag;
        while (u < 0 && e > 0) {
          e -= 1;
          u += 1;
        }
        dummy.position.copy(at(e, u));
        const s = (1 - i / TRAIL_COUNT) * 0.07;
        dummy.scale.setScalar(Math.max(0.008, s));
        dummy.updateMatrix();
        trail.current.setMatrixAt(i, dummy.matrix);
      }
      trail.current.instanceMatrix.needsUpdate = true;
    }
  });

  if (!animate) return null;

  return (
    <group>
      <mesh ref={lead}>
        <sphereGeometry args={[0.085, 12, 12]} />
        <meshBasicMaterial color={COLORS.amber} />
      </mesh>
      <instancedMesh ref={trail} args={[undefined, undefined, TRAIL_COUNT]}>
        <sphereGeometry args={[1, 8, 8]} />
        <meshBasicMaterial color={COLORS.amber} transparent opacity={0.4} />
      </instancedMesh>
    </group>
  );
}

function Edges({ activeEdge }: { activeEdge: number }) {
  return (
    <group>
      {EDGES.map(([a, b], i) => (
        <Line
          key={i}
          points={[FLEET[a].position, FLEET[b].position]}
          color={i === activeEdge ? COLORS.amber : COLORS.edge}
          lineWidth={i === activeEdge ? 2 : 1.1}
          transparent
          opacity={i === activeEdge ? 0.9 : 0.55}
        />
      ))}
    </group>
  );
}

/**
 * Camera: slow ambient drift, damped parallax toward the pointer, and the
 * "Get started" fly-in.
 *
 * Parallax is deliberately damped and small. Locking the camera 1:1 to the
 * cursor feels cheap and is a reliable way to make people motion-sick.
 */
function Rig({
  pointer,
  flying,
  animate,
  onArrived,
}: {
  pointer: React.RefObject<{ x: number; y: number }>;
  flying: boolean;
  animate: boolean;
  onArrived: () => void;
}) {
  const start = useMemo(() => new THREE.Vector3(CAMERA_X, 0.4, 12.5), []);
  const target = useMemo(() => {
    const n = FLEET[FOCUS_INDEX];
    // Stop just short of the Verifier so it fills frame as the scene hands
    // over to the Command Center's 2D graph.
    return new THREE.Vector3(n.position[0] + GROUP_X, n.position[1], n.position[2] + 1.6);
  }, []);
  const fired = useRef(false);
  const progress = useRef(0);
  const placed = useRef(false);

  // The camera is read off the per-frame state rather than captured from
  // render scope: driving a Three.js camera is inherently an imperative,
  // every-frame mutation, and closing over it makes that a render-scope
  // write that the compiler cannot reason about.
  useFrame((state, delta) => {
    const camera = state.camera;

    if (!placed.current) {
      camera.position.copy(start);
      camera.lookAt(CAMERA_X, 0, 0);
      placed.current = true;
    }

    if (flying) {
      progress.current = Math.min(1, progress.current + delta / 1.5);
      // easeInOutCubic - accelerates away from rest, settles into the node.
      const p = progress.current;
      const e = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
      camera.position.lerpVectors(start, target, e);
      camera.lookAt(target.x, target.y, target.z - 2);
      if (p >= 1 && !fired.current) {
        fired.current = true;
        onArrived();
      }
      return;
    }

    if (!animate) return;
    const t = state.clock.elapsedTime;
    const px = pointer.current?.x ?? 0;
    const py = pointer.current?.y ?? 0;
    // Damped and small on purpose. Locking the camera 1:1 to the cursor
    // feels cheap and is a reliable way to make people motion-sick.
    const desiredX = start.x + px * 1.1 + Math.sin(t * 0.11) * 0.35;
    const desiredY = start.y + py * 0.7 + Math.cos(t * 0.14) * 0.22;
    camera.position.x += (desiredX - camera.position.x) * Math.min(1, delta * 1.6);
    camera.position.y += (desiredY - camera.position.y) * Math.min(1, delta * 1.6);
    camera.lookAt(CAMERA_X, 0, 0);
  });

  return null;
}


function Constellation({
  animate,
  simplified,
  pointer,
  flying,
  onArrived,
  onHover,
  onSelect,
  selectedId,
}: {
  animate: boolean;
  simplified: boolean;
  pointer: React.RefObject<{ x: number; y: number }>;
  flying: boolean;
  onArrived: () => void;
  onHover: (n: FleetNode | null, s: { x: number; y: number } | null) => void;
  onSelect: (n: FleetNode) => void;
  selectedId: string | null;
}) {
  const group = useRef<THREE.Group>(null);
  const [step, setStep] = useState(0);
  const [t, setT] = useState(0);

  useFrame((state, delta) => {
    if (group.current && animate && !flying) {
      group.current.rotation.y = Math.sin(state.clock.elapsedTime * 0.06) * 0.14;
    }
    if (!animate) return;
    const perEdge = LOOP_SECONDS / EDGES.length;
    setT((prev) => {
      const next = prev + delta / perEdge;
      if (next >= 1) {
        setStep((s) => (s + 1) % EDGES.length);
        return 0;
      }
      return next;
    });
  });

  // The pulse is arriving at the far end of the current edge, so that node
  // is the one that lights up.
  const activeIndex = animate ? EDGES[step][1] : FOCUS_INDEX;

  return (
    <>
      <ambientLight intensity={0.95} />
      <directionalLight position={[6, 8, 6]} intensity={1.5} />
      <directionalLight position={[-8, -4, -6]} intensity={0.6} color="#5B7FBF" />
      <Rig pointer={pointer} flying={flying} animate={animate} onArrived={onArrived} />
      <group ref={group} position={[GROUP_X, 0, 0]}>
        <Edges activeEdge={animate ? step : -1} />
        <Pulse activeEdge={step} t={t} animate={animate} />
        {FLEET.map((node, i) => (
          <AgentNode
            key={node.id}
            node={node}
            active={i === activeIndex}
            dimmed={selectedId !== null && selectedId !== node.id}
            animate={animate}
            onHover={onHover}
            onSelect={onSelect}
          />
        ))}
      </group>
      {!simplified && (
        <EffectComposer>
          {/* High threshold so only the emissive active node blooms. */}
          <Bloom intensity={0.75} luminanceThreshold={0.72} luminanceSmoothing={0.18} radius={0.55} mipmapBlur />
        </EffectComposer>
      )}
    </>
  );
}

export default memo(function FleetScene({
  animate,
  simplified,
  flying,
  onArrived,
  onHover,
  onSelect,
  selectedId,
}: {
  animate: boolean;
  simplified: boolean;
  flying: boolean;
  onArrived: () => void;
  onHover: (n: FleetNode | null, s: { x: number; y: number } | null) => void;
  onSelect: (n: FleetNode) => void;
  selectedId: string | null;
}) {
  const pointer = useRef({ x: 0, y: 0 });
  const [visible, setVisible] = useState(true);

  // A backgrounded tab should cost nothing. Without this the render loop
  // keeps burning a core behind other windows.
  useEffect(() => {
    const onVis = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  return (
    <Canvas
      // dpr is capped: uncapped devicePixelRatio on a 4K panel destroys the
      // framerate for no visible gain at this level of detail.
      dpr={[1, 2]}
      camera={{ fov: 46, position: [1.2, 0.4, 13.5] }}
      frameloop={visible && (animate || flying) ? "always" : "demand"}
      gl={{ antialias: !simplified, powerPreference: "high-performance" }}
      onPointerMove={(e) => {
        const { innerWidth: w, innerHeight: h } = window;
        pointer.current = { x: (e.clientX / w) * 2 - 1, y: -((e.clientY / h) * 2 - 1) };
      }}
      style={{ position: "absolute", inset: 0 }}
    >
      <color attach="background" args={[COLORS.bg]} />
      <fog attach="fog" args={[COLORS.bg, 14, 26]} />
      <Constellation
        animate={animate}
        simplified={simplified}
        pointer={pointer}
        flying={flying}
        onArrived={onArrived}
        onHover={onHover}
        onSelect={onSelect}
        selectedId={selectedId}
      />
    </Canvas>
  );
});
