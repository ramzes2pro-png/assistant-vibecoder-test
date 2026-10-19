#!/usr/bin/env python3
"""Сводка CRM. Python 3.9+, только стандартная библиотека."""
import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

FIELDS = ('дата', 'имя', 'телефон', 'источник', 'бюджет')
SOURCES = {'яндекс': 'Яндекс', 'yandex': 'Яндекс', 'я.директ': 'Яндекс',
           'авито': 'Авито', 'сайт': 'сайт', 'сарафан': 'сарафан'}


def parse_date(value):
    for fmt in ('%d.%m.%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError('дата должна иметь вид ДД.ММ.ГГГГ или ГГГГ-ММ-ДД')


def phone_key(value):
    if not re.fullmatch(r'[+\d\s()\-]+', value.strip()):
        raise ValueError('неверный телефон')
    digits = re.sub(r'\D', '', value)
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    if len(digits) != 11 or not digits.startswith('7'):
        raise ValueError('ожидается телефон РФ: +7… или 8…, всего 11 цифр')
    return '+' + digits


def load_rows(path):
    rows, duplicates, seen = [], [], {}
    raw_dates = []
    with Path(path).open(encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError('ожидаются колонки: ' + ','.join(FIELDS))
        for item in reader:
            line = reader.line_num
            try:
                if None in item or any(item[k] is None for k in FIELDS):
                    raise ValueError('неверное число колонок')
                day = parse_date(item['дата'])
                phone = phone_key(item['телефон'])
                name = ' '.join(item['имя'].split())
                source_text = ' '.join(item['источник'].split())
                if not name or not source_text:
                    raise ValueError('пустое имя или источник')
                source = SOURCES.get(source_text.casefold(), source_text)
                budget_text = item['бюджет'].strip()
                budget = Decimal(budget_text) if budget_text else None
                if budget is not None and (not budget.is_finite() or budget < 0):
                    raise ValueError('бюджет должен быть конечным неотрицательным числом')
            except (ValueError, InvalidOperation) as error:
                raise ValueError(f'строка {line}: {error}') from error
            raw_dates.append(day)
            # Удаляется только полностью совпавшая нормализованная запись за ту же дату.
            key = (day, name.casefold(), phone, source.casefold(), budget)
            if key in seen:
                duplicates.append({'строка': line, 'совпала_со_строкой': seen[key]})
                continue
            seen[key] = line
            rows.append({'date': day, 'name': name, 'phone': phone,
                         'source': source, 'budget': budget})
    if not rows:
        raise ValueError('в файле нет лидов')
    return rows, duplicates, raw_dates


def mean_money(values):
    if not values:
        return None
    result = sum(values, Decimal(0)) / len(values)
    return float(result.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def summarize(rows, raw_dates, start, end):
    selected = [r for r in rows if start <= r['date'] <= end]
    positive = [r['budget'] for r in selected
                if r['budget'] is not None and r['budget'] > 0]
    nonmissing = [r['budget'] for r in selected if r['budget'] is not None]
    return {
        'с': start.isoformat(), 'по': end.isoformat(),
        'строк_до_дедупликации': sum(start <= d <= end for d in raw_dates),
        'лидов_записей': len(selected),
        'уникальных_телефонов': len({r['phone'] for r in selected}),
        'источники_по_записям': dict(sorted(Counter(r['source'] for r in selected).items())),
        'средний_положительный_бюджет': mean_money(positive),
        'бюджетов_в_основном_среднем': len(positive),
        'нулевых_бюджетов': sum(r['budget'] == 0 for r in selected),
        'пустых_бюджетов': sum(r['budget'] is None for r in selected),
        'средний_бюджет_если_учитывать_нули': mean_money(nonmissing),
    }


def build_report(path):
    rows, duplicates, raw_dates = load_rows(path)
    last = max(r['date'] for r in rows)
    dates_by_phone = {}
    for row in rows:
        dates_by_phone.setdefault(row['phone'], set()).add(row['date'])
    return {
        'методика': {
            'лид': 'Нормализованная запись; одинаковые записи в одну дату объединены.',
            'повтор_в_разные_даты': 'Сохранён: без ID лида нельзя доказать дубль.',
            'источники': 'Яндекс, yandex, Я.Директ объединены на уровне платформы.',
            'бюджет': 'Основное среднее по >0; смысл нуля неизвестен. Альтернатива с нулями показана отдельно.',
            'валюта': 'В CSV не указана; числовые единицы исходного файла, без конвертации.',
            'пустое_среднее': 'null означает отсутствие подходящих бюджетов, а не нулевое среднее.',
        },
        'контроль': {
            'входных_строк': len(raw_dates), 'оставлено_записей': len(rows),
            'удалено_дублей': len(duplicates), 'дубли': duplicates,
            'контактов_в_разные_даты': sum(len(ds) > 1 for ds in dates_by_phone.values()),
        },
        'день': summarize(rows, raw_dates, last, last),
        'неделя': summarize(rows, raw_dates, last - timedelta(days=6), last),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv_file', nargs='?', default=str(Path(__file__).resolve().parent.parent / 'данные' / 'лиды.csv'))
    args = parser.parse_args()
    try:
        report = build_report(args.csv_file)
    except (OSError, UnicodeError, ValueError, csv.Error) as error:
        print(f'Ошибка: {error}', file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
