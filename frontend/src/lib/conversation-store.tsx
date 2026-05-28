"use client";

import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from "react";
import {
  type Conversation,
  type ConversationDetail,
  fetchConversations,
  fetchConversation as apiFetchConversation,
  createConversation as apiCreateConversation,
  updateConversationTitle as apiUpdateTitle,
  deleteConversation as apiDeleteConversation,
} from "@/lib/api";

interface ConversationContextValue {
  conversations: Conversation[];
  activeConversationId: string | null;
  activeConversation: ConversationDetail | null;
  loading: boolean;
  setActiveConversationId: (id: string | null) => void;
  refreshConversations: () => Promise<void>;
  createConversation: (connectionId: string, title?: string) => Promise<Conversation>;
  deleteConversation: (id: string) => Promise<void>;
}

const ConversationContext = createContext<ConversationContextValue | null>(null);

export function ConversationProvider({ children }: { children: ReactNode }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveId] = useState<string | null>(null);
  const [activeConversation, setActiveConversation] = useState<ConversationDetail | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshConversations = useCallback(async () => {
    try {
      const list = await fetchConversations();
      setConversations(list);
    } catch (err) {
      console.error("Failed to fetch conversations:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshConversations();
  }, [refreshConversations]);

  // When activeConversationId changes, load full detail
  const setActiveConversationId = useCallback(async (id: string | null) => {
    setActiveId(id);
    if (!id) {
      setActiveConversation(null);
      return;
    }
    try {
      const detail = await apiFetchConversation(id);
      setActiveConversation(detail);
    } catch (err) {
      console.error("Failed to load conversation:", err);
    }
  }, []);

  const createConv = useCallback(async (connectionId: string, title?: string) => {
    const conv = await apiCreateConversation(connectionId, title);
    await refreshConversations();
    await setActiveConversationId(conv.id);
    return conv;
  }, [refreshConversations, setActiveConversationId]);

  const deleteConv = useCallback(async (id: string) => {
    await apiDeleteConversation(id);
    if (activeConversationId === id) {
      setActiveId(null);
      setActiveConversation(null);
    }
    await refreshConversations();
  }, [activeConversationId, refreshConversations]);

  return (
    <ConversationContext.Provider value={{
      conversations, activeConversationId, activeConversation,
      loading, setActiveConversationId, refreshConversations,
      createConversation: createConv, deleteConversation: deleteConv,
    }}>
      {children}
    </ConversationContext.Provider>
  );
}

export function useConversations() {
  const ctx = useContext(ConversationContext);
  if (!ctx) throw new Error("useConversations must be used within ConversationProvider");
  return ctx;
}
