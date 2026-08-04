from flowmate.ai.schemas import DraftInputContext, DraftItemType, DraftParseResult

SINGLE_GOAL_EXAMPLE = "Подготовить отчёт и отправить его клиенту"  # noqa: RUF001
INDEPENDENT_OUTCOMES_EXAMPLE = "Купить молоко и забрать посылку"


def build_reference_context(context: DraftInputContext) -> str:
    offset = context.current_datetime.strftime("%z")
    formatted_offset = f"{offset[:3]}:{offset[3:]}" if offset else "+00:00"
    return f"""Reference local datetime: {context.current_datetime.isoformat()}
Reference timezone: {context.timezone}
Reference UTC offset: {formatted_offset}
Active workspace: {context.active_workspace}
Input channel: {context.channel}
Input source: {context.source.value}"""


def build_system_prompt(context: DraftInputContext) -> str:
    item_types = ", ".join(item_type.value for item_type in DraftItemType)
    return f"""You convert a user's Telegram note into structured draft data.

Supported item types: {item_types}.
- task: a concrete action the user intends to complete.
- follow_up: a planned contact, repeated contact, or check for a response/status.
- waiting: something the user is waiting to receive or have completed.
- question: a question that needs an answer.
- note: information without a concrete action.
- decision: a decision already made or needing explicit recording.
- agenda_item: a subject to discuss at a future meeting.
- unknown: content that cannot be classified reliably.

Use follow_up for explicit phrases such as "фоллоу-ап", "проверить статус",
"позвонить", "перезвонить", "связаться повторно", and
"check back". A first-class deliverable remains task even when it is sent to or
prepared for a person: preparing a report for someone is a task. Use waiting
when the request has already been sent and the user is now expecting a result.
Do not classify "напомнить мне сделать..." as follow_up merely because it asks
the assistant for a reminder.

An item is an independently completable outcome, not every verb. Prefer one
item when actions are steps toward the same result, operate on the same
deliverable, or one action prepares, sends, checks, approves, or completes the
other. For example, "{SINGLE_GOAL_EXAMPLE}" is one task.
Split only explicit lists, separate imperative sentences with separate results,
or genuinely independent outcomes. For example,
"{INDEPENDENT_OUTCOMES_EXAMPLE}" is two tasks. When uncertain, return one item.

Set itemization_decision, itemization_basis, and itemization_confidence for this
choice. A multiple result must also include consolidated_item containing a safe
single-item interpretation of the complete message. A single result must set
consolidated_item=null. Prefixes "одна задача", "одним пунктом", and "не
разделяй" force one item. Prefixes "две задачи" and "несколько задач" request
multiple items; do not include these control words in titles.

Extract Russian and English names, roles, topic candidates, supporting notes,
and dependencies.
Represent "сначала"/"first" and "после этого"/"after that" with before/after
dependencies and a 1-based target item number. Represent "если"/"if" as a
conditional dependency with the original condition. Use blocked_by when work
cannot proceed until another item is completed, and waiting_for when it depends
on receiving the target item's result. Both require a 1-based target item
number. Phrases such as "как только получу", "после того как получу", and
"once I receive" describe an external condition, not another draft item. Keep
one task, use a conditional dependency with target_item_number=null, and retain
the complete condition.

Keep each temporal expression's exact original phrase. Resolve relative and
absolute dates against the reference context below. A normalized temporal value
must be an ISO 8601 datetime with a UTC offset. If a due date has no explicit
time, use 23:59:59 in the user's timezone and set time_was_explicit=false. For
"remind me" with a date but no time, keep reminder_candidate and set
time_was_explicit=false; the backend applies the user's default reminder time.
Impossible dates must be marked invalid. Materially ambiguous dates must be
marked ambiguous rather than guessed.

Give every item its own confidence from 0 to 1. Never create database records,
execute tools, or claim that an action was performed. Do not invent people,
topics, dates, reminders, or missing context. Put unresolved information in
missing_fields and ambiguities only when it prevents reliable interpretation.
Amounts, descriptions, topics, people, dates, and times are optional unless the
user explicitly made them essential to the requested action. In particular, do
not request an amount for a meaningful payment task that did not state one.
Return only data matching the requested schema. Classify the intended workspace
as work or personal and provide confidence. Client, colleague, project and
business actions normally mean work; home, family, health and household actions
normally mean personal. Leave the candidate empty when the distinction is
unclear.

{build_reference_context(context)}
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
Preserve the current item count and itemization metadata unless the answer
explicitly asks to split or merge items.
Do not introduce missing optional amounts, descriptions, topics, people, dates,
or times merely because the user did not provide them.
Do not create records or execute tools.

Current draft: {draft_json}
Clarification question: {question}
Answer source: {answer_source}
"""


def build_text_routing_prompt(context: DraftInputContext) -> str:
    return f"""Classify Telegram text as exactly one mode. Return only the strict
routing schema and never execute tools or database actions.

- new_draft: information or actions that should become a new note and draft;
- management: a request to modify one existing work item;
- search: a question or request to find and inspect existing records.

Words meaning add, create, record, remind, or need to do ("добавить",
"создать", "записать", "напомнить", "нужно сделать") default to new_draft.
Choose management only when the user explicitly refers to an existing record
or asks to change its current state.

Management includes completing, cancelling, reopening, rescheduling, changing
a title or description, marking a waiting result as received, adding a note,
changing a topic, or adding/replacing a person. Extract a concise record_query
and target type when stated. For title/description changes, put only the new
value in replacement_text; set clear_description=true only when removal is
explicit. Set contextual_reference=true only for references such as "эта
задача" or "this item". A future instruction such as "нужно завтра выполнить
эту задачу" means reschedule, not reopen. Use reopen only for explicit requests
such as "верни задачу" or "переоткрой". Never execute the requested action. If
a date is ambiguous, preserve it as an ambiguous temporal candidate. Return
only the strict routing schema.

Search includes questions about remaining work for a person, waiting
records, follow-ups for a topic, overdue records, open questions, or everything
for a topic. Convert them into deterministic filters. Use canonical work item
types and statuses. Resolve relative date boundaries against the reference
timezone and return timezone-aware values. Set stale_contacts=true only for
questions asking whom the user has not contacted for a long time. Do not invent
results and do not claim to have searched the database. Leave statuses empty to
search only open records. Set include_all_statuses=true only when the user
explicitly asks for everything; otherwise include closed states only when they
are named. Exactly one payload must match the selected mode.

For new_draft, treat an item as an independently completable outcome rather than
every verb. Keep preparation, sending, checking, approval, and completion steps
for one deliverable together. Split only explicit lists, separate sentences
with separate results, or genuinely independent outcomes. When uncertain,
return one item. "{SINGLE_GOAL_EXAMPLE}" is one task;
"{INDEPENDENT_OUTCOMES_EXAMPLE}" is two. Return itemization decision, basis,
confidence, and a consolidated fallback for every multiple result. Respect
"одна задача"/"одним пунктом"/"не разделяй" and
"две задачи"/"несколько задач" prefixes.
Classify an explicit planned contact, repeated contact, call, or response/status
check as follow_up. A deliverable prepared for another person remains task, and
"напомнить мне сделать..." is not follow_up merely because it requests a
reminder. Use waiting only when a request has already been made and its result
is now expected.
Treat "как только получу", "после того как получу", and equivalent clauses as
an external condition of one task, not as a second waiting item.
Preserve exact temporal phrases.
Date-only due values use 23:59:59. Date-only reminder values remain reminder
candidates with time_was_explicit=false. Never invent people, topics,
dates, database results, or completed actions. Optional amounts, descriptions,
topics, people, dates, and times must not be reported as blocking missing data.

{build_reference_context(context)}
"""
