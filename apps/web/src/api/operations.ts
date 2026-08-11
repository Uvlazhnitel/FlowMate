import { apiRequest } from "./client";

export interface PersonRef {
  0: string;
  1: string;
}

export interface ReminderCard {
  id: string;
  effective_at: string;
  revision: number;
}

export type PlannerStatus =
  | "not_required"
  | "needs_transfer"
  | "transferred"
  | "update_required"
  | "no_longer_relevant";

export interface WorkItemCardData {
  id: string;
  type: string;
  status: string;
  title: string;
  description: string | null;
  priority: string;
  planner_status: PlannerStatus;
  topic_id: string | null;
  topic_name: string | null;
  people: PersonRef[];
  due_at: string | null;
  next_follow_up_at: string | null;
  waiting_since: string | null;
  completed_at: string | null;
  updated_at: string;
  effective_at: string | null;
  overdue: boolean;
  revision: number;
  reminder: ReminderCard | null;
}

export interface PageResponse<T> {
  items: T[];
  limit: number;
  offset: number;
  has_more: boolean;
  timezone?: string;
}

export interface ActivityEntry {
  id: string;
  work_item_id: string;
  title: string;
  event_type: string;
  created_at: string;
}

export interface TodayOverviewResponse {
  timezone: string;
  summary: {
    overdue: number;
    due_today: number;
    follow_ups: number;
    waiting_overdue: number;
    questions: number;
    inbox: number;
    planner_queue: number;
  };
  focus: WorkItemCardData[];
  later_today: {
    items: WorkItemCardData[];
    has_more: boolean;
  };
}

export interface OverviewWorkItem {
  item: WorkItemCardData;
  needs_inbox: boolean;
}

export interface OverviewInboxItem {
  id: string;
  kind: "draft" | "work_item" | "note";
  title: string;
  excerpt: string;
  status: string;
  reasons: string[];
  occurred_at: string;
  item_count: number;
}

export interface OverviewColumn<T> {
  items: T[];
  total: number;
  has_more: boolean;
}

export interface OverviewResponse {
  timezone: string;
  today: OverviewColumn<OverviewWorkItem>;
  tomorrow: OverviewColumn<OverviewWorkItem>;
  inbox: OverviewColumn<OverviewInboxItem>;
}

export interface TopicSummary {
  id: string;
  name: string;
  description: string | null;
  open_count: number;
  overdue_count: number;
  follow_up_count: number;
  waiting_count: number;
  next_deadline: string | null;
}

export interface PersonSummary {
  id: string;
  display_name: string;
  role: string | null;
  open_item_count: number;
  follow_up_count: number;
  waiting_count: number;
  question_count: number;
  last_activity: string;
}

export type PeopleScope = "work" | "recent" | "all";

export interface NamedEntry {
  id: string;
  name: string;
  subtitle: string | null;
}

export interface NoteEntry {
  id: string;
  content: string;
  created_at: string;
}

export interface AgendaEntry {
  group_kind: "person" | "topic" | "unassigned";
  group_id: string | null;
  group_label: string;
  item: WorkItemCardData;
}

export type WorkItemAction =
  | "complete"
  | "reopen"
  | "cancel"
  | "reschedule"
  | "reschedule_preset"
  | "reschedule_text"
  | "snooze"
  | "add_note"
  | "waiting_received"
  | "agenda_discussed"
  | "question_answered"
  | "defer"
  | "convert_to_task"
  | "add_result"
  | "add_decision"
  | "archive"
  | "edit"
  | "planner_transferred"
  | "planner_not_required"
  | "planner_update_required"
  | "planner_needs_transfer";

export type ReschedulePreset =
  "later_today" | "tomorrow_morning" | "next_working_day" | "next_week";

export type RescheduleSelection =
  | {
      action: "reschedule";
      local_date: string;
      local_time: string;
    }
  | {
      action: "reschedule_preset";
      preset: ReschedulePreset;
    }
  | {
      action: "reschedule_text";
      phrase: string;
    };

export interface ActionPayload {
  action: WorkItemAction;
  client_action_id: string;
  expected_revision: number;
  content?: string;
  preset?: ReschedulePreset;
  phrase?: string;
  local_date?: string;
  local_time?: string;
  duration_minutes?: number;
  reminder_id?: string;
  reminder_revision?: number;
  title?: string;
  description?: string | null;
  item_type?: string;
  priority?: string;
  topic_id?: string | null;
  person_ids?: string[];
  date_changed?: boolean;
}

export interface ActionResponse {
  changed: boolean;
  work_item?: WorkItemCardData;
  reminder_id?: string;
  decision_id?: string;
}

export const operationsKeys = {
  all: ["operations"] as const,
  overview: ["operations", "overview"] as const,
  todayOverview: ["operations", "today", "overview"] as const,
  tomorrow: ["operations", "tomorrow"] as const,
};

function query(path: string, values: Record<string, string | number | undefined>) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  return `${path}?${params.toString()}`;
}

export const getTodayOverview = () =>
  apiRequest<TodayOverviewResponse>("/api/v1/today/overview");

export const getOverview = () => apiRequest<OverviewResponse>("/api/v1/overview");

export const getToday = (section: string, offset = 0) =>
  apiRequest<PageResponse<WorkItemCardData>>(
    query("/api/v1/today", { section, limit: 20, offset }),
  );

export const getTomorrow = (offset = 0) =>
  apiRequest<PageResponse<WorkItemCardData>>(
    query("/api/v1/tomorrow", { limit: 20, offset }),
  );

export const getTopics = (q: string, offset: number) =>
  apiRequest<PageResponse<TopicSummary>>(query("/api/v1/topics", { q, limit: 20, offset }));

export const getTopic = (id: string) =>
  apiRequest<{ id: string; name: string; description: string | null }>(
    `/api/v1/topics/${id}`,
  );

export const getTopicContent = <T>(id: string, section: string, offset: number) =>
  apiRequest<PageResponse<T>>(
    query(`/api/v1/topics/${id}/content`, { section, limit: 20, offset }),
  );

export const getPeople = (q: string, offset: number, scope: PeopleScope = "work") =>
  apiRequest<PageResponse<PersonSummary>>(
    query("/api/v1/people", { q, scope, limit: 20, offset }),
  );

export const getPerson = (id: string) =>
  apiRequest<{
    id: string;
    display_name: string;
    role: string | null;
    notes: string | null;
  }>(`/api/v1/people/${id}`);

export const getPersonContent = <T>(id: string, section: string, offset: number) =>
  apiRequest<PageResponse<T>>(
    query(`/api/v1/people/${id}/content`, { section, limit: 20, offset }),
  );

export const getAgenda = (groupKind: string, offset: number) =>
  apiRequest<PageResponse<AgendaEntry>>(
    query("/api/v1/agenda", {
      group_kind: groupKind || undefined,
      limit: 40,
      offset,
    }),
  );

export const runWorkItemAction = (id: string, payload: ActionPayload) =>
  apiRequest<ActionResponse>(`/api/v1/work-items/${id}/actions`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
