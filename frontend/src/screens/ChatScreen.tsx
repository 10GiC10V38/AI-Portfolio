// frontend/src/screens/ChatScreen.tsx
import { useState, useEffect, useRef, useCallback } from "react";
import { chat, ChatMessage } from "../api";

const STARTER_PROMPTS = [
  "How concentrated am I in tech stocks?",
  "Which of my holdings have the highest risk right now?",
  "What would happen to my portfolio if Reliance dropped 20%?",
  "Am I overweight any sector? Should I rebalance?",
  "Which holding has the best fundamentals right now?",
  "Summarise all alerts from this week for me.",
];

// ── Inline markdown renderer (no external deps) ──────────────────────────────

function renderInline(text: string): React.ReactNode[] {
  // Process **bold**, *italic*, `code`
  const parts: React.ReactNode[] = [];
  const re = /(\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`)/g;
  let last = 0, m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    if (m[2])      parts.push(<strong key={key++}>{m[2]}</strong>);
    else if (m[3]) parts.push(<em key={key++}>{m[3]}</em>);
    else if (m[4]) parts.push(<code key={key++}>{m[4]}</code>);
    last = re.lastIndex;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function renderMarkdown(text: string): React.ReactNode {
  const lines = text.split("\n");
  const nodes: React.ReactNode[] = [];
  let i = 0, key = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Headings
    if (line.startsWith("### ")) {
      nodes.push(<h3 key={key++}>{renderInline(line.slice(4))}</h3>);
      i++; continue;
    }
    if (line.startsWith("## ")) {
      nodes.push(<h2 key={key++}>{renderInline(line.slice(3))}</h2>);
      i++; continue;
    }
    if (line.startsWith("# ")) {
      nodes.push(<h2 key={key++}>{renderInline(line.slice(2))}</h2>);
      i++; continue;
    }

    // Unordered list — collect consecutive
    if (/^[-*] /.test(line)) {
      const items: React.ReactNode[] = [];
      while (i < lines.length && /^[-*] /.test(lines[i])) {
        items.push(<li key={i}>{renderInline(lines[i].slice(2))}</li>);
        i++;
      }
      nodes.push(<ul key={key++}>{items}</ul>);
      continue;
    }

    // Ordered list — collect consecutive
    if (/^\d+\. /.test(line)) {
      const items: React.ReactNode[] = [];
      while (i < lines.length && /^\d+\. /.test(lines[i])) {
        items.push(<li key={i}>{renderInline(lines[i].replace(/^\d+\. /, ""))}</li>);
        i++;
      }
      nodes.push(<ol key={key++}>{items}</ol>);
      continue;
    }

    // Horizontal rule
    if (/^---+$/.test(line.trim())) {
      nodes.push(<hr key={key++} />);
      i++; continue;
    }

    // Blank line — skip (paragraph spacing handled by CSS)
    if (line.trim() === "") {
      i++; continue;
    }

    // Paragraph
    nodes.push(<p key={key++}>{renderInline(line)}</p>);
    i++;
  }
  return <>{nodes}</>;
}

// ── Components ───────────────────────────────────────────────────────────────

function MessageBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === "user";
  return (
    <div className={`message-row ${isUser ? "user" : "assistant"}`}>
      {!isUser && (
        <div className="avatar"><span>AI</span></div>
      )}
      <div className={`bubble ${isUser ? "bubble-user" : "bubble-assistant"}`}>
        {isUser ? msg.content : renderMarkdown(msg.content)}
      </div>
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="message-row assistant">
      <div className="avatar"><span>AI</span></div>
      <div className="bubble bubble-assistant typing">
        <span className="dot" /><span className="dot" /><span className="dot" />
      </div>
    </div>
  );
}

export function ChatScreen() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput]       = useState("");
  const [loading, setLoading]   = useState(false);
  const [sessionId]             = useState(() => crypto.randomUUID());
  const bottomRef               = useRef<HTMLDivElement>(null);
  const inputRef                = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = () =>
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });

  useEffect(scrollToBottom, [messages, loading]);

  const sendMessage = useCallback(async (text?: string) => {
    const content = (text ?? input).trim();
    if (!content || loading) return;

    setMessages(prev => [...prev, { role: "user", content }]);
    setInput("");
    setLoading(true);

    try {
      const resp = await chat.send(content, sessionId);
      setMessages(prev => [...prev, { role: "assistant", content: resp.reply }]);
    } catch (e: any) {
      setMessages(prev => [...prev, {
        role: "assistant",
        content: `Sorry, something went wrong: ${e.message}`,
      }]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }, [input, loading, sessionId]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const isEmpty = messages.length === 0;

  return (
    <div className="chat-screen">
      <div className="message-thread">
        {isEmpty && (
          <div className="chat-welcome">
            <div className="welcome-icon">📊</div>
            <h2>Portfolio Advisor</h2>
            <p>Ask me anything about your portfolio. I have live access to your holdings, recent alerts, and market data.</p>
            <div className="starter-prompts">
              {STARTER_PROMPTS.map(p => (
                <button
                  key={p}
                  className="starter-prompt-btn"
                  onClick={() => sendMessage(p)}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <MessageBubble key={i} msg={msg} />
        ))}

        {loading && <TypingIndicator />}
        <div ref={bottomRef} />
      </div>

      <div className="chat-input-bar">
        <textarea
          ref={inputRef}
          className="chat-input"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your portfolio…"
          rows={1}
          disabled={loading}
        />
        <button
          className="send-btn"
          onClick={() => sendMessage()}
          disabled={!input.trim() || loading}
          aria-label="Send"
        >
          ↑
        </button>
      </div>
    </div>
  );
}
