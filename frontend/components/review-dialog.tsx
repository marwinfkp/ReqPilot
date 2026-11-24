"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/lib/api";

/** Generic human-review dialog: collects a comment (optionally required) and runs `onSubmit`. */
export function ReviewDialog({ trigger, title, description, confirmLabel = "Confirm", commentRequired = false, children, onSubmit, disabled }: {
  trigger: React.ReactNode;
  title: string;
  description?: React.ReactNode;
  confirmLabel?: string;
  commentRequired?: boolean;
  children?: React.ReactNode;
  disabled?: boolean;
  onSubmit: (comment: string) => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      await onSubmit(comment);
      toast.success(`${title} recorded`);
      setOpen(false);
      setComment("");
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild disabled={disabled}>{trigger}</DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        {children}
        <div className="space-y-2">
          <Label htmlFor="review-comment">Comment {commentRequired ? "(required)" : "(optional)"}</Label>
          <Textarea id="review-comment" value={comment} onChange={(e) => setComment(e.target.value)} rows={3}
            placeholder="Reason, evidence or instructions for the team" />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
          <Button onClick={submit} disabled={busy || (commentRequired && !comment.trim())}>{busy ? "Saving…" : confirmLabel}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
