"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import {
  IconLayoutDashboard,
  IconFlask2,
  IconGitPullRequest,
  IconFileCheck,
  IconShieldLock,
  IconDoorExit,
  IconListDetails,
  IconBell,
} from "@tabler/icons-react";
import { useAlerts } from "@/lib/sentinel/hooks";

const navItems = [
  { href: "/command-center", icon: IconLayoutDashboard, label: "Command Center" },
  { href: "/verification-lab", icon: IconFlask2, label: "Verification Lab" },
  { href: "/remediation", icon: IconGitPullRequest, label: "Remediation" },
  { href: "/evidence", icon: IconFileCheck, label: "Evidence Report" },
  { href: "/governance", icon: IconShieldLock, label: "Governance" },
  { href: "/audit-ledger", icon: IconListDetails, label: "Audit Ledger" },
  { href: "/deployment-gate", icon: IconDoorExit, label: "Deployment Gate" },
  { href: "/alerts", icon: IconBell, label: "Alerts" },
] as const;

export function IconRail() {
  const pathname = usePathname();
  // Real unread-badge source: the same live alert feed the Alerts page reads,
  // so the count on the rail can never disagree with the page it links to.
  const { criticalCount } = useAlerts();

  return (
    <nav className="flex w-14 shrink-0 flex-col items-center gap-1 border-r border-border-soft bg-panel/40 py-3">
      {navItems.map(({ href, icon: Icon, label }) => {
        const isActive = pathname === href;
        const showBadge = href === "/alerts" && criticalCount > 0;

        return (
          <Link
            prefetch={false}
            key={label}
            href={href}
            title={showBadge ? `${label} — ${criticalCount} critical` : label}
            aria-label={showBadge ? `${label}, ${criticalCount} critical` : label}
            aria-current={isActive ? "page" : undefined}
            className={clsx(
              "relative flex h-10 w-10 items-center justify-center transition-colors",
              isActive ? "text-amber" : "text-text-dim hover:text-text-muted"
            )}
          >
            {isActive && <span className="absolute left-0 top-1/2 h-5 w-[2px] -translate-y-1/2 bg-amber" />}
            <Icon size={18} strokeWidth={1.5} />
            {showBadge && (
              <span className="absolute right-1.5 top-1.5 flex h-[7px] w-[7px] items-center justify-center rounded-full bg-danger ring-2 ring-panel" />
            )}
          </Link>
        );
      })}
    </nav>
  );
}
