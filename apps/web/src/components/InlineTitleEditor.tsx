import { useEffect, useRef, useState } from "react";

export function InlineTitleEditor({
  value,
  pending = false,
  onSave,
  onEmpty,
  className,
}: {
  value: string;
  pending?: boolean;
  onSave: (value: string) => void;
  onEmpty?: () => void;
  className?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const inputRef = useRef<HTMLInputElement>(null);
  const original = useRef(value);

  useEffect(() => {
    if (!editing) {
      original.current = value;
    }
  }, [editing, value]);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  function cancel() {
    setDraft(original.current);
    setEditing(false);
  }

  function submit() {
    if (pending) return;
    const next = draft.trim();
    if (!next) {
      onEmpty?.();
      return;
    }
    if (next !== original.current.trim()) onSave(next);
    setEditing(false);
  }

  if (!editing) {
    return (
      <button
        type="button"
        className={className ?? "inline-title"}
        onClick={() => {
          original.current = value;
          setDraft(value);
          setEditing(true);
        }}
        aria-label={`Редактировать: ${value}`}
      >
        {value}
      </button>
    );
  }

  return (
    <input
      ref={inputRef}
      className="inline-title__input"
      value={draft}
      maxLength={10_000}
      disabled={pending}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={submit}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          submit();
        } else if (event.key === "Escape") {
          event.preventDefault();
          cancel();
        }
      }}
    />
  );
}
