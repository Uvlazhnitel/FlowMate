import { apiRequest } from "./client";
import type { PageResponse, WorkItemCardData } from "./operations";

export type InboxKind = "draft" | "work_item" | "note" | "meeting_review";
export type PlannerStatus =
  | "not_required"
  | "needs_transfer"
  | "transferred"
  | "update_required"
  | "no_longer_relevant";

export interface EntityRef {
  id: string;
  name?: string;
  display_name?: string;
}

export interface DraftItemData {
  id: string;
  position: number;
  type: string;
  title: string;
  description: string | null;
  priority: string;
  confidence: number;
  readiness: string;
  missing_fields: string[];
  ambiguities: string[];
  due_at: string | null;
  topic: EntityRef | null;
  people: EntityRef[];
}

export interface DraftInboxEntry {
  id: string;
  kind: "draft";
  status: string;
  revision: number;
  reasons: string[];
  recoverable: boolean;
  source_excerpt: string;
  created_at: string;
  updated_at: string;
  expires_at: string;
  items: DraftItemData[];
}

export interface WorkItemInboxEntry {
  kind: "work_item";
  reasons: string[];
  item: WorkItemCardData;
}

export interface NoteInboxEntry {
  id: string;
  kind: "note";
  reasons: string[];
  excerpt: string;
  source: string;
  created_at: string;
}

export interface MeetingReviewInboxEntry {
  id: string;
  kind: "meeting_review";
  reasons: string[];
  meeting_id: string;
  meeting_title: string;
  review_id: string;
  category: string;
  title: string;
  created_at: string;
}

export type InboxEntry =
  DraftInboxEntry | WorkItemInboxEntry | NoteInboxEntry | MeetingReviewInboxEntry;

export interface PlannerEntry {
  item: WorkItemCardData;
  planner_status: PlannerStatus;
  transferred_at: string | null;
}

export interface TimelineEntry {
  id: string;
  event_type: string;
  occurred_at: string;
  work_item_id: string;
  title: string;
  work_item_type: string;
  status: string;
  topic: EntityRef | null;
  people: EntityRef[];
}

export interface PreferencesData {
  timezone: string;
  morning_digest_enabled: boolean;
  morning_digest_time: string;
  evening_digest_enabled: boolean;
  evening_digest_time: string;
  quiet_hours_enabled: boolean;
  quiet_hours_start: string;
  quiet_hours_end: string;
  default_snooze_minutes: number;
  send_empty_digests: boolean;
  date_display_format: "day_month_year" | "year_month_day";
  time_display_format: "24h" | "12h";
}

export interface SettingsResponse {
  preferences: PreferencesData;
  providers: { ai_configured: boolean; speech_configured: boolean };
}

export interface SettingsTopic {
  id: string;
  name: string;
  description: string | null;
  aliases: string[];
  is_active: boolean;
}

export interface SettingsPerson {
  id: string;
  display_name: string;
  role: string | null;
  notes: string | null;
  aliases: string[];
  is_active: boolean;
}

function query(path: string, values: Record<string, string | number | undefined>) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const suffix = params.toString();
  return suffix ? `${path}?${suffix}` : path;
}

export const remainingKeys = { all: ["remaining"] as const };

export const getInbox = (kind: string, reason: string, offset: number) =>
  apiRequest<PageResponse<InboxEntry>>(
    query("/api/v1/inbox", { kind, reason, limit: 20, offset }),
  );

export const updateDraftItem = (
  draftId: string,
  itemId: string,
  payload: Record<string, unknown>,
) =>
  apiRequest<DraftInboxEntry>(`/api/v1/inbox/drafts/${draftId}/items/${itemId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });

export const runDraftAction = (
  draftId: string,
  action: string,
  expectedRevision: number,
  acceptUncertainty = false,
) =>
  apiRequest<Record<string, unknown>>(`/api/v1/inbox/drafts/${draftId}/actions`, {
    method: "POST",
    body: JSON.stringify({
      action,
      expected_revision: expectedRevision,
      accept_uncertainty: acceptUncertainty,
    }),
  });

export const runNoteAction = (noteId: string, action: "keep" | "archive") =>
  apiRequest<Record<string, unknown>>(`/api/v1/inbox/notes/${noteId}/actions`, {
    method: "POST",
    body: JSON.stringify({ action }),
  });

export const runBulkInboxAction = (action: string, entries: unknown[]) =>
  apiRequest<{ processed: number }>("/api/v1/inbox/bulk-actions", {
    method: "POST",
    body: JSON.stringify({ action, entries }),
  });

export const getPlannerQueue = (status: string, q: string, offset: number) =>
  apiRequest<PageResponse<PlannerEntry>>(
    query("/api/v1/planner-queue", { status, q, limit: 20, offset }),
  );

export const getTimeline = (filters: URLSearchParams, offset: number) => {
  const params = new URLSearchParams(filters);
  params.set("limit", "30");
  params.set("offset", String(offset));
  return apiRequest<PageResponse<TimelineEntry> & { timezone: string }>(
    `/api/v1/timeline?${params.toString()}`,
  );
};

export const getSettings = () => apiRequest<SettingsResponse>("/api/v1/settings");

export const updatePreferences = (preferences: PreferencesData) =>
  apiRequest<SettingsResponse>("/api/v1/settings/preferences", {
    method: "PUT",
    body: JSON.stringify(preferences),
  });

export const getSettingsTopics = (offset = 0) =>
  apiRequest<PageResponse<SettingsTopic>>(
    `/api/v1/settings/topics?limit=25&offset=${offset}`,
  );

export const getSettingsPeople = (offset = 0) =>
  apiRequest<PageResponse<SettingsPerson>>(
    `/api/v1/settings/people?limit=25&offset=${offset}`,
  );

export const createTopic = (payload: Omit<SettingsTopic, "id">) =>
  apiRequest<SettingsTopic>("/api/v1/topics", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateTopic = (id: string, payload: Omit<SettingsTopic, "id">) =>
  apiRequest<SettingsTopic>(`/api/v1/settings/topics/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });

export const createPerson = (payload: Omit<SettingsPerson, "id">) =>
  apiRequest<SettingsPerson>("/api/v1/people", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updatePerson = (id: string, payload: Omit<SettingsPerson, "id">) =>
  apiRequest<SettingsPerson>(`/api/v1/settings/people/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
