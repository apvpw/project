import { useEffect, useState } from "react";
import { downloadPackage, previewPackage } from "../api.js";
import FileTree from "./FileTree.jsx";

const pythonVersions = ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14", "3.15"];

function FilePreview({ files, pending }) {
  const [selected, setSelected] = useState("");
  const [copyStatus, setCopyStatus] = useState("");
  const names = Object.keys(files);
  const current = Object.hasOwn(files, selected) ? selected : names[0];
  const selectedIndex = names.indexOf(current);

  function selectFile(name) {
    setSelected(name);
    setCopyStatus("");
  }

  function moveTab(event) {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
    event.preventDefault();

    let index = selectedIndex;
    if (event.key === "Home") index = 0;
    if (event.key === "End") index = names.length - 1;
    if (event.key === "ArrowRight") index = (index + 1) % names.length;
    if (event.key === "ArrowLeft")
      index = (index - 1 + names.length) % names.length;
    selectFile(names[index]);
    event.currentTarget.querySelectorAll('[role="tab"]')[index]?.focus();
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(files[current]);
      setCopyStatus("Скопировано");
    } catch {
      setCopyStatus("Выделите текст и скопируйте его вручную.");
    }
  }

  return (
    <div className="file-preview" aria-busy={pending}>
      <div
        className="preview-tabs"
        role="tablist"
        aria-label="Созданные файлы"
        onKeyDown={moveTab}
      >
        {names.map((name, index) => (
          <button
            key={name}
            role="tab"
            id={`preview-tab-${index}`}
            aria-controls="preview-content"
            aria-selected={name === current}
            tabIndex={name === current ? 0 : -1}
            onClick={() => selectFile(name)}
          >
            {name.split("/").pop()}
          </button>
        ))}
      </div>
      <pre
        id="preview-content"
        role="tabpanel"
        aria-labelledby={current ? `preview-tab-${selectedIndex}` : undefined}
        aria-label={current ? undefined : "Предпросмотр файлов"}
        tabIndex={0}
      >
        {files[current] ||
          (pending
            ? "Подготавливаем файлы…"
            : "Исправьте поля, чтобы увидеть предпросмотр.")}
      </pre>
      <div className="preview-status">
        <span role="status">
          {pending ? "Обновляем…" : copyStatus || current}
        </span>
        <button
          className="text-button"
          disabled={pending || !current}
          onClick={copy}
        >
          Копировать
        </button>
      </div>
    </div>
  );
}

export default function PackagePanel({
  analysis,
  options,
  onOption,
  onBack,
  downloaded,
  onDownloaded,
}) {
  const [preview, setPreview] = useState({});
  const [archiveFiles, setArchiveFiles] = useState([]);
  const [preservedFiles, setPreservedFiles] = useState({});
  const [pending, setPending] = useState(true);
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    let active = true;
    setPending(true);

    // Не отправляем запрос на каждый символ при редактировании команды.
    const timeout = setTimeout(async () => {
      try {
        const result = await previewPackage(options);
        if (active) {
          setPreview(result.files);
          setArchiveFiles(result.archive_files);
          setPreservedFiles(result.preserved_files);
          setError("");
        }
      } catch (error) {
        if (active) setError(error.message);
      } finally {
        if (active) setPending(false);
      }
    }, 300);

    return () => {
      active = false;
      clearTimeout(timeout);
    };
  }, [options]);

  async function download() {
    setDownloading(true);
    try {
      await downloadPackage(options);
      onDownloaded();
      setError("");
    } catch (error) {
      setError(error.message);
    } finally {
      setDownloading(false);
    }
  }

  const unresolved = options.dependencies.filter(
    (dependency) =>
      (dependency.kind === "external" || dependency.kind === "unknown") &&
      !dependency.package,
  ).length;
  const secretFiles = new Set(
    analysis.warnings
      .filter((warning) => warning.code === "secret")
      .map((warning) => warning.file),
  );
  const entryPoint = analysis.entry_points.find(
    (entry) => entry.command === options.command,
  );
  const canDownload =
    !pending && !downloading && !error && Object.keys(preview).length > 0;

  return (
    <section className="package-panel" aria-label="Скачивание проекта">
      <div className="package-layout">
        <div className="package-main">
          <h2 className="section-title">Запуск после распаковки</h2>
          <p className="package-description">
            Команда и зависимости попадут в README. Их можно будет выполнить в
            отдельном виртуальном окружении.
          </p>
          <div className="launch-settings">
            <label className="field-label" htmlFor="launch-command">
              Команда запуска
            </label>
            <input
              id="launch-command"
              className="command-input"
              name="command"
              list="entry-points"
              autoComplete="off"
              spellCheck={false}
              placeholder="Например: python main.py…"
              value={options.command}
              maxLength={500}
              onChange={(event) => onOption("command", event.target.value)}
            />
            <datalist id="entry-points">
              {analysis.entry_points.map((entry) => (
                <option key={entry.command} value={entry.command}>
                  {entry.reason}
                </option>
              ))}
            </datalist>
            <p className="field-hint">
              {entryPoint?.reason || "Укажите команду и аргументы вручную."}{" "}
              Запуск из корня проекта.
            </p>
            {!options.command && (
              <p className="notice">
                Команда не задана. Её нужно будет дописать в README.
              </p>
            )}
          </div>

          {secretFiles.size > 0 && (
            <div className="secret-options">
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  name="exclude-secrets"
                  checked={options.exclude_suspected_secrets}
                  onChange={(event) =>
                    onOption("exclude_suspected_secrets", event.target.checked)
                  }
                />
                Исключить файлы с признаками секретов
              </label>
              <p className="field-hint">
                {options.exclude_suspected_secrets
                  ? "Исключённые файлы перечислены в отчёте. Настройки восстановите вручную."
                  : "Эти файлы останутся в ZIP. Проверьте их перед передачей."}
              </p>
            </div>
          )}
          {unresolved > 0 && (
            <p className="notice">
              Не уточнены пакеты для {unresolved} импортов. Вернитесь к
              зависимостям или скачайте черновик.
            </p>
          )}
          {error && (
            <p className="error-message" role="alert">
              {error}
            </p>
          )}
          <details className="package-details">
            <summary>Дополнительно</summary>
            <div className="docker-settings">
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  name="docker"
                  checked={options.docker}
                  onChange={(event) => onOption("docker", event.target.checked)}
                />
                Добавить Dockerfile
              </label>
              {options.docker && (
                <>
                  <label className="python-select">
                    Python в Docker
                    <select
                      aria-label="Python для Dockerfile"
                      name="python-version"
                      value={options.python_version}
                      onChange={(event) =>
                        onOption("python_version", event.target.value)
                      }
                    >
                      {pythonVersions.map((version) => (
                        <option key={version}>{version}</option>
                      ))}
                    </select>
                  </label>
                  <p className="field-hint">
                    Требование проекта:{" "}
                    {analysis.python_requirement || "не задано"}. Совместимость
                    выбранной версии не проверялась.
                  </p>
                </>
              )}
            </div>
          </details>
          <div className="verification-guide">
            <h3>Следующий шаг — проверка</h3>
            <p>
              Другой участник запускает проект в чистом venv по README и
              записывает результат в REPORT.md. Сервис не выполняет загруженный
              код.
            </p>
          </div>
        </div>
        <aside className="package-aside" aria-label="Готовый архив">
          <h2 className="section-title">Ваш проект в ZIP</h2>
          <p className="aside-intro">
            Исходники, данные и подготовленные инструкции.
          </p>
          <button
            className="button primary download-button"
            disabled={!canDownload}
            onClick={download}
          >
            {downloading ? "Собираем ZIP…" : "Скачать ZIP"}
          </button>
          <p className="field-hint">Распакуйте архив и начните с README.md.</p>
          {downloaded && (
            <p className="download-result" role="status">
              Пакет подготовлен. Осталось проверить запуск в чистой среде.
            </p>
          )}
          <FileTree
            paths={archiveFiles}
            name={analysis.name}
            title="Состав архива"
            generated={Object.keys(preview)}
          />
          <p className="field-hint">
            <code>REPORT.md</code> включён в пакет: в нём предупреждения,
            неопределённости и место для результата проверки.
          </p>
          {Object.keys(preservedFiles).length > 0 && (
            <p className="field-hint">
              Прежние инструкции сохранены в{" "}
              {Object.values(preservedFiles)[0].split("/")[0]}/.
            </p>
          )}
        </aside>
      </div>
      <details className="package-details preview-details">
        <summary>Посмотреть инструкции и отчёт</summary>
        <FilePreview files={preview} pending={pending} />
      </details>
      <div className="panel-actions">
        <button className="text-button" onClick={onBack}>
          Назад к зависимостям
        </button>
        <span className="field-hint">
          Запуск на другом компьютере проверяется отдельно.
        </span>
      </div>
    </section>
  );
}
