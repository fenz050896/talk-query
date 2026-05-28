"use client";

import { useState } from "react";
import { Database, ChevronDown, Circle } from "lucide-react";
import { useConnections } from "@/lib/connection-store";
import type { Connection } from "@/lib/api";

interface Props {
  selectedId: string | null;
  onSelect: (connection: Connection) => void;
}

const DB_ICONS: Record<string, string> = {
  postgresql: "🐘",
  mysql: "🐬",
  sqlite: "💾",
};

export function ConnectionSelector({ selectedId, onSelect }: Props) {
  const { connections } = useConnections();
  const [open, setOpen] = useState(false);

  const selected = connections.find((c) => c.id === selectedId);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 px-3 py-2 rounded-lg border bg-background text-sm hover:bg-muted/50 transition-colors"
      >
        <Database className="h-3.5 w-3.5 text-muted-foreground" />
        <span className={selected ? "" : "text-muted-foreground"}>
          {selected ? selected.name : "Select database"}
        </span>
        <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute top-full mt-1 left-0 w-72 bg-background border rounded-lg shadow-lg z-20 py-1">
            {connections.length === 0 ? (
              <p className="px-3 py-4 text-sm text-muted-foreground text-center">
                No connections yet. Add one first.
              </p>
            ) : (
              connections.map((conn) => (
                <button
                  key={conn.id}
                  onClick={() => {
                    onSelect(conn);
                    setOpen(false);
                  }}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 text-sm hover:bg-muted transition-colors ${
                    conn.id === selectedId ? "bg-muted/50" : ""
                  }`}
                >
                  <span className="text-base">{DB_ICONS[conn.db_type] || "📦"}</span>
                  <div className="flex-1 text-left">
                    <div className="font-medium">{conn.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {conn.db_type} • {conn.database_name}
                    </div>
                  </div>
                  <Circle
                    className={`h-2 w-2 fill-current ${
                      conn.health_status === "ok" ? "text-green-500" : conn.health_status === "error" ? "text-red-500" : "text-muted-foreground/30"
                    }`}
                  />
                </button>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}
