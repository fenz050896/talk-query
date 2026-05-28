"use client";

import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from "react";
import {
  type Connection,
  type ConnectionFormData,
  type TestResult,
  fetchConnections,
  createConnection as apiCreateConnection,
  updateConnection as apiUpdateConnection,
  deleteConnection as apiDeleteConnection,
  testConnection as apiTestConnection,
  testExistingConnection as apiTestExistingConnection,
} from "@/lib/api";

interface ConnectionContextValue {
  connections: Connection[];
  loading: boolean;
  refreshConnections: () => Promise<void>;
  createConnection: (data: ConnectionFormData) => Promise<Connection>;
  updateConnection: (id: string, data: Partial<ConnectionFormData>) => Promise<Connection>;
  deleteConnection: (id: string) => Promise<void>;
  testConnection: (data: ConnectionFormData) => Promise<TestResult>;
  testExisting: (id: string) => Promise<TestResult>;
}

const ConnectionContext = createContext<ConnectionContextValue | null>(null);

export function ConnectionProvider({ children }: { children: ReactNode }) {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [loading, setLoading] = useState(true);

  const refreshConnections = useCallback(async () => {
    try {
      const list = await fetchConnections();
      setConnections(list);
    } catch (err) {
      console.error("Failed to fetch connections:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshConnections();
  }, [refreshConnections]);

  const createConn = useCallback(async (data: ConnectionFormData) => {
    const conn = await apiCreateConnection(data);
    await refreshConnections();
    return conn;
  }, [refreshConnections]);

  const updateConn = useCallback(async (id: string, data: Partial<ConnectionFormData>) => {
    const conn = await apiUpdateConnection(id, data);
    await refreshConnections();
    return conn;
  }, [refreshConnections]);

  const deleteConn = useCallback(async (id: string) => {
    await apiDeleteConnection(id);
    await refreshConnections();
  }, [refreshConnections]);

  const testConn = useCallback(async (data: ConnectionFormData) => {
    return apiTestConnection(data);
  }, []);

  const testExisting = useCallback(async (id: string) => {
    return apiTestExistingConnection(id);
  }, []);

  return (
    <ConnectionContext.Provider value={{
      connections, loading,
      refreshConnections,
      createConnection: createConn,
      updateConnection: updateConn,
      deleteConnection: deleteConn,
      testConnection: testConn,
      testExisting,
    }}>
      {children}
    </ConnectionContext.Provider>
  );
}

export function useConnections() {
  const ctx = useContext(ConnectionContext);
  if (!ctx) throw new Error("useConnections must be used within ConnectionProvider");
  return ctx;
}
