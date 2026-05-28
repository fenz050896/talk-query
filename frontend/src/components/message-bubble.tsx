import { useState } from "react";
import { Bot, User, ThumbsUp, ThumbsDown } from "lucide-react";
import type { ChatResult, Style } from "@/lib/api";
import { submitFeedback } from "@/lib/api";
import { SqlBlock } from "./sql-block";
import { DataTable } from "./data-table";

interface MessageBubbleProps {
  role: "user" | "system";
  content: string;
  result?: ChatResult;
  loading?: boolean;
  style?: Style;
  messageId?: number;
}

function StyleBadge({ style }: { style: Style }) {
  if (style === "normal") return null;
  const labels: Record<string, { text: string; cls: string }> = {
    caveman: { text: "Caveman", cls: "bg-orange-500/10 text-orange-600" },
    rtk: { text: "RTK", cls: "bg-blue-500/10 text-blue-600" },
    "caveman+rtk": { text: "Caveman+RTK", cls: "bg-purple-500/10 text-purple-600" },
  };
  const label = labels[style] || labels.caveman;
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded ml-1 ${label.cls}`}>
      {label.text}
    </span>
  );
}

export function MessageBubble({ role, content, result, loading, style, messageId }: MessageBubbleProps) {
  const isUser = role === "user";
  const [feedback, setFeedback] = useState<"up" | "down" | null>(null);

  const handleFeedback = async (rating: "up" | "down") => {
    if (!messageId) return;
    const newRating = feedback === rating ? null : rating;
    setFeedback(newRating);
    if (newRating) {
      try {
        await submitFeedback(messageId, newRating);
      } catch { /* ignore */ }
    }
  };

  return (
    <div className={`flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
      <div
        className={`flex-shrink-0 h-8 w-8 rounded-full flex items-center justify-center ${
          isUser ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"
        }`}
      >
        {isUser ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
      </div>

      <div className={`flex-1 space-y-1 ${isUser ? "text-right" : ""}`}>
        <div className="text-xs font-medium text-muted-foreground">
          {isUser ? "You" : "TalkQuery"}
          {!isUser && style && <StyleBadge style={style} />}
        </div>

        {loading ? (
          <div className="inline-block px-4 py-2 rounded-lg bg-muted/50">
            <span className="flex items-center gap-1 text-sm text-muted-foreground">
              <span className="animate-pulse">Thinking</span>
              <span className="animate-bounce">...</span>
            </span>
          </div>
        ) : (
          <div
            className={`inline-block max-w-[85%] px-4 py-2 rounded-lg text-sm leading-relaxed ${
              isUser
                ? "bg-primary text-primary-foreground"
                : "bg-muted/50 border"
            }`}
          >
            <p className="whitespace-pre-wrap">{content}</p>

            {result?.sql && <SqlBlock sql={result.sql} />}
            {result && result.columns.length > 0 && result.rows.length > 0 && (
              <>
                <DataTable columns={result.columns} rows={result.rows} />
                <p className="text-xs text-muted-foreground mt-1">
                  {result.row_count} row{result.row_count !== 1 ? "s" : ""}
                </p>
              </>
            )}

            {!isUser && !loading && messageId && (
              <div className="flex items-center gap-1 mt-2">
                <button
                  onClick={() => handleFeedback("up")}
                  className={`p-1 rounded hover:bg-muted transition-colors ${
                    feedback === "up" ? "text-green-600" : "text-muted-foreground/40"
                  }`}
                >
                  <ThumbsUp className="h-3 w-3" />
                </button>
                <button
                  onClick={() => handleFeedback("down")}
                  className={`p-1 rounded hover:bg-muted transition-colors ${
                    feedback === "down" ? "text-red-600" : "text-muted-foreground/40"
                  }`}
                >
                  <ThumbsDown className="h-3 w-3" />
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
