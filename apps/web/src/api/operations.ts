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

export interface WorkItemCardData {
  id: string;
  type: string;
  status: string;
  title: string;
  description: string | null;
  priority: string;
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

export interface DashboardResponse {
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
  recommended: WorkItemCardData[];
  activity: ActivityEntry[];
  deadlines: WorkItemCardData[];
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
  follow_up_count: number;
  waiting_count: number;
  question_count: number;
  last_activity: string;
}

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

export interface ActionPayload {
  action: WorkItemAction;
  client_action_id: string;
  expected_revision: number;
  content?: string;
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
  dashboard: ["operations", "dashboard"] as const,
};

function query(path: string, values: Record<string, string | number | undefined>) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  return `${path}?${params.toString()}`;
}

export const getDashboard = () => apiRequest<DashboardResponse>("/api/v1/dashboard");

export const getToday = (section: string, offset = 0) =>
  apiRequest<PageResponse<WorkItemCardData>>(
    query("/api/v1/today", { section, limit: 20, offset }),
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

export const getPeople = (q: string, offset: number) =>
  apiRequest<PageResponse<PersonSummary>>(
    query("/api/v1/people", { q, limit: 20, offset }),
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
