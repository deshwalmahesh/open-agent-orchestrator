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
        map.set(key, { agentName: chat.agent_name ?? "No Pipeline", chats: [] });
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
            <p className="p-4 text-xs text-zinc-400 italic">No chats yet.</p>
          ) : (
            chatGroups.map(({ key, agentName, chats: groupChats }) => (
              <div key={key}>
                {/* Agent group header — slim, neutral */}
                <div className="px-3 py-1.5 text-[9px] font-bold uppercase tracking-[0.18em] text-zinc-400 bg-zinc-50/60 border-b border-zinc-100 sticky top-0 z-10 truncate">
                  {agentName}
                </div>
                {groupChats.map((chat) => {
                  const isActive = chat.id === activeId;
                  const title = chat.title?.trim() || "Untitled";
                  const preview = chat.preview?.trim();
                  return (
                    <div
                      key={chat.id}
                      className={cn(
                        "group/chat relative border-b border-zinc-100 last:border-0 transition-colors",
                        isActive ? "bg-violet-50" : "hover:bg-zinc-50",
                      )}
                    >
                      <button
                        onClick={() => navigate(`/chats/${chat.id}`)}
                        className="w-full text-left px-3 py-2.5 pr-8"
                      >
                        <div className={cn(
                          "text-sm font-medium truncate leading-tight",
                          isActive ? "text-violet-900" : "text-zinc-800",
                        )}>{title}</div>
                        {preview ? (
                          <div className="text-[11px] text-zinc-500 truncate mt-0.5 leading-snug">{preview}</div>
                        ) : (
                          <div className="text-[11px] text-zinc-400 italic mt-0.5">No messages yet</div>
                        )}
                        <div className="text-[10px] text-zinc-400 mt-1 font-mono">
                          {new Date(chat.updated_at).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                        </div>
                      </button>
                      {/* Hover-only delete (no full-row click; doesn't navigate) */}
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          if (confirm(`Delete chat "${title}"? This cannot be undone.`)) {
                            deleteChat_.mutate(chat.id);
                          }
                        }}
                        title="Delete chat"
                        className="absolute top-2.5 right-2 size-6 rounded-md text-zinc-400 hover:text-red-500 hover:bg-red-50 flex items-center justify-center text-sm opacity-0 group-hover/chat:opacity-100 transition-opacity"
                      >
                        ×
                      </button>
                    </div>
                  );
                })}
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
            {/* Chat header — calm, single row, neutral type */}
            <div className="px-5 py-3 border-b border-zinc-100 flex items-center gap-3 bg-white">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-zinc-900 truncate leading-tight">
                  {activeChat.title?.trim() || "Untitled"}
                </p>
                {activeChat.agent_name && (
                  <p className="text-[11px] text-zinc-500 mt-0.5 truncate">
                    <span className="text-zinc-400">via</span> {activeChat.agent_name}
                  </p>
                )}
              </div>
              <Button variant="ghost" size="sm" className="text-xs text-zinc-600" onClick={() => setReassignOpen(true)}>
                Reassign
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="text-xs text-zinc-500 hover:text-red-500 hover:bg-red-50"
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

            {/* Compose bar — single rounded panel; attach + send live inside */}
            <div className="px-5 pt-3 pb-4 bg-white border-t border-zinc-100">
              <div
                className={cn(
                  "rounded-2xl border bg-white transition-colors",
                  sending || isRunning
                    ? "border-zinc-200 opacity-70"
                    : "border-zinc-200 focus-within:border-violet-400 focus-within:ring-2 focus-within:ring-violet-100",
                )}
              >
                {files.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 px-3 pt-2.5">
                    {files.map((f, i) => (
                      <span
                        key={i}
                        className="inline-flex items-center gap-1.5 bg-zinc-100 text-zinc-700 text-[11px] px-2 py-0.5 rounded-md font-medium"
                      >
                        <span className="text-zinc-400">↳</span>
                        {f.name}
                        <button
                          type="button"
                          className="text-zinc-400 hover:text-red-500 -mr-0.5"
                          onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                )}
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
                <Textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Type a message…"
                  rows={2}
                  className="border-0 shadow-none focus-visible:ring-0 resize-none px-3.5 py-2.5 text-sm leading-relaxed"
                  disabled={sending || isRunning}
                />
                <div className="flex items-center px-2 pb-2">
                  <button
                    type="button"
                    title="Attach image or PDF"
                    className="size-8 rounded-lg text-zinc-400 hover:text-zinc-700 hover:bg-zinc-50 transition-colors disabled:opacity-40 flex items-center justify-center"
                    disabled={sending || isRunning}
                    onClick={() => fileInputRef.current?.click()}
                  >
                    <span className="text-base leading-none">+</span>
                  </button>
                  <div className="flex-1" />
                  <span className="text-[10px] text-zinc-400 mr-2 font-mono">⌘↵</span>
                  <Button
                    onClick={handleSend}
                    disabled={sending || isRunning || !input.trim()}
                    size="sm"
                    className="bg-violet-600 hover:bg-violet-700 text-white px-3 h-8 text-xs font-semibold"
                  >
                    {sending || isRunning ? "Sending…" : "Send"}
                  </Button>
                </div>
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
    <div className={cn("flex gap-2.5", isUser ? "justify-end" : "justify-start")}>
      {!isUser && (
        <div className="size-7 rounded-full bg-violet-100 border border-violet-200 flex items-center justify-center text-[10px] font-bold tracking-wider text-violet-700 shrink-0 mt-0.5">
          AI
        </div>
      )}
      <div
        className={cn(
          "max-w-[78%] rounded-2xl px-3.5 py-2.5 text-sm whitespace-pre-wrap leading-relaxed",
          isUser
            ? "bg-violet-600 text-white rounded-tr-md"
            : "bg-white text-zinc-800 border border-zinc-200 rounded-tl-md shadow-sm",
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
  // Drafts are excluded — they can't be used until deployed.
  const pipelines = agents.filter((a) => isPipelineRoot(a, agents) && !!a.deployed_at);

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
  const navigate = useNavigate();
  const [agentId, setAgentId] = useState("");
  const [title, setTitle] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const { data: agents = [], isLoading: agentsLoading } = useQuery({
    queryKey: ["agents"],
    queryFn: () => listAgents(token),
    enabled: open,
  });

  // New chat picks a deployed pipeline (root). Drafts excluded — they must be
  // deployed before they can be used.
  const allPipelines = agents.filter((a) => isPipelineRoot(a, agents));
  const pipelines = allPipelines.filter((a) => !!a.deployed_at);
  const noPipelines = !agentsLoading && pipelines.length === 0;
  // If the user has pipelines but they're all drafts, surface a different CTA.
  const onlyDrafts = noPipelines && allPipelines.length > 0;

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
        {noPipelines ? (
          <div className="py-6 text-center space-y-4">
            <div>
              <p className="text-sm font-medium text-zinc-900">
                {onlyDrafts ? "No deployed pipelines" : "No pipelines yet"}
              </p>
              <p className="text-xs text-zinc-500 mt-1">
                {onlyDrafts
                  ? "All your pipelines are still in Draft. Deploy one to use it in a chat."
                  : "A chat runs against a pipeline. Build one first, then come back here."}
              </p>
            </div>
            <Button
              className="bg-violet-600 hover:bg-violet-700 text-white"
              onClick={() => { onOpenChange(false); navigate("/agents"); }}
            >
              {onlyDrafts ? "Go to Pipelines" : "Create your first pipeline"}
            </Button>
          </div>
        ) : (
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
              <Label>Theme (optional)</Label>
              <Input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="What's this chat about? e.g. Q3 marketing plan"
              />
              <p className="text-[11px] text-zinc-400">
                Shown in the sidebar so you can find the chat later.
              </p>
            </div>

            <Button
              className="w-full bg-violet-600 hover:bg-violet-700 text-white"
              onClick={handleCreate}
              disabled={!agentId || submitting}
            >
              {submitting ? "Creating…" : "Create Chat"}
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
