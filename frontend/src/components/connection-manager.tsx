"use client";

import { useState } from "react";
import { X, Plus, Pencil, Trash2, Circle, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConnectionForm } from "@/components/connection-form";
import { useConnections } from "@/lib/connection-store";
import { analyzeConnection, type Connection, type ConnectionFormData, type TestResult } from "@/lib/api";

interface Props {
  open: boolean;
  onClose: () => void;
}

const DB_ICONS: Record<string, string> = { postgresql: "🐘", mysql: "🐬", sqlite: "💾" };

export function ConnectionManager({ open, onClose }: Props) {
  const { connections, createConnection, updateConnection, deleteConnection, testConnection, testExisting, refreshConnections } = useConnections();
  const [formOpen, setFormOpen] = useState(false);
  const [editConn, setEditConn] = useState<Connection | null>(null);

  if (!open) return null;

  const handleSave = async (data: ConnectionFormData) => {
    if (editConn) {
      await updateConnection(editConn.id, data);
    } else {
      await createConnection(data);
    }
  };

  const handleTest = async (data: ConnectionFormData): Promise<TestResult> => {
    return testConnection(data);
  };

  const handleDelete = async (conn: Connection) => {
    if (!confirm(`Delete connection "${conn.name}"?`)) return;
    try {
      await deleteConnection(conn.id);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const handleEdit = (conn: Connection) => {
    setEditConn(conn);
    setFormOpen(true);
  };

  const [analyzingIds, setAnalyzingIds] = useState<Set<string>>(new Set());

  const handleAnalyze = async (conn: Connection) => {
    setAnalyzingIds((prev) => new Set(prev).add(conn.id));
    try {
      const result = await analyzeConnection(conn.id);
      alert(`Analysis complete: ${result.tables_analyzed} tables analyzed.`);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Analysis failed.");
    } finally {
      setAnalyzingIds((prev) => {
        const next = new Set(prev);
        next.delete(conn.id);
        return next;
      });
    }
  };

  const handleTestExisting = async (conn: Connection) => {
    const result = await testExisting(conn.id);
    alert(result.success ? `Connected! ${result.tables.length} tables.` : `Failed: ${result.message}`);
    await refreshConnections();
  };

  return (
    <>
      <div className="fixed inset-0 z-40 flex justify-end">
        <div className="fixed inset-0 bg-black/30" onClick={onClose} />
        <div className="relative w-full max-w-sm bg-background border-l h-full overflow-y-auto p-6 z-50">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-lg font-semibold">Connections</h2>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="xs" onClick={() => { setEditConn(null); setFormOpen(true); }}>
                <Plus className="h-3.5 w-3.5 mr-1" />
                Add
              </Button>
              <button onClick={onClose} className="p-1 rounded-md hover:bg-muted">
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>

          {connections.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-12">
              No connections yet.<br />Add your first database.
            </p>
          ) : (
            <div className="space-y-2">
              {connections.map((conn) => (
                <div key={conn.id} className="border rounded-lg p-3">
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2">
                      <span>{DB_ICONS[conn.db_type] || "📦"}</span>
                      <div>
                        <p className="text-sm font-medium">{conn.name}</p>
                        <p className="text-xs text-muted-foreground">
                          {conn.db_type} • {conn.host ? `${conn.host}:${conn.port}/` : ""}{conn.database_name}
                        </p>
                      </div>
                    </div>
                    <Circle
                      className={`h-2 w-2 fill-current mt-1.5 ${
                        conn.health_status === "ok" ? "text-green-500" : conn.health_status === "error" ? "text-red-500" : "text-muted-foreground/30"
                      }`}
                    />
                  </div>
                  <div className="flex gap-1 mt-3">
                    <Button variant="ghost" size="xs" onClick={() => handleTestExisting(conn)}>
                      Test
                    </Button>
                    <Button variant="ghost" size="xs" disabled={analyzingIds.has(conn.id)} onClick={() => handleAnalyze(conn)}>
                      <RefreshCw className={`h-3 w-3 mr-1 ${analyzingIds.has(conn.id) ? "animate-spin" : ""}`} />
                      Analyze
                    </Button>
                    <Button variant="ghost" size="xs" onClick={() => handleEdit(conn)}>
                      <Pencil className="h-3 w-3 mr-1" />
                      Edit
                    </Button>
                    <Button variant="ghost" size="xs" onClick={() => handleDelete(conn)}>
                      <Trash2 className="h-3 w-3 mr-1" />
                      Delete
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <ConnectionForm
        open={formOpen}
        onClose={() => { setFormOpen(false); setEditConn(null); }}
        onSave={handleSave}
        onTest={handleTest}
        editConnection={editConn}
      />
    </>
  );
}
