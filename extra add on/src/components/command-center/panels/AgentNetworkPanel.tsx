"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCollide,
  forceX,
  forceY,
  type Simulation,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from "d3-force";
import {
  IconBrandGithub,
  IconAlertTriangle,
  IconCpu,
  IconSearch,
  IconClipboardCheck,
  IconShieldSearch,
  IconTools,
  IconRepeat,
  IconEye,
} from "@tabler/icons-react";
import { Panel } from "../Panel";
import type { AgentId, GraphNode, GraphEdge } from "@/lib/types";
import type { FindingSummary } from "@/lib/sentinel/api";

interface SimNode extends GraphNode, SimulationNodeDatum {
  baseX: number;
  baseY: number;
  radius: number;
}

interface SimLink extends SimulationLinkDatum<SimNode> {
  id: string;
  lastMessage?: string;
}

const W = 640;
const H = 460;

const iconFor: Record<string, typeof IconBrandGithub> = {
  github: IconBrandGithub,
  vulnerability: IconAlertTriangle,
  hub: IconCpu,
  hunter: IconSearch,
  analyst: IconClipboardCheck,
  verifier: IconShieldSearch,
  "patch-forge": IconTools,
  "re-verifier": IconRepeat,
  watchdog: IconEye,
};

const baselinePositions: Record<string, { x: number; y: number }> = {
  github: { x: 90, y: 140 },
  vulnerability: { x: 90, y: 270 },
  hub: { x: 220, y: 205 },
  verifier: { x: 440, y: 225 },
  hunter: { x: 340, y: 110 },
  analyst: { x: 500, y: 95 },
  "patch-forge": { x: 560, y: 250 },
  "re-verifier": { x: 470, y: 360 },
  watchdog: { x: 320, y: 335 },
};

const radiusFor: Record<GraphNode["type"], number> = {
  source: 18,
  finding: 18,
  hub: 20,
  agent: 24,
};

const severityColor: Record<FindingSummary["severity"], string> = {
  critical: "text-danger border-danger/50 bg-danger/10",
  high: "text-warning border-warning/50 bg-warning/10",
  medium: "text-neutral border-neutral/50 bg-neutral/10",
  low: "text-success border-success/50 bg-success/10",
};

interface AgentNetworkPanelProps {
  graphNodes: GraphNode[];
  graphEdges: GraphEdge[];
  activeEdgeIds: string[];
  finding: FindingSummary | null;
  loading?: boolean;
  error?: string | null;
  selectedAgentId?: AgentId | null;
  onSelectAgent?: (id: AgentId) => void;
}

export function AgentNetworkPanel({
  graphNodes,
  graphEdges,
  activeEdgeIds,
  finding,
  loading,
  error,
  selectedAgentId,
  onSelectAgent,
}: AgentNetworkPanelProps) {
  const [nodes, setNodes] = useState<SimNode[]>([]);
  const [hoveredEdge, setHoveredEdge] = useState<SimLink | null>(null);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const simRef = useRef<Simulation<SimNode, SimLink> | null>(null);

  const links: SimLink[] = useMemo(
    () => graphEdges.map((e) => ({ id: e.id, source: e.source, target: e.target, lastMessage: e.lastMessage })),
    [graphEdges]
  );

  useEffect(() => {
    if (graphNodes.length === 0) return;
    const simNodes: SimNode[] = graphNodes.map((n) => {
      const base = baselinePositions[n.id] ?? { x: W / 2, y: H / 2 };
      const radius = n.id === "verifier" ? 30 : radiusFor[n.type];
      return { ...n, baseX: base.x, baseY: base.y, x: base.x, y: base.y, radius };
    });

    const sim = forceSimulation<SimNode, SimLink>(simNodes)
      .force(
        "link",
        forceLink<SimNode, SimLink>(links)
          .id((d) => d.id)
          .distance((l) => {
            const s = l.source as SimNode;
            const t = l.target as SimNode;
            return s.type === "agent" && t.type === "agent" ? 150 : 110;
          })
          .strength(0.25)
      )
      .force("charge", forceManyBody().strength(-90))
      .force("collide", forceCollide<SimNode>((d) => d.radius + 14))
      .force("x", forceX<SimNode>((d) => d.baseX).strength(0.12))
      .force("y", forceY<SimNode>((d) => d.baseY).strength(0.12))
      .alpha(0.9)
      .alphaDecay(0.02)
      .alphaMin(0.001)
      .velocityDecay(0.35);

    simRef.current = sim;

    sim.on("tick", () => {
      setNodes(simNodes.map((n) => ({ ...n })));
    });

    return () => {
      sim.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphNodes]);

  const nodeById = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);

  return (
    <Panel
      title="Sentinel Agent Network"
      headerRight={
        <span className="text-[10px] uppercase tracking-[0.06em] text-text-dim">
          {error ? "connection lost" : "real-time relationship graph"}
        </span>
      }
      className="row-span-2"
      bodyClassName="relative"
    >
      {loading && nodes.length === 0 ? (
        <div className="flex h-full items-center justify-center text-[11px] text-text-dim">
          connecting to agent engine…
        </div>
      ) : error && nodes.length === 0 ? (
        <div className="flex h-full flex-col items-center justify-center gap-1 px-6 text-center text-[11px] text-danger">
          <span>{error}</span>
        </div>
      ) : (
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="h-full w-full"
          onMouseMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            setMousePos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
          }}
        >
          <defs>
            <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
              <path d="M0,0 L8,4 L0,8 Z" className="fill-text-dim" />
            </marker>
          </defs>

          <g>
            {links.map((l) => {
              const s = typeof l.source === "object" ? l.source : nodeById.get(l.source as unknown as string);
              const t = typeof l.target === "object" ? l.target : nodeById.get(l.target as unknown as string);
              if (!s || !t) return null;
              const isActive = activeEdgeIds.includes(l.id);
              return (
                <line
                  key={l.id}
                  x1={s.x}
                  y1={s.y}
                  x2={t.x}
                  y2={t.y}
                  strokeWidth={isActive ? 1.6 : 1}
                  className={clsx(isActive ? "stroke-amber" : "stroke-border")}
                  strokeDasharray={isActive ? "4 4" : undefined}
                  style={isActive ? { animation: "edge-pulse 1.6s linear infinite" } : { opacity: 0.55 }}
                  onMouseEnter={() => setHoveredEdge(l)}
                  onMouseLeave={() => setHoveredEdge((cur) => (cur?.id === l.id ? null : cur))}
                  pointerEvents="stroke"
                />
              );
            })}
          </g>

          <g>
            {nodes.map((n) => {
              const Icon = iconFor[n.id] ?? IconCpu;
              const isActiveNode = Boolean(n.active);
              const isSelected = n.agentId && n.agentId === selectedAgentId;
              return (
                <g
                  key={n.id}
                  transform={`translate(${n.x ?? 0}, ${n.y ?? 0})`}
                  className={clsx(n.type === "agent" && "cursor-pointer")}
                  onClick={() => n.agentId && onSelectAgent?.(n.agentId)}
                >
                  {isSelected && (
                    <circle r={n.radius + 6} className="fill-none stroke-amber/50" strokeWidth={1.5} />
                  )}
                  <circle
                    r={n.radius}
                    className={clsx(
                      "stroke-[1.5px]",
                      isActiveNode ? "fill-panel stroke-amber" : "fill-panel stroke-border"
                    )}
                    style={isActiveNode ? { animation: "node-glow 2.2s ease-in-out infinite" } : undefined}
                  />
                  <foreignObject x={-9} y={-9} width={18} height={18} pointerEvents="none">
                    <Icon
                      size={18}
                      strokeWidth={1.5}
                      className={isActiveNode ? "text-amber" : "text-text-muted"}
                    />
                  </foreignObject>
                  <text
                    y={n.radius + 14}
                    textAnchor="middle"
                    className="fill-text-muted font-sans text-[9.5px] uppercase tracking-[0.04em]"
                  >
                    {n.label}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>
      )}

      {finding && (
        <div className="absolute bottom-3 left-3">
          <span
            className={clsx(
              "border px-2 py-1 font-data text-[10px] uppercase tracking-[0.06em]",
              severityColor[finding.severity]
            )}
          >
            {finding.severity} · {finding.cve}
          </span>
        </div>
      )}

      {hoveredEdge && hoveredEdge.lastMessage && (
        <div
          className="pointer-events-none absolute z-10 max-w-[240px] border border-border bg-panel px-2 py-1.5 font-data text-[10px] text-text-muted shadow-lg"
          style={{ left: mousePos.x + 12, top: mousePos.y + 12 }}
        >
          {hoveredEdge.lastMessage}
        </div>
      )}
    </Panel>
  );
}
