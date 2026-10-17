"""Пример с локальным модулем и только стандартной библиотекой."""

from pathlib import Path

from helpers import count_words


def main():
    text = (Path(__file__).resolve().parent / "sample.txt").read_text(encoding="utf-8")
    counts = count_words(text)
    print(f"Всего слов: {sum(counts.values())}")
    print(f"Уникальных слов: {len(counts)}")
    for word, count in counts.most_common(3):
        print(f"{word}: {count}")


if __name__ == "__main__":
    main()
