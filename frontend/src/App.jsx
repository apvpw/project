import { useEffect, useMemo, useRef, useState } from "react";
import { analyzeProject, deleteProject, findPackages } from "./api.js";
import Dependencies from "./components/Dependencies.jsx";
import PackagePanel from "./components/PackagePanel.jsx";
import UploadPanel from "./components/UploadPanel.jsx";

const steps = ["Загрузка", "Зависимости", "Скачивание"];

export default function App() {
  const [step, setStep] = useState(0);
  const [files, setFiles] = useState([]);
  const [environment, setEnvironment] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [dependencies, setDependencies] = useState([]);
  const [packageSearch, setPackageSearch] = useState({
    pending: false,
    results: {},
    error: "",
  });
  const [settings, setSettings] = useState({
    command: "",
    python_version: "3.12",
    docker: false,
    exclude_suspected_secrets: true,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const heading = useRef(null);
  const help = useRef(null);
  const analyzedInput = useRef(null);
  const analysisReady =
    analysis &&
    analyzedInput.current?.files === files &&
    analyzedInput.current?.environment === environment;

  // Стабильный объект не даёт предпросмотру перезапрашиваться при смене шага.
  const options = useMemo(
    () => ({
      ...settings,
      analysis_id: analysis?.id,
      dependencies,
    }),
    [settings, analysis, dependencies],
  );

  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }, [step]);

  async function analyze() {
    setBusy(true);
    setError("");
    try {
      const result = await analyzeProject(files, environment);
      if (analysis) {
        try {
          await deleteProject(analysis.id);
        } catch (error) {
          console.warn(
            "Предыдущая загрузка удалится по сроку хранения:",
            error.message,
          );
        }
      }
      setAnalysis(result);
      analyzedInput.current = { files, environment };
      setDependencies(result.dependencies);
      const match = result.python_requirement.match(/3\.(\d+)/);
      const minor = match ? Number(match[1]) : 12;
      const pythonVersion = minor >= 9 && minor <= 15 ? `3.${minor}` : "3.12";
      setSettings((current) => ({
        ...current,
        command: result.entry_points[0]?.command || "",
        python_version: pythonVersion,
      }));
      setStep(1);
      setPackageSearch({ pending: true, results: {}, error: "" });
      try {
        const results = await findPackages(result.id);
        setPackageSearch({ pending: false, results, error: "" });
      } catch (error) {
        setPackageSearch({
          pending: false,
          results: {},
          error:
            "Поиск в PyPI недоступен. Неизвестные пакеты можно указать вручную.",
        });
      }
    } catch (error) {
      setError(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    setError("");
    try {
      if (analysis) await deleteProject(analysis.id);
      setAnalysis(null);
      analyzedInput.current = null;
      setDependencies([]);
      setPackageSearch({ pending: false, results: {}, error: "" });
      setFiles([]);
      setEnvironment(null);
      setStep(0);
    } catch (error) {
      setError(error.message);
    } finally {
      setBusy(false);
    }
  }

  function changeStep(nextStep) {
    setError("");
    setStep(nextStep);
  }

  function changeDependency(module, field, value) {
    setDependencies((current) =>
      current.map((dependency) =>
        dependency.module === module
          ? { ...dependency, [field]: value }
          : dependency,
      ),
    );
  }

  function changeOption(field, value) {
    setSettings((current) => ({ ...current, [field]: value }));
  }

  return (
    <>
      <a className="skip-link" href="#main-content">
        К содержимому
      </a>
      <header className="site-header">
        <div className="header-inner">
          <span className="brand" translate="no">
            Перенос
          </span>
          <span className="header-description">
            Подготовка Python-проекта к запуску
          </span>
          <div className="header-actions">
            <button
              className="text-button"
              onClick={() => help.current.showModal()}
            >
              Как это работает
            </button>
            {analysis && (
              <button className="text-button" onClick={reset} disabled={busy}>
                Новый проект
              </button>
            )}
          </div>
        </div>
      </header>

      <main
        id="main-content"
        className={`app-layout${step === 1 ? "" : " compact-layout"}`}
      >
        <nav className="workflow" aria-label="Этапы подготовки">
          <ol className="step-list">
            {steps.map((title, index) => (
              <li key={title}>
                <button
                  aria-current={step === index ? "step" : undefined}
                  disabled={busy || (index > 0 && !analysisReady)}
                  onClick={() => changeStep(index)}
                >
                  {title}
                </button>
              </li>
            ))}
          </ol>
        </nav>

        <div className="page-heading">
          <h1 ref={heading} tabIndex={-1}>
            {step === 0
              ? "Загрузите проект"
              : step === 1
                ? "Проверьте зависимости"
                : "Скачайте проект"}
          </h1>
          {step === 0 && (
            <p>
              Добавьте код и данные. Получите ZIP с исходниками,
              requirements.txt и инструкцией запуска.
            </p>
          )}
          {step === 1 && (
            <p>
              {analysis.name} · Уточните неизвестные пакеты и версии, если они
              вам известны.
            </p>
          )}
          {step === 2 && (
            <p>
              {analysis.name} · Проверьте команду запуска. Инструкции будут
              внутри ZIP.
            </p>
          )}
        </div>

        <div className="workspace">
          {error && (
            <p className="error-message" role="alert">
              {error}
            </p>
          )}
          {step === 0 && (
            <UploadPanel
              files={files}
              setFiles={setFiles}
              environment={environment}
              setEnvironment={setEnvironment}
              busy={busy}
              onAnalyze={analyze}
              onError={setError}
            />
          )}
          {step === 1 && analysisReady && (
            <Dependencies
              analysis={analysis}
              dependencies={dependencies}
              onChange={changeDependency}
              packageSearch={packageSearch}
              onBack={() => changeStep(0)}
              onContinue={() => changeStep(2)}
            />
          )}
          {step === 2 && analysisReady && (
            <PackagePanel
              analysis={analysis}
              options={options}
              onOption={changeOption}
              onBack={() => changeStep(1)}
            />
          )}
        </div>
      </main>
      <dialog ref={help} className="help-dialog" aria-labelledby="help-title">
        <div className="help-heading">
          <h2 id="help-title">Как это работает</h2>
          <form method="dialog">
            <button className="text-button" autoFocus>
              Закрыть
            </button>
          </form>
        </div>
        <ol className="help-steps">
          <li>
            <strong>Добавьте проект.</strong> Выберите папку, ZIP, RAR или
            отдельные файлы. Приложите локальные модули и пример входных данных.
            Сразу появится дерево файлов.
          </li>
          <li>
            <strong>Проверьте зависимости.</strong> Сервис читает код без
            запуска, отделяет стандартные и локальные модули от внешних
            библиотек. Неоднозначные пакеты можно уточнить вручную.
          </li>
          <li>
            <strong>Скачайте ZIP.</strong> Внутри будут исходники, данные,
            requirements.txt, README с командами установки и запуска, а также
            REPORT.md с замечаниями. Dockerfile добавляется по желанию.
          </li>
        </ol>
        <p>
          Проверяем наличие Python-кода. Изображения, аудио и другие данные
          сохраняются, если они загружены вместе с проектом. Служебные папки
          окружения пропускаются.
        </p>
        <p>
          По одному <code>import</code> нельзя узнать версию библиотеки. Версии
          берутся из загруженных файлов зависимостей или вводятся вручную.
        </p>
        <p>
          После распаковки следуйте README и проверьте запуск в чистом
          виртуальном окружении. Статический анализ не гарантирует точного
          восстановления среды.
        </p>
      </dialog>
      <div className="sr-only" role="status">
        {busy && "Анализируем проект…"}
        {!busy &&
          analysisReady &&
          `Найдено зависимостей: ${dependencies.length}.`}
      </div>
    </>
  );
}
