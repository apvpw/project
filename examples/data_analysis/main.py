"""Небольшой отчёт о продажах. Запуск из папки проекта: python main.py."""

from pathlib import Path

import numpy as np
import pandas as pd


def main():
    folder = Path(__file__).resolve().parent
    sales = pd.read_csv(folder / "sales.csv")
    sales["revenue"] = sales["quantity"] * sales["price"]
    total = float(np.sum(sales["revenue"]))
    average = float(np.mean(sales["revenue"]))
    sales.to_csv(folder / "result.csv", index=False)
    print(f"Строк: {len(sales)}")
    print(f"Выручка: {total:.2f}")
    print(f"Средняя выручка на строку: {average:.2f}")
    print("Отчёт сохранён в result.csv")


if __name__ == "__main__":
    main()
