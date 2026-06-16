import sys
import os
import pickle
import csv
import json
import copy
from dataclasses import dataclass, asdict
from enum import Enum
from typing import List, Dict

# ==========================================
# 1. ПЕРЕЛІЧУВАНИЙ ТИП (Enum)
# ==========================================
class Currency(Enum):
    UAH = "UAH"
    USD = "USD"
    EUR = "EUR"


# ==========================================
# 2. МОДЕЛЮВАННЯ ДАНИХ (dataclasses) & JSON
# ==========================================
@dataclass
class User:
    username: str
    email: str

    def __str__(self):
        return f"{self.username} ({self.email})"
    
    def __repr__(self):
        return f"User(username='{self.username}', email='{self.email}')"


@dataclass
class AppConfig:
    """Клас налаштувань для виконання вимоги по JSON персистентності"""
    theme: str = "dark"
    items_per_page: int = 5
    json_filename: str = "app_config.json"

    def save_to_json(self):
        with open(self.json_filename, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=4)

    def load_from_json(self):
        if os.path.exists(self.json_filename):
            with open(self.json_filename, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.theme = data.get("theme", "dark")
                self.items_per_page = data.get("items_per_page", 5)


# ==========================================
# 3. ІНКАПСУЛЯЦІЯ, ВАЛІДАЦІЯ ТА МАГІЧНІ МЕТОДИ
# ==========================================
class ExpenseRecord:
    def __init__(self, description: str, total_amount: float, paid_by: str, currency: Currency, users: List[str]):
        self.description = description      # Викличе сетер
        self.total_amount = total_amount    # Викличе сетер
        self.paid_by = paid_by
        self.currency = currency
        self.users = users

    @property
    def description(self) -> str:
        return self._description

    @description.setter
    def description(self, value: str):
        if not value or not value.strip():
            raise ValueError("Опис витрати не може бути порожнім!")
        self._description = value.strip()

    @property
    def total_amount(self) -> float:
        return self._total_amount

    @total_amount.setter
    def total_amount(self, value: float):
        if value <= 0:
            raise ValueError("Сума витрати повинна бути строго більшою за нуль!")
        self._total_amount = round(value, 2)

    def calculate_splits(self) -> Dict[str, float]:
        """Базовий метод розрахунку (перевизначається в нащадках)"""
        return {}

    # Магічні методи порівняння та відображення
    def __lt__(self, other):
        if not isinstance(other, ExpenseRecord):
            return NotImplemented
        return self.total_amount < other.total_amount

    def __str__(self):
        return f"{self.description}: {self.total_amount} {self.currency.value} (Оплатив: {self.paid_by})"

    def __repr__(self):
        return f"ExpenseRecord(desc='{self.description}', amount={self.total_amount}, paid_by='{self.paid_by}')"


# ==========================================
# 4. ІЄРАРХІЯ КЛАСІВ (Наслідування та Поліморфізм)
# ==========================================
class EqualSplit(ExpenseRecord):
    def calculate_splits(self) -> Dict[str, float]:
        """ПОЛІМОРФІЗМ: Сума ділиться порівну між усіма учасниками"""
        if not self.users:
            return {}
        share = round(self.total_amount / len(self.users), 2)
        return {user: share for user in self.users}


class PercentageSplit(ExpenseRecord):
    def __init__(self, description: str, total_amount: float, paid_by: str, currency: Currency, 
                 users: List[str], percentages: Dict[str, float]):
        super().__init__(description, total_amount, paid_by, currency, users)
        self.percentages = percentages  # {username: відсоток}

    def calculate_splits(self) -> Dict[str, float]:
        """ПОЛІМОРФІЗМ: Сума ділиться відповідно до вказаних відсотків"""
        splits = {}
        for user in self.users:
            pct = self.percentages.get(user, 0.0)
            splits[user] = round((pct / 100.0) * self.total_amount, 2)
        return splits


# ==========================================
# 5. ІТЕРАТОРИ ТА ГЕНЕРАТОРИ
# ==========================================
class ActiveExpenseIterator:
    """Власний ітератор для кастомного обходу колекції витрат"""
    def __init__(self, expenses: List[ExpenseRecord]):
        self._expenses = expenses
        self._index = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self._index < len(self._expenses):
            result = self._expenses[self._index]
            self._index += 1
            return result
        raise StopIteration


# ==========================================
# 6. МЕНЕДЖЕР КОНТЕКСТУ (Транзакційне редагування)
# ==========================================
class ExpenseTransaction:
    """
    Менеджер контексту для безпечного редагування запису.
    Якщо під час редагування в сетерах станеться помилка (ValueError),
    зміни скасовуються (відкат до копії).
    """
    def __init__(self, ledger: 'GroupLedger', index: int):
        self.ledger = ledger
        self.index = index
        self.backup = None

    def __enter__(self):
        # Робимо глибоку копію об'єкта перед початком редагування
        self.original_expense = self.ledger[self.index]
        self.backup = copy.deepcopy(self.original_expense)
        return self.original_expense

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # Сталася помилка під час зміни атрибутів - повертаємо старий стан
            self.ledger.expenses[self.index] = self.backup
            print(f"\n [Транзакція скасована] Зміни відкочено через помилку: {exc_val}")
            return True  # Помилка оброблена успішно
        else:
            print("\n [Транзакція успішна] Зміни до запису успішно застосовано та збережено.")
            return False


# ==========================================
# 7. КОМПОЗИЦІЯ / АГРЕГАЦІЯ (Клас-Менеджер)
# ==========================================
class GroupLedger:
    def __init__(self, ledger_name: str):
        self.ledger_name = ledger_name
        self.users: Dict[str, User] = {}
        self.expenses: List[ExpenseRecord] = [] # Агрегація об'єктів
        self.db_filename = "ledger_state.pkl"

    # Магічний метод __getitem__ для пошуку за індексом
    def __getitem__(self, index: int) -> ExpenseRecord:
        if 0 <= index < len(self.expenses):
            return self.expenses[index]
        raise IndexError("Запис із таким ID не знайдено в базі!")

    def add_user(self, user: User):
        if user.username in self.users:
            raise ValueError(f"Користувач з іменем '{user.username}' вже існує!")
        self.users[user.username] = user
        print(f" [Success] Користувача '{user.username}' успішно додано.")

    def add_expense(self, expense: ExpenseRecord):
        if expense.paid_by not in self.users:
            raise ValueError(f"Платник '{expense.paid_by}' не зареєстрований у групі!")
        for u in expense.users:
            if u not in self.users:
                raise ValueError(f"Учасник витрати '{u}' не зареєстрований у групі!")
        
        self.expenses.append(expense)
        print(f" [Success] Витрату '{expense.description}' успішно додано.")

    def get_balances(self) -> Dict[str, float]:
        balances = {username: 0.0 for username in self.users}
        for exp in self.expenses:
            # Тому, хто заплатив, система "викуповує" повну суму
            if exp.paid_by in balances:
                balances[exp.paid_by] += exp.total_amount
            
            # З усіх учасників (включаючи платника) вираховується їхня частка
            splits = exp.calculate_splits()
            for user, share in splits.items():
                if user in balances:
                    balances[user] -= share
        return {user: round(bal, 2) for user, bal in balances.items()}

    # --- ПОШУК, ФІЛЬТРАЦІЯ, СОРТУВАННЯ ---
    def search_expenses(self, keyword: str) -> List[tuple]:
        """Пошук за ключовим словом в описі. Повертає кортеж (оригінальний_індекс, об'єкт)"""
        return [(i, exp) for i, exp in enumerate(self.expenses) if keyword.lower() in exp.description.lower()]

    def filter_expenses_by_payer(self, payer_username: str) -> List[ExpenseRecord]:
        """Фільтрація витрат за конкретним платником"""
        return [exp for exp in self.expenses if exp.paid_by.lower() == payer_username.lower()]

    def get_sorted_expenses(self) -> List[ExpenseRecord]:
        """Сортування витрат на основі магічного методу __lt__ (за сумою)"""
        return sorted(self.expenses)

    def display_via_iterator(self):
        """Використання кастомного ітератора для виведення даних"""
        print("\n--- Список усіх витрат (через кастомний ітератор) ---")
        iterator = ActiveExpenseIterator(self.expenses)
        has_items = False
        for i, exp in enumerate(iterator):
            has_items = True
            split_type = "Порівну" if isinstance(exp, EqualSplit) else "У відсотках"
            print(f"  ID [{i}]: [{split_type}] {exp}")
        if not has_items:
            print("  (немає записів про витрати)")

    # --- СЕРІАЛІЗАЦІЯ (Pickle & CSV) ---
    def save_to_pickle(self):
        try:
            with open(self.db_filename, "wb") as f:
                pickle.dump((self.users, self.expenses), f)
            print(f" [System] Стан системи збережено у '{self.db_filename}'.")
        except Exception as e:
            print(f" [Error] Не вдалося зберегти стан через Pickle: {e}")

    def load_from_pickle(self):
        if os.path.exists(self.db_filename):
            try:
                with open(self.db_filename, "rb") as f:
                    self.users, self.expenses = pickle.load(f)
                print(f" [System] Стан системи успішно відновлено з '{self.db_filename}'.")
            except Exception as e:
                print(f" [Warning] Помилка зчитування Pickle ({e}). Базу ініціалізовано порожньою.")
        else:
            print(" [Info] Файл збереження стану .pkl не знайдено. Створено нову групу.")

    def export_balances_csv(self):
        filename = "balances_report.csv"
        try:
            balances = self.get_balances()
            with open(filename, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Користувач", "Баланс"])
                for user, bal in balances.items():
                    writer.writerow([user, bal])
            print(f" [System] Фінансовий звіт успішно експортовано у файл '{filename}'.")
        except Exception as e:
            print(f" [Error] Не вдалося експортувати CSV: {e}")


# ==========================================
# 8. КОНСОЛЬНИЙ ІНТЕРФЕЙС (CLI)
# ==========================================
def show_menu():
    print("\n" + "="*50)
    print("      ГОЛОВНЕ МЕНЮ: SPLITWISE TRACKER PRO      ")
    print("="*50)
    print("1. Показати користувачів та поточні баланси")
    print("2. Вивести історію витрат (Кастомний ітератор)")
    print("3. Додати нового користувача")
    print("4. Додати новий запис про витрату")
    print("5. [Пошук] та [Редагування] витрати (with transaction)")
    print("6. [Фільтрація] витрат за платником")
    print("7. [Сортування] витрат за сумою")
    print("8. Зберегти стан (Pickle)")
    print("9. Експортувати звіт у CSV")
    print("0. Безпечний вихід із програми")
    print("="*50)


if __name__ == "__main__":
    # Завантаження JSON налаштувань
    config = AppConfig()
    config.load_from_json()
    config.save_to_json() # Перезапис/створення за замовчуванням якщо файлу немає

    ledger = GroupLedger(ledger_name="Вінницькі Студенти")
    ledger.load_from_pickle()
    
    print(f"\nВітаємо у трекері! Поточна JSON-тема системи: {config.theme}")
    
    # Головний життєвий цикл з глобальним перехопленням винятків
    while True:
        show_menu()
        try:
            choice = input("Оберіть дію (0-9): ").strip()
            
            match choice:
                case "1":
                    print(f"\n--- Стан трекера: '{ledger.ledger_name}' ---")
                    print(f"Зареєстровані користувачі ({len(ledger.users)}):")
                    for u in ledger.users.values():
                        print(f"  • {u}")
                    
                    print("\nПоточний фінансовий баланс:")
                    for user, bal in ledger.get_balances().items():
                        if bal < 0:
                            status = f"(борг: {abs(bal)})"
                        elif bal > 0:
                            status = f"(йому винні: {bal})"
                        else:
                            status = "(розрахувався повністю)"
                        print(f"  • {user}: {bal} {status}")
                        
                case "2":
                    ledger.display_via_iterator()
                    
                case "3":
                    print("\n--- Реєстрація нового користувача ---")
                    username = input("Введіть унікальний username: ").strip()
                    if not username:
                        raise ValueError("Ім'я користувача не може бути порожнім!")
                    email = input("Введіть email: ").strip()
                    if "@" not in email:
                        raise ValueError("Некоректний формат email (відсутній символ '@')!")
                        
                    ledger.add_user(User(username=username, email=email))
                    
                case "4":
                    print("\n--- Створення нової спільної витрати ---")
                    if not ledger.users:
                        raise ValueError("У групі немає користувачів для розподілу!")
                        
                    description = input("Опис витрати (наприклад, 'Продукти в Сільпо'): ").strip()
                    total_amount = float(input("Введіть повну суму витрати: "))
                    paid_by = input("Хто сплатив? (username): ").strip()
                    
                    print(f"Доступні валюти: {[c.value for c in Currency]}")
                    curr_input = input("Валюта: ").strip().upper()
                    if curr_input not in [c.value for c in Currency]:
                        raise ValueError("Така валюта не підтримується!")
                    currency = Currency(curr_input)
                    
                    users_input = input("Введіть імена учасників через кому (наприклад: ivan, petro): ")
                    participants = [u.strip() for u in users_input.split(",") if u.strip()]
                    if not participants:
                        raise ValueError("Список учасників не може бути порожнім!")
                    
                    print("\nТип розподілу: 1 - Порівну, 2 - У відсотках")
                    split_choice = input("Ваш вибір: ").strip()
                    
                    if split_choice == "1":
                        expense = EqualSplit(description, total_amount, paid_by, currency, participants)
                        ledger.add_expense(expense)
                    elif split_choice == "2":
                        percentages = {}
                        total_pct = 0.0
                        for p in participants:
                            pct = float(input(f"  Відсоток для {p} (%): "))
                            if pct < 0:
                                raise ValueError("Відсоток не може бути від'ємним!")
                            percentages[p] = pct
                            total_pct += pct
                        if abs(total_pct - 100.0) > 0.01:
                            raise ValueError(f"Сума часток дорівнює {total_pct}%, а має бути рівно 100%.")
                        
                        expense = PercentageSplit(description, total_amount, paid_by, currency, participants, percentages)
                        ledger.add_expense(expense)
                    else:
                        print(" [Warning] Невідомий тип розподілу!")
                            
                case "5":
                    print("\n--- Пошук та транзакційне редагування ---")
                    keyword = input("Введіть ключове слово для пошуку витрати: ").strip()
                    found = ledger.search_expenses(keyword)
                    
                    if not found:
                        print("Записів з таким ключовим словом не знайдено.")
                    else:
                        print("\nЗнайдені записи:")
                        for idx, exp in found:
                            print(f"  ID [{idx}] -> {exp}")
                        
                        edit_idx_str = input("\nВведіть ID запису для редагування (або Enter для скасування): ").strip()
                        if edit_idx_str:
                            edit_idx = int(edit_idx_str)
                            
                            # Виклик контекстного менеджера з транзакційністю
                            with ExpenseTransaction(ledger, edit_idx) as exp_to_edit:
                                print(f"\nРедагуємо запис: {exp_to_edit.description}")
                                new_desc = input("Введіть новий опис (Enter щоб залишити поточний): ").strip()
                                if new_desc:
                                    exp_to_edit.description = new_desc
                                    
                                new_amount_str = input("Введіть нову суму (Enter щоб залишити поточну): ").strip()
                                if new_amount_str:
                                    exp_to_edit.total_amount = float(new_amount_str)
                                    
                case "6":
                    print("\n--- Фільтрація витрат за платником ---")
                    payer = input("Введіть username платника: ").strip()
                    filtered = ledger.filter_expenses_by_payer(payer)
                    if not filtered:
                        print(f"Витрат, які сплатив користувач '{payer}', не знайдено.")
                    else:
                        for exp in filtered:
                            print(f"  • {exp}")
                            
                case "7":
                    print("\n--- Сортування витрат за сумою (від меншої до більшої) ---")
                    sorted_exp = ledger.get_sorted_expenses()
                    if not sorted_exp:
                        print("Немає даних для сортування.")
                    else:
                        for exp in sorted_exp:
                            print(f"  • {exp.total_amount} {exp.currency.value} — {exp.description}")
                            
                case "8":
                    ledger.save_to_pickle()
                    
                case "9":
                    ledger.export_balances_csv()
                    
                case "0":
                    print("\n[Вихід] Автоматичне збереження стану системи...")
                    ledger.save_to_pickle()
                    print("Дякуємо за використання застосунку! Успішного захисту курсової!")
                    sys.exit(0)
                    
                case _:
                    print(" [Warning] Невідома дія! Оберіть пункт від 0 до 9.")
                    
        except ValueError as e:
            print(f"\n [Помилка валідації/вводу]: {e}")
            print("Спробуйте ще раз. Зміни внесено не було.")
        except IndexError as e:
            print(f"\n [Помилка індексу]: {e}")
            print(expense.calculate_splits())
        except Exception as e:
            print(f"\n [Глобальне перехоплення помилки]: {e}")
