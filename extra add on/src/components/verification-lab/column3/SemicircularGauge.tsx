import { motion } from "framer-motion";

const CX = 100;
const CY = 100;
const R = 82;

function polarToCartesian(angleDeg: number, radius: number) {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: CX + radius * Math.cos(rad), y: CY - radius * Math.sin(rad) };
}

function describeArc(startAngle: number, endAngle: number, radius: number) {
  const start = polarToCartesian(startAngle, radius);
  const end = polarToCartesian(endAngle, radius);
  const largeArcFlag = Math.abs(startAngle - endAngle) <= 180 ? 0 : 1;
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArcFlag} 1 ${end.x} ${end.y}`;
}

// pct 0 -> 180deg (left), pct 100 -> 0deg (right)
function angleForPct(pct: number) {
  return 180 - (pct / 100) * 180;
}

const zones = [
  { from: 0, to: 40, color: "var(--sentinel-danger)" },
  { from: 40, to: 75, color: "var(--sentinel-warning)" },
  { from: 75, to: 100, color: "var(--sentinel-success)" },
];

export function SemicircularGauge({ pct }: { pct: number }) {
  const needleAngle = angleForPct(pct);
  const needleTip = polarToCartesian(needleAngle, R - 22);

  return (
    <div className="flex flex-col items-center gap-1 pt-1">
      <svg viewBox="0 0 200 112" className="w-full max-w-[220px]">
        {zones.map((z) => (
          <path
            key={z.from}
            d={describeArc(angleForPct(z.from), angleForPct(z.to), R)}
            fill="none"
            stroke={z.color}
            strokeWidth={14}
            strokeLinecap="butt"
            opacity={0.85}
          />
        ))}
        <motion.line
          x1={CX}
          y1={CY}
          stroke="var(--sentinel-text)"
          strokeWidth={2.5}
          strokeLinecap="round"
          initial={false}
          animate={{ x2: needleTip.x, y2: needleTip.y }}
          transition={{ type: "spring", stiffness: 90, damping: 16 }}
        />
        <circle cx={CX} cy={CY} r={4} fill="var(--sentinel-text)" />
      </svg>
      <div className="-mt-4 flex flex-col items-center">
        <span className="font-data text-[22px] font-medium tabular-nums text-text">{pct.toFixed(0)}%</span>
        <span className="text-[9.5px] uppercase tracking-[0.08em] text-text-dim">current compliance</span>
      </div>
    </div>
  );
}
