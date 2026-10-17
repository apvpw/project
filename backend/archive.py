"""Чтение исходников, ZIP и RAR без извлечения файлов по путям из архива."""

import io
import stat
import tokenize
import zipfile
import zlib
from pathlib import PurePosixPath

import rarfile

MAX_UPLOAD = 20 * 1024 * 1024
MAX_EXPANDED = 50 * 1024 * 1024
MAX_FILE = 5 * 1024 * 1024
MAX_FILES = 500
IGNORED = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "__MACOSX",
    ".pytest_cache",
}


class UploadError(ValueError):
    pass


def decode_python(data: bytes) -> str:
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    return data.decode(encoding)


def safe_name(name: str) -> str:
    if "\\" in name or "\x00" in name or len(name) > 512:
        raise UploadError("Имя файла содержит недопустимые символы.")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or ":" in path.parts[0]
    ):
        raise UploadError(
            "В архиве найден небезопасный путь. Уберите абсолютные пути и '..'."
        )
    if any(ord(char) < 32 for char in name):
        raise UploadError("Имя файла содержит управляющие символы.")
    return path.as_posix()


def rar_entries(data: bytes):
    with rarfile.RarFile(io.BytesIO(data), errors="strict") as archive:
        if archive.needs_password():
            raise UploadError("RAR защищён паролем. Загрузите архив без пароля.")
        if len(archive.volumelist()) > 1:
            raise UploadError("Многотомный RAR не поддерживается. Соберите один архив.")
        entries = archive.infolist()
        if len(entries) > MAX_FILES + 200:
            raise UploadError("В архиве слишком много файлов и каталогов.")
        expanded = 0
        for item in entries:
            safe_name(item.filename.rstrip("/"))
            if item.is_dir():
                continue
            if not item.is_file() or item.is_symlink() or item.file_redir:
                raise UploadError("Ссылки и специальные файлы в RAR не поддерживаются.")
            expanded += item.file_size
            if item.file_size > MAX_FILE or expanded > MAX_EXPANDED:
                raise UploadError("Архив превышает допустимый размер после распаковки.")
            with archive.open(item) as source:
                yield item.filename, source.read(MAX_FILE + 1)


def read_uploads(
    uploads: list[tuple[str, bytes]],
) -> tuple[dict[str, bytes], list[str]]:
    files: dict[str, bytes] = {}
    skipped: list[str] = []
    total = 0
    if sum(len(data) for _, data in uploads) > MAX_UPLOAD:
        raise UploadError("Размер загрузки превышает 20 МБ.")
    archives = [
        name
        for name, _ in uploads
        if "/" not in name and name.lower().endswith((".zip", ".rar"))
    ]
    if archives and len(uploads) != 1:
        raise UploadError(
            "Выберите один ZIP/RAR или несколько отдельных файлов, без смешивания."
        )

    def add(name: str, data: bytes):
        nonlocal total
        name = safe_name(name)
        parts = PurePosixPath(name).parts
        if any(part in IGNORED for part in parts) or parts[-1] == ".DS_Store":
            skipped.append(name)
            return
        if len(data) > MAX_FILE:
            raise UploadError(f"Файл {name} больше 5 МБ.")
        total += len(data)
        if total > MAX_EXPANDED or len(files) >= MAX_FILES:
            raise UploadError("После распаковки допустимо до 50 МБ и 500 файлов.")
        if name in files:
            raise UploadError(f"Повторяющееся имя файла: {name}.")
        if any(
            name.startswith(other + "/") or other.startswith(name + "/")
            for other in files
        ):
            raise UploadError(
                f"Путь {name} одновременно используется как файл и папка."
            )
        files[name] = data

    try:
        for name, data in uploads:
            if name in archives:
                if name.lower().endswith(".rar"):
                    for filename, content in rar_entries(data):
                        add(filename, content)
                    continue
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    entries = archive.infolist()
                    if len(entries) > MAX_FILES + 200:
                        raise UploadError("В архиве слишком много файлов и каталогов.")
                    expanded = 0
                    for item in entries:
                        safe_name(item.filename.rstrip("/"))
                        if item.is_dir():
                            continue
                        mode = item.external_attr >> 16
                        if stat.S_ISLNK(mode) or (
                            stat.S_IFMT(mode) and not stat.S_ISREG(mode)
                        ):
                            raise UploadError(
                                "Ссылки и специальные файлы в ZIP не поддерживаются."
                            )
                        if item.flag_bits & 1:
                            raise UploadError(
                                "ZIP защищён паролем. Загрузите архив без пароля."
                            )
                        expanded += item.file_size
                        if item.file_size > MAX_FILE or expanded > MAX_EXPANDED:
                            raise UploadError(
                                "Архив превышает допустимый размер после распаковки."
                            )
                        add(item.filename, archive.read(item))
            else:
                add(name, data)
    except rarfile.RarCannotExec as error:
        raise UploadError(
            "На сервере не установлен распаковщик RAR. Используйте ZIP или выберите папку."
        ) from error
    except rarfile.Error as error:
        raise UploadError(
            "Не удалось прочитать RAR. Проверьте архив; пароль и многотомные архивы не поддерживаются."
        ) from error
    except (
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        EOFError,
        zlib.error,
    ) as error:
        raise UploadError(
            "Не удалось прочитать ZIP. Проверьте архив и попробуйте снова."
        ) from error

    # Убираем одну внешнюю папку загрузки; вложенные каталоги сохраняем.
    if files:
        parts = [PurePosixPath(name).parts for name in files]
        if all(len(p) > 1 and p[0] == parts[0][0] for p in parts):
            files = {
                "/".join(PurePosixPath(name).parts[1:]): data
                for name, data in files.items()
            }
    python_files = [name for name in files if name.endswith(".py")]
    if not python_files:
        raise UploadError(
            "Это не Python-проект: не найдены файлы .py. Выберите исходники или папку с кодом."
        )
    for name in python_files:
        try:
            source = decode_python(files[name])
        except (SyntaxError, UnicodeError, LookupError):
            continue
        if not any(ord(char) < 32 and char not in "\t\r\n\f" for char in source):
            break
    else:
        raise UploadError(
            "В файлах .py не найден текст Python-кода. Проверьте, что это исходники, а не переименованные бинарные файлы, и укажите кодировку в исходнике при необходимости."
        )
    return files, skipped
