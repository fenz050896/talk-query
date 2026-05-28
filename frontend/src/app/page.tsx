import { ConversationSidebar } from "@/components/conversation-sidebar";
import { ChatPanel } from "@/components/chat-panel";

export default function Home() {
  return (
    <div className="flex h-screen">
      <ConversationSidebar />
      <div className="flex-1 min-w-0">
        <ChatPanel />
      </div>
    </div>
  );
}
