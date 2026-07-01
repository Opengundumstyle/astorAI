"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import type { Role } from "@/lib/types";

const NAV = [
  { href: "/", label: "Dashboard", icon: "◧" },
  { href: "/ingest", label: "Ingest & Browse", icon: "⇪" },
];

export function Sidebar() {
  const pathname = usePathname();
  const [role, setRole] = useState<Role>("ops");

  useEffect(() => {
    const saved = window.localStorage.getItem("astor-role") as Role | null;
    if (saved) setRole(saved);
  }, []);

  function pick(next: Role) {
    setRole(next);
    window.localStorage.setItem("astor-role", next);
  }

  return (
    <aside
      className="flex w-56 flex-col gap-6 p-4"
      style={{ background: "var(--bg-elev)", borderRight: "1px solid var(--border)" }}
    >
      <div className="flex items-center gap-2">
        <div className="gradient-accent h-5 w-5 rounded-md" />
        <span className="font-bold">
          Astor<span style={{ color: "var(--teal)" }}>Scientific</span>
        </span>
      </div>

      <nav className="flex flex-col gap-1">
        <div className="px-1 text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>
          Workspace
        </div>
        {NAV.map((item) => {
          const active = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className="rounded-lg px-3 py-2 text-sm"
              style={{
                color: active ? "var(--teal)" : "#aeb8cc",
                background: active ? "rgba(94,234,212,0.1)" : "transparent",
              }}
            >
              <span className="mr-2">{item.icon}</span>
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto">
        <div className="mb-2 px-1 text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>
          Role
        </div>
        <div className="flex gap-1 rounded-lg p-1" style={{ background: "rgba(255,255,255,0.06)" }}>
          {(["ops", "buyer"] as Role[]).map((r) => (
            <button
              key={r}
              onClick={() => pick(r)}
              className="flex-1 rounded-md px-2 py-1 text-xs capitalize"
              style={{
                color: role === r ? "#0d1322" : "#aeb8cc",
                background: role === r ? "var(--teal)" : "transparent",
                fontWeight: role === r ? 700 : 400,
              }}
            >
              {r}
            </button>
          ))}
        </div>
        <p className="mt-2 px-1 text-[10px]" style={{ color: "var(--muted)" }}>
          M1 builds the Ops views. Buyer hides origin & supplier.
        </p>
      </div>
    </aside>
  );
}
