"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

/** A modal on the native `<dialog>` element (task 0.4).
 *
 *  `showModal()` gives what a hand-rolled overlay has to fake: the rest of the
 *  page becomes inert, focus moves in and stays in, Esc cancels, and the
 *  dialog sits in the top layer above everything. The app used
 *  `window.confirm` / `window.prompt` instead, which cannot be styled, block
 *  the whole tab, and some browsers let users suppress entirely. */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  wide = false,
  dismissOnBackdrop = true,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  /** Room for a longer body (a preview), which scrolls within the screen. */
  wide?: boolean;
  /** Close on a click outside the dialog. Off for anything holding typed
   *  input, which a stray click would otherwise throw away. */
  dismissOnBackdrop?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descId = useId();

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={description ? descId : undefined}
      // Esc and the close() above both fire "close"; one path out.
      onClose={onClose}
      onClick={(e) => {
        // A click on the backdrop lands on the <dialog> itself.
        if (dismissOnBackdrop && e.target === e.currentTarget) onClose();
      }}
      className={cn(
        "bg-card text-foreground border-border shadow-float backdrop:bg-scrim open:animate-dialog-in m-auto max-h-[calc(100dvh-2rem)] overflow-auto rounded-xl border p-0",
        wide ? "w-[min(40rem,calc(100vw-2rem))]" : "w-[min(28rem,calc(100vw-2rem))]",
      )}
    >
      <div className="space-y-3 p-6">
        <h2 id={titleId} className="text-h3 font-semibold">
          {title}
        </h2>
        {description && (
          <div id={descId} className="text-muted-foreground text-sm">
            {description}
          </div>
        )}
        {children}
      </div>
      {footer && <div className="flex flex-wrap justify-end gap-2 px-6 pb-6">{footer}</div>}
    </dialog>
  );
}

type ConfirmOptions = {
  title: string;
  description?: ReactNode;
  confirmLabel?: string;
  /** Styles the confirm button as destructive. */
  destructive?: boolean;
};
type PromptOptions = {
  title: string;
  description?: ReactNode;
  label: string;
  defaultValue?: string;
  confirmLabel?: string;
  placeholder?: string;
};
type Pending =
  | ({ kind: "confirm"; resolve: (v: boolean) => void } & ConfirmOptions)
  | ({ kind: "prompt"; resolve: (v: string | null) => void } & PromptOptions);

const Ctx = createContext<{
  confirm: (o: ConfirmOptions) => Promise<boolean>;
  prompt: (o: PromptOptions) => Promise<string | null>;
} | null>(null);

/** One dialog host for the app; `useConfirm` / `usePrompt` open it and
 *  resolve with the answer, so a call site reads like the native API it
 *  replaces: `if (!(await confirm({...}))) return;` */
export function DialogProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [value, setValue] = useState("");
  const inputId = useId();

  const confirm = useCallback(
    (o: ConfirmOptions) =>
      new Promise<boolean>((resolve) => setPending({ kind: "confirm", resolve, ...o })),
    [],
  );
  const prompt = useCallback(
    (o: PromptOptions) =>
      new Promise<string | null>((resolve) => {
        setValue(o.defaultValue ?? "");
        setPending({ kind: "prompt", resolve, ...o });
      }),
    [],
  );

  const settle = (ok: boolean) => {
    if (!pending) return;
    if (pending.kind === "confirm") pending.resolve(ok);
    else pending.resolve(ok ? value : null);
    setPending(null);
  };

  return (
    <Ctx.Provider value={{ confirm, prompt }}>
      {children}
      <Dialog
        open={pending !== null}
        onClose={() => settle(false)}
        dismissOnBackdrop={pending?.kind !== "prompt"}
        title={pending?.title ?? ""}
        description={pending?.description}
        footer={
          <>
            <Button variant="outline" onClick={() => settle(false)}>
              Cancel
            </Button>
            <Button
              // Enter in the prompt's field submits through the form below.
              type={pending?.kind === "prompt" ? "submit" : "button"}
              form={pending?.kind === "prompt" ? `${inputId}-form` : undefined}
              variant={
                pending?.kind === "confirm" && pending.destructive ? "destructive" : "default"
              }
              // No autofocus here: `showModal()` focuses the first control,
              // Cancel, so Enter on a destructive confirm does not destroy.
              onClick={pending?.kind === "confirm" ? () => settle(true) : undefined}
            >
              {pending?.confirmLabel || "Confirm"}
            </Button>
          </>
        }
      >
        {pending?.kind === "prompt" && (
          <form
            id={`${inputId}-form`}
            onSubmit={(e) => {
              e.preventDefault();
              settle(true);
            }}
            className="space-y-1.5"
          >
            <Label htmlFor={inputId}>{pending.label}</Label>
            <Input
              id={inputId}
              autoFocus
              value={value}
              placeholder={pending.placeholder}
              onChange={(e) => setValue(e.target.value)}
            />
          </form>
        )}
      </Dialog>
    </Ctx.Provider>
  );
}

function useDialogs() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useConfirm/usePrompt need a <DialogProvider>");
  return ctx;
}

export const useConfirm = () => useDialogs().confirm;
export const usePrompt = () => useDialogs().prompt;
