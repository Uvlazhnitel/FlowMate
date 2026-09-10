import { BriefcaseBusiness, House } from "lucide-react";

export function WorkspaceBadge({ workspace }: { workspace: "work" | "personal" }) {
  const Icon = workspace === "work" ? BriefcaseBusiness : House;
  const label = workspace === "work" ? "Работа" : "Личное";
  return (
    <span className={`workspace-badge workspace-badge--${workspace}`}>
      <Icon size={12} aria-hidden />
      {label}
    </span>
  );
}
