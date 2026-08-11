import { FilePenLine } from "lucide-react";

import type {
  WorkItemInboxEntry,
  SettingsPerson,
  SettingsTopic,
} from "../../api/remaining";
import { WorkItemCard } from "../../components/WorkItemCard";
import type { DateTimePreferences } from "../../lib/dates";
import { WorkItemEditor } from "./WorkItemEditor";

export function WorkItemInboxCard({
  entry,
  dateTimePreferences,
  timezone,
  topics,
  people,
  onSaved,
}: {
  entry: WorkItemInboxEntry;
  dateTimePreferences: DateTimePreferences;
  timezone: string;
  topics: SettingsTopic[];
  people: SettingsPerson[];
  onSaved: () => void;
}) {
  return (
    <>
      <WorkItemCard item={entry.item} dateTimePreferences={dateTimePreferences} />
      <details className="edit-panel">
        <summary>
          <FilePenLine size={16} /> Уточнить поля
        </summary>
        <WorkItemEditor
          item={entry.item}
          timezone={timezone}
          topics={topics}
          people={people}
          onSaved={onSaved}
        />
      </details>
    </>
  );
}
