import { Fragment, useState } from "react";

const filters = [
  { value: "all", label: "Все" },
  { value: "install", label: "К установке" },
  { value: "local", label: "Локальные" },
  { value: "standard", label: "Стандартные" },
];

const kinds = {
  external: "Внешний пакет",
  unknown: "Уточнить пакет",
  local: "Локальный модуль",
  standard: "Стандартный",
};

function Warnings({ warnings }) {
  const [showInfo, setShowInfo] = useState(false);
  const visible = warnings.filter(
    (warning) => showInfo || warning.level !== "info",
  );
  const hasSecrets = warnings.some((warning) => warning.level === "danger");

  return (
    <details className="warnings-block" open={hasSecrets || undefined}>
      <summary>Замечания к проекту ({warnings.length})</summary>
      <label className="checkbox-row">
        <input
          type="checkbox"
          name="show-info"
          checked={showInfo}
          onChange={(event) => setShowInfo(event.target.checked)}
        />
        Показать также пояснения
      </label>
      <ul className="warning-list">
        {visible.map((warning, index) => (
          <li key={index} className={warning.level}>
            <p>{warning.message}</p>
            {warning.file && (
              <code>
                {warning.file}
                {warning.line > 0 && `:${warning.line}`}
              </code>
            )}
          </li>
        ))}
      </ul>
      {visible.length === 0 && (
        <p className="field-hint">
          Предупреждений нет. Дополнительные пояснения доступны выше.
        </p>
      )}
    </details>
  );
}

function DependencyRow({
  dependency,
  expanded,
  onToggle,
  onChange,
  suggestion,
}) {
  const { module, package: packageName, kind } = dependency;
  const needsInstall = kind === "external" || kind === "unknown";
  const label = module.startsWith("declared:") ? packageName : module;
  const kindLabel =
    kind === "unknown" && packageName ? "Указан вручную" : kinds[kind];

  return (
    <Fragment>
      <tr>
        <td>
          <button
            className="evidence-toggle"
            aria-expanded={expanded}
            onClick={onToggle}
          >
            <code>{label}</code>
          </button>
        </td>
        <td>
          <span className={`kind-label ${kind}`}>{kindLabel}</span>
        </td>
        {needsInstall ? (
          <>
            <td>
              <input
                className={!packageName ? "needs-input" : ""}
                aria-label={`Пакет для ${module}`}
                name={`package-${module}`}
                autoComplete="off"
                spellCheck={false}
                placeholder="Имя пакета…"
                value={packageName}
                onChange={(event) =>
                  onChange(module, "package", event.target.value)
                }
              />
              {!packageName && suggestion?.status === "found" && (
                <div className="package-suggestion">
                  <span>
                    В PyPI есть{" "}
                    <a href={suggestion.url} target="_blank" rel="noreferrer">
                      {suggestion.package}
                    </a>
                    .
                  </span>
                  <p>{suggestion.summary}</p>
                  <button
                    className="text-button"
                    onClick={() =>
                      onChange(module, "package", suggestion.package)
                    }
                  >
                    Использовать {suggestion.package}
                  </button>
                  <span>
                    Совпадение имени не гарантирует соответствие импорту.
                  </span>
                </div>
              )}
              {!packageName && suggestion?.status === "not_found" && (
                <p className="field-hint">
                  По этому имени пакет в PyPI не найден. Название для pip может
                  отличаться.
                </p>
              )}
              {!packageName &&
                ["unavailable", "skipped"].includes(suggestion?.status) && (
                  <p className="field-hint">
                    PyPI не проверен. Название можно указать вручную.
                  </p>
                )}
            </td>
            <td>
              <input
                aria-label={`Версия для ${module}`}
                name={`version-${module}`}
                autoComplete="off"
                spellCheck={false}
                placeholder="Не указана"
                value={dependency.version}
                onChange={(event) =>
                  onChange(module, "version", event.target.value)
                }
              />
              {dependency.environment_version && (
                <div className="environment-version">
                  <span>У автора: {dependency.environment_version}</span>
                  {!dependency.version && (
                    <button
                      className="text-button"
                      onClick={() =>
                        onChange(
                          module,
                          "version",
                          dependency.environment_version,
                        )
                      }
                    >
                      Подставить версию
                    </button>
                  )}
                </div>
              )}
            </td>
          </>
        ) : (
          <td colSpan={2} className="no-install">
            Установка через pip не нужна
          </td>
        )}
      </tr>
      {expanded && (
        <tr className="evidence-row">
          <td colSpan={4}>
            <p>{dependency.source}</p>
            <ul>
              {dependency.evidence.map((evidence, index) => (
                <li key={index}>
                  <code>
                    {evidence.file}
                    {evidence.line > 0 && `:${evidence.line}`}
                  </code>
                  <span>{evidence.detail}</span>
                </li>
              ))}
            </ul>
            {dependency.environment_version && (
              <p>
                В отчёте исходной среды:{" "}
                <code>{dependency.environment_version}</code>. Совместимость не
                проверена.
              </p>
            )}
          </td>
        </tr>
      )}
    </Fragment>
  );
}

export default function Dependencies({
  analysis,
  dependencies,
  onChange,
  onBack,
  onContinue,
  packageSearch,
}) {
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState(null);

  const unresolved = dependencies.filter(
    (dependency) =>
      (dependency.kind === "external" || dependency.kind === "unknown") &&
      !dependency.package,
  ).length;

  const shown = dependencies.filter((dependency) => {
    const needsInstall =
      dependency.kind === "external" || dependency.kind === "unknown";
    const matchesFilter =
      filter === "all" ||
      (filter === "install" && needsInstall) ||
      dependency.kind === filter;
    const text = `${dependency.module} ${dependency.package}`.toLowerCase();
    return matchesFilter && text.includes(search.toLowerCase());
  });

  function toggleEvidence(module) {
    setExpanded((current) => (current === module ? null : module));
  }

  return (
    <section aria-label="Проверка зависимостей">
      <div className="project-summary">
        <span>Файлов: {analysis.files.length}</span>
        {analysis.stack.length > 0 && <span>{analysis.stack.join(", ")}</span>}
      </div>
      <div className="python-note">
        <strong>
          Требование к Python: {analysis.python_requirement || "не указано"}
        </strong>
        <p>{analysis.python_source}</p>
        {analysis.environment_summary && (
          <p>Среда автора: {analysis.environment_summary}</p>
        )}
      </div>
      {dependencies.some(
        (dependency) =>
          ["external", "unknown"].includes(dependency.kind) &&
          !dependency.version,
      ) && (
        <p className="version-note">
          По import версию определить нельзя. Пустое поле означает, что она не
          найдена в файлах зависимостей. Впишите известную версию или оставьте
          поле пустым.
        </p>
      )}
      {unresolved > 0 && (
        <p className="notice">
          Пакеты не определены для {unresolved} импортов. Уточните их или
          оставьте в черновике.
        </p>
      )}
      {packageSearch.pending && (
        <p className="field-hint" role="status">
          Ищем неизвестные пакеты в PyPI…
        </p>
      )}
      {packageSearch.error && (
        <p className="field-hint" role="status">
          {packageSearch.error}
        </p>
      )}
      <div className="table-toolbar">
        <div className="filter-tabs" aria-label="Фильтр зависимостей">
          {filters.map(({ value, label }) => (
            <button
              key={value}
              aria-pressed={filter === value}
              onClick={() => setFilter(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <input
          className="search-input"
          type="search"
          name="dependency-search"
          aria-label="Найти зависимость"
          autoComplete="off"
          spellCheck={false}
          placeholder="Найти модуль…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      <div
        className="dependency-table-wrap"
        tabIndex={0}
        role="region"
        aria-label="Таблица зависимостей"
      >
        <table className="dependency-table">
          <thead>
            <tr>
              <th scope="col">Импорт / источник</th>
              <th scope="col">Тип</th>
              <th scope="col">Пакет для установки</th>
              <th scope="col">Версия</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((dependency) => (
              <DependencyRow
                key={dependency.module}
                dependency={dependency}
                expanded={expanded === dependency.module}
                onToggle={() => toggleEvidence(dependency.module)}
                onChange={onChange}
                suggestion={packageSearch.results[dependency.module]}
              />
            ))}
            {shown.length === 0 && (
              <tr>
                <td colSpan={4} className="empty-table">
                  Зависимостей по этому фильтру нет.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="field-hint">
        Ограничение версии: <code>==2.2.3</code> или <code>&gt;=2,&lt;3</code>.
      </p>
      <Warnings warnings={analysis.warnings} />
      <div className="panel-actions">
        <button className="text-button" onClick={onBack}>
          Назад к загрузке
        </button>
        <button className="button primary" onClick={onContinue}>
          К скачиванию
        </button>
      </div>
    </section>
  );
}
