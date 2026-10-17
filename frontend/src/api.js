async function request(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error(
      "Сервер недоступен. Проверьте, запущен ли Python-сервер, и повторите.",
    );
  }
  if (response.ok) return response;

  let message =
    response.status === 413
      ? "Загрузка слишком большая. Допустимо до 20 МБ."
      : "Не удалось выполнить запрос. Попробуйте ещё раз.";
  const body = await response.text();
  try {
    const error = JSON.parse(body);
    if (typeof error.detail === "string") {
      message = error.detail;
    } else if (Array.isArray(error.detail)) {
      message = "Проверьте имя пакета, версию Python и команду запуска.";
    }
  } catch {
    // Ответ прокси при ошибке может быть обычным текстом, а не JSON.
  }
  throw new Error(message);
}

async function upload(path, files, environment) {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file, file.webkitRelativePath || file.name);
  }
  if (environment) form.append("environment", environment);
  const response = await request(path, {
    method: "POST",
    body: form,
  });
  return response.json();
}

export function inspectProject(files) {
  return upload("/api/inspect", files);
}

export function analyzeProject(files, environment) {
  return upload("/api/analyze", files, environment);
}

function post(path, options) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options),
  });
}

export async function previewPackage(options) {
  const response = await post("/api/preview", options);
  return response.json();
}

export async function downloadPackage(options) {
  const response = await post("/api/package", options);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "python-reproducibility.zip";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function deleteProject(id) {
  await request(`/api/projects/${id}`, { method: "DELETE" });
}
