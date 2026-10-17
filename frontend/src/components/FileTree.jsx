function buildTree(paths) {
  const root = { folders: new Map(), files: [] };
  for (const path of paths) {
    const parts = path.split("/");
    let branch = root;
    for (const folder of parts.slice(0, -1)) {
      if (!branch.folders.has(folder)) {
        branch.folders.set(folder, { folders: new Map(), files: [] });
      }
      branch = branch.folders.get(folder);
    }
    branch.files.push({ name: parts.at(-1), path });
  }
  return root;
}

function Branch({ node, generated }) {
  const folders = [...node.folders.entries()].sort(([a], [b]) =>
    a.localeCompare(b, "ru"),
  );
  const files = [...node.files].sort((a, b) =>
    a.name.localeCompare(b.name, "ru"),
  );

  return (
    <ul className="tree-branch">
      {folders.map(([name, folder]) => (
        <li key={`folder:${name}`}>
          <details open>
            <summary>
              <span className="tree-folder">{name}/</span>
            </summary>
            <Branch node={folder} generated={generated} />
          </details>
        </li>
      ))}
      {files.map((file) => (
        <li key={file.path} className="tree-file">
          <span className="tree-filename">{file.name}</span>
          {generated.has(file.path) && (
            <span className="tree-added">Добавлен</span>
          )}
        </li>
      ))}
    </ul>
  );
}

export default function FileTree({
  paths,
  name,
  title = "Структура проекта",
  generated = [],
}) {
  return (
    <section className="file-tree" aria-label={title}>
      <div className="tree-heading">
        <h2>{title}</h2>
        <span>Файлов: {paths.length}</span>
      </div>
      <div className="tree-scroll" tabIndex={0}>
        <div className="tree-root">{name || "project"}/</div>
        <Branch node={buildTree(paths)} generated={new Set(generated)} />
      </div>
    </section>
  );
}
