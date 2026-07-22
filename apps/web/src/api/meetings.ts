import { apiRequest } from "./client";
import type { PageResponse } from "./operations";
import type { DraftItemData } from "./remaining";

export type MeetingType =
  "lead" | "team" | "client_sync" | "steering" | "one_to_one" | "other";

export interface MeetingCardData {
  id: string;
  title: string;
  type: MeetingType;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  summary: string | null;
  primary_topic_id: string | null;
  participants: [string, string][];
  topics: [string, string][];
  captured_note_count: number;
  created_at: string;
  updated_at: string;
  revision: number;
  long_running: boolean;
}

export interface MeetingCaptureData {
  id: string;
  meeting_id: string;
  sequence: number;
  status: string;
  review_status: "pending" | "edited" | "removed";
  revision: number;
  source_type: "text" | "voice";
  source_text: string | null;
  source_redacted: boolean;
  context: {
    timezone: string;
    captured_at: string;
    meeting_type: MeetingType;
    participants: { id: string; name: string }[];
    topics: { id: string; name: string }[];
  };
  confidence: number | null;
  suggested_question: string | null;
  created_at: string;
  updated_at: string;
  items: DraftItemData[];
}

export interface MeetingReviewItemData {
  id: string;
  position: number;
  category: string;
  status: string;
  title: string;
  source_capture_id: string | null;
  source_draft_item_id: string | null;
  suggested_next_action: string | null;
  consequences: string[];
  clarification_question: string | null;
  planner_requested: boolean;
  result_work_item_id: string | null;
  result_note_id: string | null;
}

export interface MeetingReviewData {
  id: string;
  meeting_id: string;
  status: "processing" | "review_required" | "completed" | "failed";
  summary: string | null;
  suggested_next_actions: string[];
  counts: Record<string, number>;
  revision: number;
  last_error_code: string | null;
  items: MeetingReviewItemData[];
  agenda: {
    id: string;
    work_item_id: string;
    title: string;
    type: string;
    outcome: string;
    result: string | null;
  }[];
  results: {
    id: string;
    type: string;
    title: string;
    status: string;
    due_at: string | null;
    next_follow_up_at: string | null;
    planner_status: string;
    role: string;
  }[];
  timeline: {
    id: string;
    event_type: string;
    previous_status: string | null;
    new_status: string;
    created_at: string;
  }[];
}

export interface MeetingDetailData {
  meeting: MeetingCardData;
  review: MeetingReviewData | null;
}

export const meetingsKeys = {
  all: ["meetings"] as const,
  active: ["meetings", "active"] as const,
  recent: ["meetings", "recent"] as const,
  captures: (meetingId: string) => ["meetings", meetingId, "captures"] as const,
  detail: (meetingId: string) => ["meetings", meetingId, "detail"] as const,
};

export const getActiveMeeting = () =>
  apiRequest<{ meeting: MeetingCardData | null }>("/api/v1/meetings/active");

export const getRecentMeetings = (offset = 0) =>
  apiRequest<PageResponse<MeetingCardData>>(`/api/v1/meetings?limit=20&offset=${offset}`);

export const getMeetingCaptures = (meetingId: string, offset = 0) =>
  apiRequest<PageResponse<MeetingCaptureData>>(
    `/api/v1/meetings/${meetingId}/captures?limit=20&offset=${offset}`,
  );

export const getMeetingDetail = (meetingId: string) =>
  apiRequest<MeetingDetailData>(`/api/v1/meetings/${meetingId}`);

export const runMeetingReviewAction = (
  meetingId: string,
  review: MeetingReviewData,
  action: "retry" | "confirm_ready" | "complete_with_inbox",
) =>
  apiRequest<{ review: MeetingReviewData }>(
    `/api/v1/meetings/${meetingId}/review/actions`,
    {
      method: "POST",
      body: JSON.stringify({
        action,
        expected_revision: review.revision,
        client_action_id: crypto.randomUUID(),
      }),
    },
  );

export const runMeetingReviewItemAction = (
  meetingId: string,
  review: MeetingReviewData,
  itemId: string,
  action: "exclude" | "include" | "planner_on" | "planner_off" | "inbox",
) =>
  apiRequest<{ review: MeetingReviewData }>(
    `/api/v1/meetings/${meetingId}/review/items/${itemId}/actions`,
    {
      method: "POST",
      body: JSON.stringify({ action, expected_revision: review.revision }),
    },
  );

export const answerMeetingReviewItem = (
  meetingId: string,
  review: MeetingReviewData,
  itemId: string,
  answer: string,
) =>
  apiRequest<{ review: MeetingReviewData }>(
    `/api/v1/meetings/${meetingId}/review/items/${itemId}/clarification`,
    {
      method: "POST",
      body: JSON.stringify({ answer, expected_revision: review.revision }),
    },
  );

export const setMeetingAgendaOutcome = (
  meetingId: string,
  entryId: string,
  outcome: "discussed" | "answered" | "deferred" | "unresolved",
  result: string | null,
) =>
  apiRequest<{ review: MeetingReviewData }>(
    `/api/v1/meetings/${meetingId}/review/agenda/${entryId}`,
    { method: "POST", body: JSON.stringify({ outcome, result }) },
  );

export const addMeetingAgendaItem = (meetingId: string, title: string) =>
  apiRequest<{ review: MeetingReviewData }>(`/api/v1/meetings/${meetingId}/review/agenda`, {
    method: "POST",
    body: JSON.stringify({ title }),
  });

export const updateMeetingCaptureItem = (
  meetingId: string,
  captureId: string,
  itemId: string,
  payload: Record<string, unknown>,
) =>
  apiRequest<{ capture: MeetingCaptureData }>(
    `/api/v1/meetings/${meetingId}/captures/${captureId}/items/${itemId}`,
    { method: "PATCH", body: JSON.stringify(payload) },
  );

export const removeMeetingCapture = (capture: MeetingCaptureData) =>
  apiRequest<{ capture: MeetingCaptureData }>(
    `/api/v1/meetings/${capture.meeting_id}/captures/${capture.id}/actions`,
    {
      method: "POST",
      body: JSON.stringify({
        action: "remove",
        client_action_id: crypto.randomUUID(),
        expected_revision: capture.revision,
      }),
    },
  );

export const startMeeting = (payload: {
  client_action_id: string;
  type: MeetingType;
  title: string | null;
  participant_ids: string[];
  topic_ids: string[];
  primary_topic_id: string | null;
}) =>
  apiRequest<{ meeting: MeetingCardData }>("/api/v1/meetings", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const runMeetingAction = (
  meeting: MeetingCardData,
  action: "start" | "end" | "cancel",
) =>
  apiRequest<{ meeting: MeetingCardData }>(`/api/v1/meetings/${meeting.id}/actions`, {
    method: "POST",
    body: JSON.stringify({
      action,
      expected_revision: meeting.revision,
      client_action_id: crypto.randomUUID(),
    }),
  });
