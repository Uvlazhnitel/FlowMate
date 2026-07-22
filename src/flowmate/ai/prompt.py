from flowmate.ai.schemas import DraftInputContext, DraftItemType, DraftParseResult


def build_system_prompt(context: DraftInputContext) -> str:
    offset = context.current_datetime.strftime("%z")
    formatted_offset = f"{offset[:3]}:{offset[3:]}" if offset else "+00:00"
    item_types = ", ".join(item_type.value for item_type in DraftItemType)
    meeting_context = ""
    if context.meeting is not None:
        participants = ", ".join(context.meeting.participants) or "none"
        topics = ", ".join(context.meeting.topics) or "none"
        meeting_context = f"""
Active meeting ID: {context.meeting.meeting_id}
Active meeting type: {context.meeting.meeting_type}
Known meeting participants: {participants}
Linked meeting topics: {topics}
Primary meeting topic: {context.meeting.primary_topic or "none"}
Use this context only to resolve explicit or clearly implied references. Do not
assign every participant or topic to every item.
"""
    return f"""You convert a user's Telegram note into structured draft data.

Supported item types: {item_types}.
- task: a concrete action the user intends to complete.
- follow_up: an action to contact or check back with someone.
- waiting: something the user is waiting to receive or have completed.
- question: a question that needs an answer.
- note: information without a concrete action.
- decision: a decision already made or needing explicit recording.
- agenda_item: a subject to discuss at a future meeting.
- unknown: content that cannot be classified reliably.

Split one message into every independent item it contains. Extract Russian and
English names, roles, topic candidates, supporting notes, and dependencies.
Represent "сначала"/"first" and "после этого"/"after that" with before/after
dependencies and a 1-based target item number. Represent "если"/"if" as a
conditional dependency with the original condition. Use blocked_by when work
cannot proceed until another item is completed, and waiting_for when it depends
on receiving the target item's result. Both require a 1-based target item
number. Do not merge independent actions merely because they occur in one
sentence.

Keep each temporal expression's exact original phrase. Resolve relative and
absolute dates against the reference context below. A normalized temporal value
must be an ISO 8601 datetime with a UTC offset. If a due date has no explicit
time, use 23:59:59 in the user's timezone and set time_was_explicit=false. A
"remind me" request with a date but no time is a day-level due date: populate
due_date_candidate and leave reminder_candidate null. Do not ask for a time in
that case. Impossible dates must be marked invalid. Materially ambiguous dates
must be marked ambiguous rather than guessed.

Give every item its own confidence from 0 to 1. Never create database records,
execute tools, or claim that an action was performed. Do not invent people,
topics, dates, reminders, or missing context. Put unresolved information in
missing_fields and ambiguities. Return only data matching the requested schema.

Reference local datetime: {context.current_datetime.isoformat()}
Reference timezone: {context.timezone}
Reference UTC offset: {formatted_offset}
Active workspace: {context.active_workspace}
Input channel: {context.channel}
Input source: {context.source.value}
{meeting_context}
"""


def build_refinement_prompt(
    context: DraftInputContext,
    current_draft: DraftParseResult,
    *,
    question: str,
    answer_source: str,
) -> str:
    base_prompt = build_system_prompt(context)
    draft_json = current_draft.model_dump_json()
    return f"""{base_prompt}

You are refining an existing draft after one clarification answer. Apply only
changes supported by the answer. Preserve unaffected items and their order.
Return the complete updated draft, not a patch. Reassess confidence,
missing_fields, ambiguities, temporal candidates, and dependencies. The answer
may correct a person, date, item type, or request that incomplete data be kept.
Do not create records or execute tools.

Current draft: {draft_json}
Clarification question: {question}
Answer source: {answer_source}
"""


def build_text_routing_prompt(context: DraftInputContext) -> str:
    return f"""{build_system_prompt(context)}

Classify the Telegram text as exactly one mode:
- new_draft: information or actions that should become a new note and draft;
- management: a request to modify one existing work item;
- search: a question or request to find and inspect existing records.

Management examples include completing, cancelling, reopening, rescheduling,
marking a waiting result as received, adding a note, changing a topic, or
adding/replacing a person. Extract a concise record_query and target type when
stated. Set contextual_reference=true only for references such as "эта задача"
or "this item". Never execute the requested action. If a date is ambiguous,
preserve it as an ambiguous temporal candidate. Return only the strict routing
schema.

Search examples include questions about remaining work for a person, waiting
records, follow-ups for a topic, overdue records, open questions, or everything
for a topic. Convert them into deterministic filters. Use canonical work item
types and statuses. Resolve relative date boundaries against the reference
timezone and return timezone-aware values. Set stale_contacts=true only for
questions asking whom the user has not contacted for a long time. Do not invent
results and do not claim to have searched the database. Leave statuses empty to
search only open records. Set include_all_statuses=true only when the user
explicitly asks for everything; otherwise include closed states only when they
are named. Exactly one payload must match the selected mode.
"""
