import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { createPersona, updatePersona, deletePersona } from "@/api/personas";
import { useAuth } from "@/hooks/useAuth";
import type { Persona } from "@/types";

interface Props {
  open: boolean;
  // initial = null/undefined → create from scratch
  // initial with owner_id != null → edit in place
  // initial with owner_id === null → copy a global as a new owned persona
  initial?: Persona | null;
  onClose: () => void;
  // Called after successful create/update with the saved persona.
  onSaved: (persona: Persona) => void;
  // Called after successful delete with the deleted persona's id.
  // Caller is responsible for clearing any selection that referenced it.
  onDeleted?: (deletedId: string) => void;
}

export default function PersonaPopup({ open, initial, onClose, onSaved, onDeleted }: Props) {
  const { token } = useAuth();
  const qc = useQueryClient();

  const isCopyOfGlobal = !!initial && initial.owner_id === null;
  const isEdit = !!initial && initial.owner_id !== null;
  // Anything that isn't "edit in place" is a new persona — that's the create path.

  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState("");

  // Reset fields whenever the popup opens or initial changes
  useEffect(() => {
    if (!open) return;
    setName(isCopyOfGlobal ? `${initial!.name} (copy)` : initial?.name ?? "");
    setPrompt(initial?.system_prompt ?? "");
    setError("");
  }, [open, initial, isCopyOfGlobal]);

  const saveMut = useMutation({
    mutationFn: async () => {
      const payload = { name: name.trim(), system_prompt: prompt.trim() };
      return isEdit
        ? updatePersona(token!, initial!.id, payload)
        : createPersona(token!, payload);
    },
    onSuccess: async (p) => {
      await qc.invalidateQueries({ queryKey: ["personas"] });
      onSaved(p);
      onClose();
    },
    onError: (e) => setError(e instanceof Error ? e.message : "Save failed"),
  });

  const deleteMut = useMutation({
    mutationFn: async () => {
      if (!isEdit) throw new Error("Cannot delete this persona");
      await deletePersona(token!, initial!.id);
      return initial!.id;
    },
    onSuccess: async (id) => {
      await qc.invalidateQueries({ queryKey: ["personas"] });
      onDeleted?.(id);
      onClose();
    },
    onError: (e) => setError(e instanceof Error ? e.message : "Delete failed"),
  });

  const busy = saveMut.isPending || deleteMut.isPending;
  const canSave = name.trim().length > 0 && prompt.trim().length > 0 && !busy;

  const headerLabel = isEdit ? "Edit persona" : isCopyOfGlobal ? "Copy persona" : "New persona";
  const saveLabel = saveMut.isPending
    ? "Saving…"
    : isEdit
      ? "Save changes"
      : isCopyOfGlobal
        ? "Save as new"
        : "Create & use";

  function confirmDelete() {
    if (!isEdit) return;
    if (!confirm(`Delete persona "${initial!.name}"? This cannot be undone.`)) return;
    deleteMut.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && !busy && onClose()}>
      <DialogContent className="max-w-3xl p-0 overflow-hidden">
        <div className="px-6 pt-5 pb-4 border-b">
          <p className="font-semibold text-base text-gray-900">{headerLabel}</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            {isCopyOfGlobal
              ? "Defaults can't be edited. This makes an owned copy you can change."
              : "A persona is a named system prompt. Use it on any agent — main or sub."}
          </p>
        </div>

        <div className="px-6 py-5 space-y-4">
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold text-gray-700">Name</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Research Lead"
              className="focus-visible:ring-violet-300"
              autoFocus
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs font-semibold text-gray-700">System prompt</Label>
            <Textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="You are…"
              rows={22}
              className="text-sm font-mono leading-relaxed resize-y focus-visible:ring-violet-300 min-h-[24rem]"
            />
          </div>

          {error && (
            <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg p-2">
              {error}
            </p>
          )}

          <div className="flex gap-2 pt-1">
            {isEdit && (
              <Button
                variant="ghost"
                size="sm"
                onClick={confirmDelete}
                disabled={busy}
                className="text-destructive hover:text-destructive hover:bg-red-50"
              >
                {deleteMut.isPending ? "Deleting…" : "Delete"}
              </Button>
            )}
            <div className="flex-1" />
            <Button variant="outline" size="sm" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={!canSave}
              onClick={() => saveMut.mutate()}
              className="bg-violet-600 hover:bg-violet-700 text-white"
            >
              {saveLabel}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
