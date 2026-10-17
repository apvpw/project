import { useEffect, useState } from "react";
import { inspectProject } from "../api.js";
import FileTree from "./FileTree.jsx";

const ignoredFolders = new Set([
  ".git",
  ".venv",
  "venv",
  "node_modules",
  "__pycache__",
  "__MACOSX",
  ".pytest_cache",
]);

function filePath(file) {
  return file.webkitRelativePath || file.name;
}

function sizeLabel(bytes) {
  if (bytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  }
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}

export default function UploadPanel({
  files,
  setFiles,
  environment,
  setEnvironment,
  busy,
  onAnalyze,
  onError,
}) {
  const [dragging, setDragging] = useState(false);
  const [structure, setStructure] = useState(null);
  const [reading, setReading] = useState(false);
  const folderName = files[0]?.webkitRelativePath.split("/")[0];
  const hasArchive = files.some(
    (file) => !file.webkitRelativePath && /\.(zip|rar)$/i.test(file.name),
  );

  useEffect(() => {
    let active = true;
    setStructure(null);
    if (files.length === 0) {
      setReading(false);
      return;
    }
    setReading(true);
    inspectProject(files)
      .then((result) => {
        if (active) setStructure(result);
      })
      .catch((error) => {
        if (active) onError(error.message);
      })
      .finally(() => {
        if (active) setReading(false);
      });
    return () => {
      active = false;
    };
  }, [files, onError]);

  function addFiles(fileList) {
    const selected = Array.from(fileList);
    if (selected.length === 0) return;
    const incoming = selected.filter((file) => {
      const parts = filePath(file).split("/");
      return (
        file.name !== ".DS_Store" &&
        !parts.slice(0, -1).some((part) => ignoredFolders.has(part))
      );
    });
    if (incoming.length === 0) {
      onError(
        "В выбранной папке нет файлов проекта. Служебные папки пропускаются.",
      );
      return;
    }

    const newFolder = incoming.some((file) => file.webkitRelativePath);
    const newArchive = incoming.some(
      (file) => !file.webkitRelativePath && /\.(zip|rar)$/i.test(file.name),
    );
    const previous =
      hasArchive || newArchive || folderName || newFolder ? [] : files;
    const byName = new Map();
    for (const file of [...previous, ...incoming]) {
      byName.set(filePath(file), file);
    }
    const next = [...byName.values()];
    const archiveCount = next.filter(
      (file) => !file.webkitRelativePath && /\.(zip|rar)$/i.test(file.name),
    ).length;
    if (archiveCount > 0 && next.length > 1) {
      onError("Выберите один ZIP/RAR или несколько отдельных файлов.");
      return;
    }

    const totalSize = next.reduce((sum, file) => sum + file.size, 0);
    if (next.length > 100 || totalSize > 20 * 1024 * 1024) {
      onError("Допустимо до 100 отдельных файлов и до 20 МБ суммарно.");
      return;
    }
    onError("");
    setFiles(next);
  }

  function chooseFiles(event) {
    addFiles(event.target.files);
    event.target.value = "";
    event.target.closest("details").open = false;
  }

  function dropFiles(event) {
    event.preventDefault();
    setDragging(false);
    if (!busy) addFiles(event.dataTransfer.files);
  }

  function dragOver(event) {
    event.preventDefault();
    if (!busy) setDragging(true);
  }

  function dragLeave(event) {
    if (!event.currentTarget.contains(event.relatedTarget)) {
      setDragging(false);
    }
  }

  function chooseEnvironment(event) {
    setEnvironment(event.target.files[0] || null);
    event.target.value = "";
  }

  const pickers = (
    <details className="project-picker">
      <summary className="button">
        {files.length ? "Добавить или заменить" : "Выбрать проект"}
      </summary>
      <div className="picker-options">
        <label className="file-picker">
          <span>Файлы или архив ZIP / RAR</span>
          <input
            type="file"
            name="project-files"
            multiple
            disabled={busy}
            aria-label="Файлы проекта"
            onChange={chooseFiles}
          />
        </label>
        <label className="file-picker">
          <span>Папка проекта</span>
          <input
            type="file"
            name="project-folder"
            aria-label="Папка проекта"
            webkitdirectory=""
            multiple
            disabled={busy}
            onChange={chooseFiles}
          />
        </label>
      </div>
    </details>
  );

  return (
    <section aria-label="Загрузка проекта">
      <div
        className={`dropzone ${dragging ? "is-dragging" : ""}`}
        onDragOver={dragOver}
        onDragLeave={dragLeave}
        onDrop={dropFiles}
      >
        {files.length === 0 ? (
          <div className="upload-placeholder">
            <h2>
              {dragging ? "Отпустите файлы здесь" : "Перетащите файлы сюда"}
            </h2>
            <p>ZIP, RAR, папка или отдельные файлы. До 20 МБ.</p>
            {pickers}
          </div>
        ) : (
          <>
            {folderName ? (
              <div className="folder-selection">
                <div className="file-name">
                  <strong>{folderName}/</strong>
                  <span>
                    Файлов: {files.length} ·{" "}
                    {sizeLabel(
                      files.reduce((total, file) => total + file.size, 0),
                    )}
                  </span>
                </div>
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={() => setFiles([])}
                >
                  Убрать папку
                </button>
              </div>
            ) : (
              <ul className="file-list" aria-label="Выбранные файлы">
                {files.map((file) => (
                  <li key={filePath(file)}>
                    <div className="file-name">
                      <strong>{file.name}</strong>
                      <span>{sizeLabel(file.size)}</span>
                    </div>
                    <button
                      className="text-button"
                      aria-label={`Убрать ${file.name}`}
                      disabled={busy}
                      onClick={() =>
                        setFiles(files.filter((item) => item !== file))
                      }
                    >
                      Убрать
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {pickers}
          </>
        )}
      </div>

      {reading && (
        <p className="field-hint" role="status">
          Читаем структуру проекта…
        </p>
      )}
      {structure && (
        <>
          <FileTree paths={structure.files} name={structure.name} />
          {structure.skipped > 0 && (
            <p className="field-hint">
              Служебные файлы и окружения пропущены: {structure.skipped}.
            </p>
          )}
        </>
      )}

      <p className="upload-note">
        Добавьте локальные модули и пример данных вместе с кодом. Чужой код не
        запускается; загрузка удаляется через 30 минут.
        {folderName &&
          " Служебные папки (.venv, .git, node_modules) не отправляются. До 100 файлов в папке."}
      </p>

      <details className="environment-input">
        <summary>Есть отчёт среды с версиями?</summary>
        <p>
          Необязательно. Если проект работает на вашем компьютере, команда{" "}
          <code>python tools/environment_report.py</code> сохранит версии в
          JSON. Приложите его, чтобы видеть эти версии в деталях зависимостей.
        </p>
        <label className="file-picker-label">
          Выбрать JSON-отчёт
          <input
            type="file"
            name="environment"
            accept=".json"
            disabled={busy}
            onChange={chooseEnvironment}
          />
        </label>
        {environment && (
          <div className="inline-file">
            <span>{environment.name}</span>
            <button
              className="text-button"
              disabled={busy}
              onClick={() => setEnvironment(null)}
            >
              Убрать отчёт
            </button>
          </div>
        )}
      </details>

      <div className="panel-actions">
        <span className="field-hint">Структура папок сохранится в ZIP.</span>
        <button
          className="button primary"
          disabled={!structure || reading || busy}
          onClick={onAnalyze}
        >
          {busy ? "Анализируем…" : "Продолжить"}
        </button>
      </div>
    </section>
  );
}
