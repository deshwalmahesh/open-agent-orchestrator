import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { listSkills, createSkill, updateSkill, deleteSkill } from "@/api/skills";
import { useAuth } from "@/hooks/useAuth";
import type { Skill } from "@/types";

type Editing = Skill | "new" | null;

export default function Skills() {
  const { token } = useAuth();
  const qc = useQueryClient();
  const [editing, setEditing] = useState<Editing>(null);
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const { data: skills = [], isLoading, error } = useQuery({
    queryKey: ["skills"],
    queryFn: () => listSkills(token!),
    enabled: !!token,
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteSkill(token!, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["skills"] }),
    onError: (err) => console.error("Delete skill failed:", err),
  });

  function openCreate() {
    setName("");
    setContent("");
    setEditing("new");
  }

  function openEdit(skill: Skill) {
    setName(skill.name);
    setContent(skill.content);
    setEditing(skill);
  }

  function closeDialog() {
    setEditing(null);
    setName("");
    setContent("");
  }

  async function handleSave() {
    if (!name.trim() || !content.trim()) return;
    setSubmitting(true);
    try {
      if (editing === "new") {
        await createSkill(token!, { name: name.trim(), content: content.trim() });
      } else if (editing) {
        await updateSkill(token!, editing.id, { name: name.trim(), content: content.trim() });
      }
      await qc.invalidateQueries({ queryKey: ["skills"] });
      closeDialog();
    } catch (err) {
      console.error("Save skill failed:", err);
    } finally {
      setSubmitting(false);
    }
  }

  if (isLoading) return <div className="p-6 text-muted-foreground">Loading skills…</div>;
  if (error) return <div className="p-6 text-destructive">Failed to load skills.</div>;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold">Skills</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Context documents injected into an agent's system prompt at runtime.
          </p>
        </div>
        <Button onClick={openCreate}>+ New Skill</Button>
      </div>

      {skills.length === 0 ? (
        <div className="text-muted-foreground text-sm">
          No skills yet. Create one and assign it to an agent.
        </div>
      ) : (
        <div className="space-y-2">
          {skills.map((skill) => (
            <div
              key={skill.id}
              className="flex items-start gap-3 border rounded-lg p-4 hover:bg-accent/30 transition-colors"
            >
              <div className="flex-1 min-w-0">
                <div className="font-medium">{skill.name}</div>
                <p className="text-sm text-muted-foreground mt-0.5 line-clamp-2 whitespace-pre-wrap">
                  {skill.content}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <Button variant="ghost" size="sm" onClick={() => openEdit(skill)}>
                  Edit
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-destructive hover:text-destructive"
                  onClick={() => {
                    if (confirm(`Delete skill "${skill.name}"?`)) deleteMut.mutate(skill.id);
                  }}
                >
                  Delete
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <Dialog open={!!editing} onOpenChange={(open: boolean) => !open && closeDialog()}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editing === "new" ? "New Skill" : `Edit — ${(editing as Skill)?.name}`}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1">
              <Label>Name *</Label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Python Style Guide"
              />
            </div>
            <div className="space-y-1">
              <Label>Content *</Label>
              <Textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder="Write your knowledge/instruction document here. Markdown supported."
                rows={10}
                className="font-mono text-sm"
              />
              <p className="text-xs text-muted-foreground">
                This text is prepended to the agent's system prompt when this skill is assigned.
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                className="flex-1"
                onClick={handleSave}
                disabled={!name.trim() || !content.trim() || submitting}
              >
                {submitting ? "Saving…" : editing === "new" ? "Create Skill" : "Update Skill"}
              </Button>
              <Button variant="outline" onClick={closeDialog}>
                Cancel
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
