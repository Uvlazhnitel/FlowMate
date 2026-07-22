import { EmptyState } from "../components/PageState";

export interface PageDefinition {
  path: string;
  eyebrow: string;
  title: string;
  description: string;
  emptyTitle: string;
  emptyDescription: string;
}

// Route metadata is colocated with its renderer to keep foundation pages consistent.
// eslint-disable-next-line react-refresh/only-export-components
export const pageDefinitions: PageDefinition[] = [
  {
    path: "/dashboard",
    eyebrow: "Командный центр",
    title: "Обзор",
    description: "Спокойная точка входа в рабочий день.",
    emptyTitle: "Обзор готов к данным",
    emptyDescription:
      "В следующем этапе здесь появятся актуальные задачи, ожидания и follow-up.",
  },
  {
    path: "/today",
    eyebrow: "Фокус",
    title: "Сегодня",
    description: "То, что требует внимания именно сейчас.",
    emptyTitle: "На сегодня ничего не загружено",
    emptyDescription:
      "Подключение реального списка запланировано вместе с Task Engine API.",
  },
  {
    path: "/topics",
    eyebrow: "Контекст",
    title: "Темы",
    description: "Рабочие направления без потери деталей.",
    emptyTitle: "Темы пока не подключены",
    emptyDescription: "Справочник появится здесь без дублирования backend-логики.",
  },
  {
    path: "/people",
    eyebrow: "Связи",
    title: "Люди",
    description: "Кому написать, от кого ждём и о чём договорились.",
    emptyTitle: "Люди пока не загружены",
    emptyDescription: "Здесь появится персональный контекст из PostgreSQL.",
  },
  {
    path: "/agenda",
    eyebrow: "Разговоры",
    title: "Повестка",
    description: "Вопросы и решения к следующей встрече.",
    emptyTitle: "Повестка пуста",
    emptyDescription: "Эта страница станет представлением существующих agenda items.",
  },
  {
    path: "/inbox",
    eyebrow: "Разобрать",
    title: "Входящие",
    description: "Новые записи, которым ещё нужен план.",
    emptyTitle: "Входящие не подключены",
    emptyDescription: "На этом этапе PWA не запрашивает Task Engine данные.",
  },
  {
    path: "/planner-queue",
    eyebrow: "Следующий шаг",
    title: "Очередь планирования",
    description: "Черновики и записи, готовые к уточнению.",
    emptyTitle: "Очередь пока пуста",
    emptyDescription: "Интерактивное планирование будет добавлено отдельным API этапом.",
  },
  {
    path: "/timeline",
    eyebrow: "История",
    title: "Лента",
    description: "Хронология изменений и принятых решений.",
    emptyTitle: "События пока не загружены",
    emptyDescription: "Позже здесь появится история WorkItemEvent.",
  },
];

export function PlaceholderPage({ page }: { page: PageDefinition }) {
  return (
    <section className="page" aria-labelledby="page-title">
      <header className="page-heading">
        <span className="eyebrow">{page.eyebrow}</span>
        <h1 id="page-title">{page.title}</h1>
        <p>{page.description}</p>
      </header>
      <EmptyState title={page.emptyTitle} description={page.emptyDescription} />
    </section>
  );
}
