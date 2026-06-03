import { useState, useRef, useEffect, useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import RunEventsPanel from "@/components/RunEventsPanel";
import { listChats, createChat, deleteChat, patchChat, getMessages, sendMessage, type FileAttachment } from "@/api/chats";
import { listAgents } from "@/api/agents";
import { useAuth } from "@/hooks/useAuth";
import { useSSE } from "@/hooks/useSSE";
import type { Message } from "@/types";
import { cn, isPipelineRoot } from "@/lib/utils";

function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve((reader.result as string).split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

export default function ChatPage() {
  const { token } = useAuth();
  const { id: paramId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const activeId = paramId ?? null;
  const [newChatOpen, setNewChatOpen] = useState(false);
  const [reassignOpen, setReassignOpen] = useState(false);
  const [input, setInput] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [sending, setSending] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const runEvents = useSSE(activeRunId);
  const runFinished = runEvents.some((e) => ["run.finished", "run.error"].includes(e.type));
  const isRunning = !!activeRunId && !runFinished;
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const { data: chats = [] } = useQuery({
    queryKey: ["chats"],
    queryFn: () => listChats(token!),
    enabled: !!token,
  });

  const { data: messages = [], refetch: refetchMessages } = useQuery({
    queryKey: ["messages", activeId],
    queryFn: () => getMessages(token!, activeId!),
    enabled: !!activeId && !!token,
  });

  // auto-scroll on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  useEffect(() => {
    if (runFinished) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setActiveRunId(null);
      refetchMessages();
    }
  }, [runFinished, refetchMessages]);

  const deleteChat_ = useMutation({
    mutationFn: (id: string) => deleteChat(token!, id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ["chats"] });
      if (paramId === id) navigate("/chats");
    },
    onError: (err) => console.error("Delete chat failed:", err),
  });

  async function handleSend() {
    if (!input.trim() || !activeId) return;
    setSending(true);
    try {
      const attachments: FileAttachment[] = await Promise.all(
        files.map(async (f) => ({
          name: f.name,
          content_base64: await toBase64(f),
          mime_type: f.type || "application/octet-stream",
        })),
      );
      const { run_id } = await sendMessage(token!, activeId, input.trim(), attachments);
      setInput("");
      setFiles([]);
      setActiveRunId(run_id);
      await refetchMessages();
    } catch (err) {
      console.error("Send message failed:", err);
    } finally {
      setSending(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSend();
  }

  const activeChat = chats.find((c) => c.id === activeId);

  // Group chats by agent_id. Backend already returns chats sorted by updated_at DESC,
  // so groups are ordered by their most-recently-active chat (first element in each group).
  const chatGroups = useMemo(() => {
    const map = new Map<string, { agentName: string; chats: typeof chats }>();
    for (const chat of chats) {
      const key = chat.agent_id ?? "__no_agent__";
      if (!map.has(key)) {
        map.set(key, { agentName: chat.agent_name ?? "No Agent", chats: [] });
      }
      map.get(key)!.chats.push(chat);
    }
    return Array.from(map.entries()).map(([key, val]) => ({ key, ...val }));
  }, [chats]);

  return (
    <div className="flex h-full">
      {/* Sidebar: chat list */}
      <aside className="w-64 border-r flex flex-col shrink-0">
        <div className="p-3 border-b flex items-center justify-between">
          <span className="text-sm font-semibold">Chats</span>
          <Button size="sm" variant="outline" onClick={() => setNewChatOpen(true)}>
            + New
          </Button>
        </div>
        <ScrollArea className="flex-1">
          {chats.length === 0 ? (
            <p className="p-3 text-xs text-muted-foreground">No chats yet.</p>
          ) : (
            chatGroups.map(({ key, agentName, chats: groupChats }) => (
              <div key={key}>
                {/* Agent group header */}
                <div className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground bg-muted/40 border-b sticky top-0 z-10 truncate">
                  {agentName}
                </div>
                {groupChats.map((chat) => (
                  <button
                    key={chat.id}
                    onClick={() => navigate(`/chats/${chat.id}`)}
                    className={cn(
                      "w-full text-left px-3 py-2 text-sm hover:bg-accent/40 transition-colors border-b last:border-0",
                      chat.id === activeId && "bg-accent",
                    )}
                  >
                    <div className="font-medium truncate">{chat.title ?? "Chat"}</div>
                    <div className="text-xs text-muted-foreground truncate">
                      {new Date(chat.updated_at).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                    </div>
                  </button>
                ))}
              </div>
            ))
          )}
        </ScrollArea>
      </aside>

      {/* Main: messages + input */}
      <div className="flex-1 flex flex-col min-w-0">
        {!activeChat ? (
          <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
            Select a chat or create one.
          </div>
        ) : (
          <>
            {/* Chat header */}
            <div className="px-4 py-3 border-b flex items-center gap-2">
              <span className="font-medium">{activeChat.title ?? "Chat"}</span>
              {activeChat.agent_name && (
                <Badge variant="secondary" className="text-xs">{activeChat.agent_name}</Badge>
              )}
              <div className="flex-1" />
              <Button variant="ghost" size="sm" onClick={() => setReassignOpen(true)}>
                Reassign
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="text-destructive hover:text-destructive"
                onClick={() => {
                  if (confirm("Delete this chat?")) deleteChat_.mutate(activeId!);
                }}
              >
                Delete
              </Button>
            </div>

            {/* Messages */}
            <ScrollArea className="flex-1 px-4 py-3">
              <div className="space-y-3">
                {messages.map((msg) => (
                  <MessageBubble key={msg.id} msg={msg} />
                ))}
                <div ref={messagesEndRef} />
              </div>
            </ScrollArea>

            {/* SSE events panel */}
            <RunEventsPanel events={runEvents} isRunning={isRunning} />

            {/* Input */}
            <div className="p-3 border-t space-y-2">
              {files.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {files.map((f, i) => (
                    <span
                      key={i}
                      className="flex items-center gap-1 bg-muted text-xs px-2 py-1 rounded-md"
                    >
                      {f.name}
                      <button
                        type="button"
                        className="text-muted-foreground hover:text-foreground ml-0.5"
                        onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              )}
              <div className="flex gap-2">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*,application/pdf"
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    const picked = Array.from(e.target.files ?? []);
                    setFiles((prev) => [...prev, ...picked]);
                    e.target.value = "";
                  }}
                />
                <button
                  type="button"
                  title="Attach image or PDF"
                  className="self-end p-2 rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors disabled:opacity-40"
                  disabled={sending || isRunning}
                  onClick={() => fileInputRef.current?.click()}
                >
                  📎
                </button>
                <Textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Type a message… (Ctrl+Enter to send)"
                  rows={2}
                  className="flex-1 resize-none"
                  disabled={sending || isRunning}
                />
                <Button
                  onClick={handleSend}
                  disabled={sending || isRunning || !input.trim()}
                  className="self-end"
                >
                  {sending ? "…" : "Send"}
                </Button>
              </div>
            </div>
          </>
        )}
      </div>

      <NewChatDialog
        open={newChatOpen}
        onOpenChange={setNewChatOpen}
        token={token!}
        onCreated={(id) => {
          qc.invalidateQueries({ queryKey: ["chats"] });
          navigate(`/chats/${id}`);
          setNewChatOpen(false);
        }}
      />

      <ReassignDialog
        open={reassignOpen}
        onOpenChange={setReassignOpen}
        chatId={activeId}
        token={token!}
        onSaved={() => qc.invalidateQueries({ queryKey: ["chats"] })}
      />
    </div>
  );
}

function MessageBubble({ msg }: { msg: Message }) {
  const isUser = msg.sender === "user";
  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[75%] rounded-xl px-3 py-2 text-sm whitespace-pre-wrap",
          isUser ? "bg-primary text-primary-foreground" : "bg-muted",
        )}
      >
        {msg.content}
      </div>
    </div>
  );
}

// ── Reassign Dialog ──────────────────────────────────────────────────────────
function ReassignDialog({
  open, onOpenChange, chatId, token, onSaved,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  chatId: string | null;
  token: string;
  onSaved: () => void;
}) {
  const [agentId, setAgentId] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: () => listAgents(token),
    enabled: open,
  });

  // Chats are reassigned to whole pipelines (roots), not internal sub-agents.
  const pipelines = agents.filter((a) => isPipelineRoot(a, agents));

  async function handleSave() {
    if (!chatId || !agentId) return;
    setSubmitting(true);
    try {
      await patchChat(token, chatId, { agent_id: agentId });
      onSaved();
      onOpenChange(false);
      setAgentId("");
    } catch (err) {
      console.error("Reassign agent failed:", err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader><DialogTitle>Reassign Pipeline</DialogTitle></DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1">
            <Label>Pipeline</Label>
            <Select value={agentId} onValueChange={(v: string | null) => setAgentId(v ?? "")}>
              <SelectTrigger><SelectValue placeholder="Select pipeline" /></SelectTrigger>
              <SelectContent>
                {pipelines.map((a) => (
                  <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button className="w-full" onClick={handleSave} disabled={!agentId || submitting}>
            {submitting ? "Saving…" : "Reassign"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ── New Chat Dialog ──────────────────────────────────────────────────────────
function NewChatDialog({
  open,
  onOpenChange,
  token,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  token: string;
  onCreated: (id: string) => void;
}) {
  const [agentId, setAgentId] = useState("");
  const [title, setTitle] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: () => listAgents(token),
    enabled: open,
  });

  // New chat picks a pipeline (root), not an internal sub-agent.
  const pipelines = agents.filter((a) => isPipelineRoot(a, agents));

  async function handleCreate() {
    if (!agentId) return;
    setSubmitting(true);
    try {
      const chat = await createChat(token, {
        agent_id: agentId,
        title: title.trim() || undefined,
      });
      onCreated(chat.id);
      setAgentId(""); setTitle("");
    } catch (err) {
      console.error("Create chat failed:", err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>New Chat</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1">
            <Label>Pipeline *</Label>
            <Select value={agentId} onValueChange={(v: string | null) => setAgentId(v ?? "")}>
              <SelectTrigger><SelectValue placeholder="Select pipeline" /></SelectTrigger>
              <SelectContent>
                {pipelines.map((a) => (
                  <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1">
            <Label>Title (optional)</Label>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="My research task" />
          </div>

          <Button className="w-full" onClick={handleCreate} disabled={!agentId || submitting}>
            {submitting ? "Creating…" : "Create Chat"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
