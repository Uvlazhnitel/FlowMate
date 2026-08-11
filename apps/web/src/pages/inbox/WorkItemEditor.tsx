import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { runWorkItemAction, type WorkItemCardData } from "../../api/operations";
import { type SettingsPerson, type SettingsTopic } from "../../api/remaining";
import { itemTypeLabels, itemTypes, localParts, priorities } from "./presentation";

export function WorkItemEditor({
  item,
  timezone,
  topics,
  people,
  onSaved,
}: {
  item: WorkItemCardData;
  timezone: string;
  topics: SettingsTopic[];
  people: SettingsPerson[];
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(item.title);
  const [description, setDescription] = useState(item.description ?? "");
  const [type, setType] = useState(item.type);
  const [priority, setPriority] = useState(item.priority);
  const [topicId, setTopicId] = useState(item.topic_id ?? "");
  const [personIds, setPersonIds] = useState(item.people.map((person) => person[0]));
  const initialDate = localParts(item.effective_at, timezone);
  const [localDate, setLocalDate] = useState(initialDate.date);
  const [localTime, setLocalTime] = useState(initialDate.time);
  const mutation = useMutation({
    mutationFn: () =>
      runWorkItemAction(item.id, {
        action: "edit",
        client_action_id: crypto.randomUUID(),
        expected_revision: item.revision,
        title,
        description: description || null,
        item_type: type,
        priority,
        topic_id: topicId || null,
        person_ids: personIds,
        date_changed: true,
        local_date: localDate || undefined,
        local_time: localDate ? localTime : undefined,
      }),
    onSuccess: onSaved,
  });
  return (
    <form
      className="editor-grid"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate();
      }}
    >
      <label>
        Заголовок
        <input value={title} onChange={(event) => setTitle(event.target.value)} />
      </label>
      <label>
        Тип
        <select value={type} onChange={(event) => setType(event.target.value)}>
          {itemTypes
            .filter((value) => value !== "note")
            .map((value) => (
              <option value={value} key={value}>
                {itemTypeLabels[value] ?? value}
              </option>
            ))}
        </select>
      </label>
      <label>
        Приоритет
        <select value={priority} onChange={(event) => setPriority(event.target.value)}>
          {priorities.map((value) => (
            <option value={value} key={value}>
              {value}
            </option>
          ))}
        </select>
      </label>
      <label>
        Тема
        <select value={topicId} onChange={(event) => setTopicId(event.target.value)}>
          <option value="">Без темы</option>
          {topics
            .filter((topic) => topic.is_active)
            .map((topic) => (
              <option value={topic.id} key={topic.id}>
                {topic.name}
              </option>
            ))}
        </select>
      </label>
      <label>
        Люди
        <select
          multiple
          value={personIds}
          onChange={(event) =>
            setPersonIds([...event.target.selectedOptions].map((option) => option.value))
          }
        >
          {people
            .filter((person) => person.is_active)
            .map((person) => (
              <option value={person.id} key={person.id}>
                {person.display_name}
              </option>
            ))}
        </select>
      </label>
      <label>
        Дата
        <input
          type="date"
          value={localDate}
          onChange={(event) => setLocalDate(event.target.value)}
        />
      </label>
      <label>
        Время
        <input
          type="time"
          value={localTime}
          disabled={!localDate}
          onChange={(event) => setLocalTime(event.target.value)}
        />
      </label>
      <label className="editor-grid__wide">
        Описание
        <textarea
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>
      {mutation.isError && <p className="inline-error">Не удалось сохранить запись.</p>}
      <button className="button button--primary" disabled={mutation.isPending}>
        Сохранить
      </button>
    </form>
  );
}
