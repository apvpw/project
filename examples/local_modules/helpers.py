"""Функции обработки текста, которые входят в сам проект."""

import re
from collections import Counter


def count_words(text):
    words = re.findall(r"[а-яёa-z]+", text.lower())
    return Counter(words)
