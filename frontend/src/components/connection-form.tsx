"use client";

import { useState, useEffect } from "react";
import { X, Loader2, CheckCircle, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { type Connection, type ConnectionFormData, type TestResult } from "@/lib/api";

interface Props {
  open: boolean;
  onClose: () => void;
  onSave: (data: ConnectionFormData) => Promise<void>;
  onTest: (data: ConnectionFormData) => Promise<TestResult>;
  editConnection?: Connection | null;
}

export function ConnectionForm({ open, onClose, onSave, onTest, editConnection }: Props) {
  const [dbType, setDbType] = useState<"sqlite" | "postgresql" | "mysql">("postgresql");
  const [name, setName] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("5432");
  const [databaseName, setDatabaseName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [sslMode, setSslMode] = useState("prefer");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);

  useEffect(() => {
    if (editConnection) {
      setDbType(editConnection.db_type);
      setName(editConnection.name);
      setHost(editConnection.host || "");
      setPort(editConnection.port?.toString() || (editConnection.db_type === "postgresql" ? "5432" : "3306"));
      setDatabaseName(editConnection.database_name);
      setUsername(editConnection.username || "");
      setPassword("");
      setSslMode(editConnection.ssl_mode);
      setTestResult(null);
    } else {
      setDbType("postgresql");
      setName("");
      setHost("");
      setPort("5432");
      setDatabaseName("");
      setUsername("");
      setPassword("");
      setSslMode("prefer");
      setTestResult(null);
    }
  }, [editConnection, open]);

  const isRemote = dbType !== "sqlite";

  const getFormData = (): ConnectionFormData => {
    const data: ConnectionFormData = {
      name,
      db_type: dbType,
      database_name: databaseName,
      ssl_mode: sslMode,
    };
    if (isRemote) {
      data.host = host || undefined;
      data.port = parseInt(port) || undefined;
      data.username = username || undefined;
      if (password) data.password = password;
    }
    return data;
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await onTest(getFormData());
      setTestResult(result);
    } catch (err: unknown) {
      setTestResult({ success: false, message: err instanceof Error ? err.message : "Test failed", tables: [], db_version: "" });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(getFormData());
      onClose();
    } catch (err) {
      console.error("Save failed:", err);
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/40" onClick={onClose} />
      <div className="relative bg-background border rounded-xl shadow-2xl w-full max-w-lg mx-4 max-h-[85vh] overflow-y-auto p-6">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-lg font-semibold">
            {editConnection ? "Edit Connection" : "Add Connection"}
          </h2>
          <button onClick={onClose} className="p-1 rounded-md hover:bg-muted">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* DB Type */}
        <div className="mb-4">
          <label className="text-sm font-medium mb-1.5 block">Database Type</label>
          <div className="flex gap-1 bg-muted rounded-lg p-1">
            {(["postgresql", "mysql", "sqlite"] as const).map((t) => (
              <button
                key={t}
                onClick={() => {
                  setDbType(t);
                  setTestResult(null);
                  if (t === "sqlite") setPort("0");
                  else if (t === "postgresql") setPort("5432");
                  else setPort("3306");
                }}
                className={`flex-1 text-xs px-3 py-1.5 rounded-md transition-colors ${
                  dbType === t ? "bg-background shadow-sm font-medium" : "text-muted-foreground"
                }`}
              >
                {t === "postgresql" ? "PostgreSQL" : t === "mysql" ? "MySQL" : "SQLite"}
              </button>
            ))}
          </div>
        </div>

        {/* Name */}
        <div className="mb-4">
          <label className="text-sm font-medium mb-1.5 block">Connection Name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g., Production DB"
            className="w-full px-3 py-2 rounded-lg border bg-transparent text-sm outline-none focus-visible:border-ring"
          />
        </div>

        {/* Host (remote only) */}
        {isRemote && (
          <div className="mb-4 grid grid-cols-3 gap-3">
            <div className="col-span-2">
              <label className="text-sm font-medium mb-1.5 block">Host</label>
              <input
                value={host}
                onChange={(e) => setHost(e.target.value)}
                placeholder="localhost"
                className="w-full px-3 py-2 rounded-lg border bg-transparent text-sm outline-none focus-visible:border-ring"
              />
            </div>
            <div>
              <label className="text-sm font-medium mb-1.5 block">Port</label>
              <input
                type="number"
                value={port}
                onChange={(e) => setPort(e.target.value)}
                className="w-full px-3 py-2 rounded-lg border bg-transparent text-sm outline-none focus-visible:border-ring"
              />
            </div>
          </div>
        )}

        {/* Database name */}
        <div className="mb-4">
          <label className="text-sm font-medium mb-1.5 block">
            {dbType === "sqlite" ? "File Path" : "Database Name"}
          </label>
          <input
            value={databaseName}
            onChange={(e) => setDatabaseName(e.target.value)}
            placeholder={dbType === "sqlite" ? "/path/to/database.sqlite" : "my_database"}
            className="w-full px-3 py-2 rounded-lg border bg-transparent text-sm outline-none focus-visible:border-ring"
          />
        </div>

        {/* Username + Password (remote only) */}
        {isRemote && (
          <div className="mb-4 grid grid-cols-2 gap-3">
            <div>
              <label className="text-sm font-medium mb-1.5 block">Username</label>
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="postgres"
                className="w-full px-3 py-2 rounded-lg border bg-transparent text-sm outline-none focus-visible:border-ring"
              />
            </div>
            <div>
              <label className="text-sm font-medium mb-1.5 block">
                Password {editConnection && <span className="text-xs text-muted-foreground">(unchanged)</span>}
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={editConnection ? "••••••••" : ""}
                className="w-full px-3 py-2 rounded-lg border bg-transparent text-sm outline-none focus-visible:border-ring"
              />
            </div>
          </div>
        )}

        {/* SSL Mode (remote only) */}
        {isRemote && (
          <div className="mb-4">
            <label className="text-sm font-medium mb-1.5 block">SSL Mode</label>
            <select
              value={sslMode}
              onChange={(e) => setSslMode(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border bg-transparent text-sm outline-none focus-visible:border-ring"
            >
              <option value="prefer">prefer</option>
              <option value="require">require</option>
              <option value="disable">disable</option>
              <option value="verify-ca">verify-ca</option>
              <option value="verify-full">verify-full</option>
            </select>
          </div>
        )}

        {/* Test result */}
        {testResult && (
          <div className={`mb-4 p-3 rounded-lg text-sm flex items-start gap-2 ${
            testResult.success ? "bg-green-50 text-green-700 border border-green-200" : "bg-red-50 text-red-700 border border-red-200"
          }`}>
            {testResult.success ? <CheckCircle className="h-4 w-4 mt-0.5" /> : <XCircle className="h-4 w-4 mt-0.5" />}
            <div>
              <p className="font-medium">{testResult.success ? "Connected" : "Connection Failed"}</p>
              <p className="text-xs opacity-80">{testResult.message}</p>
              {testResult.success && testResult.tables.length > 0 && (
                <p className="text-xs mt-1 opacity-80">{testResult.tables.length} tables: {testResult.tables.join(", ")}</p>
              )}
            </div>
          </div>
        )}

        {/* Buttons */}
        <div className="flex gap-2 justify-end">
          <Button variant="outline" size="sm" onClick={handleTest} disabled={testing || !databaseName}>
            {testing ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : null}
            Test Connection
          </Button>
          <Button size="sm" onClick={handleSave} disabled={saving || !name || !databaseName}>
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : null}
            {editConnection ? "Save Changes" : "Add Connection"}
          </Button>
        </div>
      </div>
    </div>
  );
}
