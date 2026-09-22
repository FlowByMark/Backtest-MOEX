#!/usr/bin/env python3
import concurrent.futures
import datetime as dt
import http.server
import io
import json
import shutil
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from pathlib import Path

START = dt.date(2026, 1, 1)
END = min(dt.date.today(), dt.date(2026, 12, 31))
OUT = Path("Si_open_2026_5m.html")
CACHE_DIR = Path("_si_moex_cache")
RESEARCH_DATA = Path("Si_research_data.json")


def contract_for(day):
    if day <= dt.date(2026, 3, 19):
        return "SiH6"
    if day <= dt.date(2026, 6, 18):
        return "SiM6"
    if day <= dt.date(2026, 9, 17):
        return "SiU6"
    if day <= dt.date(2026, 12, 17):
        return "SiZ6"
    return "SiH7"


def request_page(day, contract, start):
    params = urllib.parse.urlencode({
        "from": day.isoformat(), "till": day.isoformat(), "interval": 1,
        "start": start, "iss.meta": "off", "iss.only": "candles",
        "candles.columns": "open,close,high,low,volume,begin,end",
    })
    url = ("https://iss.moex.com/iss/engines/futures/markets/forts/"
           f"boards/RFUD/securities/{contract}/candles.json?{params}")
    last_error = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=90) as response:
                return json.load(response), None
        except Exception as exc:
            last_error = str(exc)
            time.sleep(1.5 * (attempt + 1))
    return None, last_error


def fetch_day(task):
    day, contract = task
    CACHE_DIR.mkdir(exist_ok=True)
    cache = CACHE_DIR / f"{contract}_{day.isoformat()}.json"
    if cache.exists() and day < dt.date.today():
        try:
            return day.isoformat(), contract, json.loads(cache.read_text(encoding="utf-8")), None
        except Exception:
            pass
    rows, start = [], 0
    for _ in range(12):
        payload, error = request_page(day, contract, start)
        if error:
            return day.isoformat(), contract, [], error
        block = payload.get("candles", {})
        cols = block.get("columns", [])
        page = [dict(zip(cols, row)) for row in block.get("data", [])]
        if not page:
            break
        rows.extend(page)
        start += len(page)
    if day < dt.date.today():
        cache.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return day.isoformat(), contract, rows, None


def aggregate(rows, interval):
    groups = {}
    for row in sorted(rows, key=lambda item: item["begin"]):
        stamp = dt.datetime.fromisoformat(row["begin"])
        floored = stamp.replace(minute=(stamp.minute // interval) * interval, second=0)
        key = floored.isoformat(timespec="seconds")
        if key not in groups:
            groups[key] = {"ts": key, "o": row["open"], "h": row["high"],
                           "l": row["low"], "c": row["close"], "v": row["volume"]}
        else:
            bar = groups[key]
            bar["h"] = max(bar["h"], row["high"])
            bar["l"] = min(bar["l"], row["low"])
            bar["c"] = row["close"]
            bar["v"] += row["volume"]
    return [groups[key] for key in sorted(groups)]


def in_clock(row, start, end):
    clock = dt.datetime.fromisoformat(row["begin"]).time()
    return start <= clock <= end


def build_events(raw, analysis_days):
    result, available = {}, {}
    for (contract, date), rows in raw.items():
        if rows:
            available.setdefault(contract, []).append(date)
    for dates in available.values():
        dates.sort()
    for day in analysis_days:
        date, contract = day.isoformat(), contract_for(day)
        current = raw.get((contract, date), [])
        if not current:
            continue
        price_1700 = next((row["open"] for row in current
                           if dt.datetime.fromisoformat(row["begin"]).time() == dt.time(17, 0)), None)
        previous_dates = [value for value in available.get(contract, []) if value < date]
        previous_date = previous_dates[-1] if previous_dates else None
        previous = raw.get((contract, previous_date), []) if previous_date else []
        previous = [row for row in previous if in_clock(row, dt.time(18), dt.time(23, 59, 59))]
        current = [row for row in current if in_clock(row, dt.time(8, 50), dt.time(18, 50, 59))]
        combined = previous + current
        if combined:
            result[date] = {"contract": contract, "previous_date": previous_date,
                            "price_1700": price_1700,
                            "m5": aggregate(combined, 5), "m15": aggregate(combined, 15)}
    return result


HTML_TEMPLATE = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Si — исследование экстремумов у открытия 10:00</title>
<style>
:root{--bg:#090d12;--panel:#111820;--panel2:#151e28;--grid:#263341;--text:#dce7f0;--muted:#8495a6;--up:#25b991;--down:#ef5350;--accent:#f6c453;--orange:#ff8b45;--blue:#58a6ff;--purple:#c384ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,Segoe UI,Arial,sans-serif}.wrap{max-width:1850px;margin:auto;padding:14px}
.top,.tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.top{margin-bottom:10px}.tools{padding:10px;background:var(--panel);border:1px solid #202c38;border-radius:9px;margin-bottom:10px}
h1{font-size:20px;margin:0 14px 0 0}.spacer{flex:1}.muted{color:var(--muted)}button,select,input{background:var(--panel2);color:var(--text);border:1px solid #344352;border-radius:7px;padding:8px 10px;font:inherit}button{cursor:pointer}button:hover{border-color:#6c8195}button.active{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}button:disabled{opacity:.4;cursor:not-allowed}.danger{color:#ff9b95}.take{color:#66ddb4}.stop{color:#ff918b}.no-entry{color:#b6c6d5}
.workspace{display:grid;grid-template-columns:minmax(0,1fr) 355px;gap:10px}.card,.side{background:var(--panel);border:1px solid #202c38;border-radius:10px}.card{padding:8px;min-width:0}.side{padding:13px}.chart-wrap{position:relative}canvas{display:block;width:100%;height:760px}.legend{display:flex;gap:15px;color:var(--muted);padding:7px 5px 1px;flex-wrap:wrap}.sw{display:inline-block;width:14px;border-top:2px dashed;margin-right:5px;vertical-align:middle}
.side h2{font-size:16px;margin:0 0 10px}.instruction{background:#0d141b;border-left:3px solid var(--accent);padding:9px 10px;line-height:1.45;margin-bottom:12px}.selection{line-height:1.7;margin-bottom:10px}.metric-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px}.metric{background:#0d141b;border:1px solid #202c38;border-radius:7px;padding:8px}.metric b{display:block;font-size:17px;margin-top:3px}.metric span{color:var(--muted);font-size:12px}.wide{grid-column:1/-1}.form-row{margin-top:10px}.form-row label{display:block;color:var(--muted);font-size:12px;margin-bottom:4px}.form-row select,.form-row input{width:100%}.save{width:100%;margin-top:10px;background:#183b31;border-color:#286a56}.save:disabled{opacity:.4;cursor:not-allowed}.records{margin-top:12px;border-top:1px solid #263341;padding-top:10px;max-height:260px;overflow:auto}.record{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:6px;align-items:center;padding:7px;border-bottom:1px solid #202c38;font-size:12px}.record.active{background:#1a272f;border-left:3px solid var(--orange)}.record button{padding:4px 7px}.open-record{color:#b9d9ff}
.note{color:var(--muted);line-height:1.5;margin-top:10px}.warn{color:#ffad66}.pill{padding:3px 7px;border-radius:10px;background:#1a2632;font-size:12px}.status{font-weight:600}.cross-tip{position:absolute;display:none;pointer-events:none;background:#071018e8;border:1px solid #425568;border-radius:6px;padding:6px 8px;font-size:12px;white-space:nowrap}
.modal{position:fixed;inset:0;z-index:20;display:none;align-items:center;justify-content:center;padding:24px;background:#02060acc}.modal.open{display:flex}.dialog{width:min(1720px,97vw);max-height:94vh;overflow:hidden;background:var(--panel);border:1px solid #344352;border-radius:12px;box-shadow:0 20px 70px #000;display:flex;flex-direction:column}.dialog-head{display:flex;gap:10px;align-items:center;padding:14px;border-bottom:1px solid #263341}.dialog-head h2{margin:0;font-size:18px}.stats-scroll{overflow:auto}.stats-tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:12px 14px}.stats-section{padding:0 14px}.stats-section h3{font-size:15px;margin:10px 0 8px}.table-wrap{overflow:auto;padding-bottom:14px}.stats-table{width:100%;border-collapse:collapse;white-space:nowrap}.stats-table th,.stats-table td{padding:9px 10px;border:1px solid #263341;text-align:right}.stats-table th{position:sticky;top:0;background:#18222d;color:#aebdca;font-size:12px}.stats-table th:first-child,.stats-table td:first-child{text-align:left}.stats-table tr.total{background:#17231f;font-weight:700}.stats-help{margin:0 14px 14px;color:var(--muted);line-height:1.5}.stats-guide{margin:0 14px 16px;background:#0d141b;border:1px solid #263341;border-radius:8px}.stats-guide summary{cursor:pointer;padding:12px;color:var(--accent);font-weight:700}.guide-body{padding:0 12px 12px}.guide-table{width:100%;border-collapse:collapse}.guide-table td{border-top:1px solid #263341;padding:8px;vertical-align:top;line-height:1.4}.guide-table td:first-child{width:230px;color:#dbe8f2;font-weight:600}.close{font-size:20px;line-height:1;padding:6px 10px}
@media(max-width:1050px){.workspace{grid-template-columns:1fr}.side{order:-1}canvas{height:620px}}@media(max-width:650px){canvas{height:520px}.spacer{display:none}}
</style></head><body><div class="wrap">
<div class="top"><h1>Si: исследование снятия экстремумов <span class="muted">v0.6.0</span></h1><button id="prev">← День</button><select id="date"></select><button id="next">День →</button><span class="pill" id="dayStats"></span><span class="spacer"></span><button id="statsOpen">Статистика</button><button id="projectExport">Экспорт проекта ZIP</button><button id="import">Импорт CSV</button><input id="importFile" type="file" accept=".csv,text/csv" hidden><button id="export">Экспорт разметки CSV</button></div>
<div class="tools"><span class="muted">График:</span><button class="tf active" data-tf="m5">M5</button><button class="tf" data-tf="m15">M15</button><select id="view"><option value="full">18:00 пред. дня → 18:50</option><option value="context">21:00 пред. дня → 13:00</option><option value="open">08:50 → 13:00</option></select><button id="pivots" class="active">Подсказки экстремумов</button><button id="savedMarks" class="active">Сохранённая разметка</button><span class="muted">Разметка:</span><button id="pickHigh">Выбрать HIGH</button><button id="pickLow">Выбрать LOW</button><button id="pickSweep">Указать снятие</button><button id="pickResweep" class="stop">Указать пересвип (СТОП)</button><button id="markTake" class="take">ТЕЙК / без пересвипа</button><button id="markNoEntry" class="no-entry">Нет входа в день</button><button id="reset">Сбросить выбор</button></div>
<div class="workspace"><div class="card"><div class="chart-wrap"><canvas id="chart"></canvas><div class="cross-tip" id="tip"></div></div><div class="legend"><span><i class="sw" style="border-color:#f6c453"></i>10:00</span><span><i class="sw" style="border-color:#ff8b45"></i>активный уровень</span><span><i class="sw" style="border-color:#58a6ff"></i>сохранённый HIGH</span><span><i class="sw" style="border-color:#c384ff"></i>сохранённый LOW</span><span>▲/▼ предполагаемые неснятые экстремумы</span></div></div>
<aside class="side"><h2>Разметка события</h2><div class="instruction" id="instruction">Нажми «Выбрать HIGH» или «Выбрать LOW», затем кликни по нужной свече.</div><div class="selection" id="selection">Уровень пока не выбран.</div><div class="metric-grid" id="metrics"></div><div class="form-row"><label>Классификация</label><select id="classification"><option value="auto">Определить автоматически</option><option value="fast_reversal">Снятие + быстрый возврат</option><option value="delayed_reversal">Снятие + поздний возврат</option><option value="breakout">Пробой / продолжение</option><option value="multiple">Снятие нескольких уровней</option><option value="no_touch">Уровень не достигнут</option><option value="ambiguous">Неоднозначный случай</option></select></div><div class="form-row"><label>Комментарий</label><input id="notes" placeholder="Почему выбрал уровень, наблюдения"></div><button id="save" class="save" disabled>Сохранить размеченный случай</button><div class="records"><b>Сохранённые случаи: <span id="recordCount">0</span></b><div id="recordList"></div></div></aside></div>
<div class="note">Источник: MOEX ISS, минутные свечи RFUD; графики агрегированы в M5 и M15. Подсказки — подтверждённые локальные pivots (по две свечи слева и справа), которые не были повторно пробиты до 10:00. Окончательный уровень выбираешь ты.</div><div class="note warn" id="errors"></div></div>
<div class="modal" id="statsModal"><div class="dialog"><div class="dialog-head"><h2>Статистика размеченных случаев</h2><span class="spacer"></span><button class="close" id="statsClose">×</button></div><div class="stats-scroll">
<div class="stats-tools"><span class="muted">Направление:</span><select id="statsKind"><option value="all">Все</option><option value="high">HIGH</option><option value="low">LOW</option></select><span class="muted">Таймфрейм:</span><select id="statsTf"><option value="all">Все</option><option value="m5">M5</option><option value="m15">M15</option></select><span class="muted">Возврат:</span><select id="statsReturn"><option value="all">Все случаи</option><option value="returned">Был возврат</option><option value="not_returned">Возврата не было</option></select><span class="spacer"></span><button id="exportStats">Экспорт статистики CSV</button></div>
<section class="stats-section"><h3>Исходы модели</h3><div class="table-wrap"><table class="stats-table"><thead><tr><th>Месяц</th><th>Отмечено дней</th><th>Дней без входа</th><th>Тейков</th><th>Стопов</th><th>Без исхода</th><th>Тейк среди исходов</th><th>Ср. макс. ход до стопа</th></tr></thead><tbody id="statsOutcomes"></tbody></table></div></section>
<section class="stats-section"><h3>Основные показатели сетапов</h3><div class="table-wrap"><table class="stats-table"><thead><tr><th>Месяц</th><th>Дней</th><th>Сетапов</th><th>HIGH</th><th>LOW</th><th>M5</th><th>M15</th><th>Возврат</th><th>Ср. до уровня</th><th>Ср. возраст уровня</th><th>Ср. время за уровнем</th><th>Ср. прокол</th><th>Медиана</th><th>P75</th><th>P90</th><th>Макс. прокол</th><th>Ср. ход</th><th>Медиана ход/прокол</th></tr></thead><tbody id="statsMain"></tbody></table></div></section>
<section class="stats-section"><h3>Максимально доступное движение после возврата</h3><div class="table-wrap"><table class="stats-table"><thead><tr><th>Месяц</th><th>Ср. макс. ход 15м</th><th>Ср. макс. ход 30м</th><th>Ср. макс. ход 60м</th><th>Ср. макс. ход до 18:50</th><th>Медиана макс. хода</th><th>Ср. макс. ход до пересвипа</th></tr></thead><tbody id="statsMoves"></tbody></table></div></section>
<section class="stats-section"><h3>Результат в фиксированный момент</h3><div class="table-wrap"><table class="stats-table"><thead><tr><th>Месяц</th><th>Ср. результат 15м</th><th>Медиана 15м</th><th>Плюс 15м</th><th>Ср. результат 30м</th><th>Медиана 30м</th><th>Плюс 30м</th><th>Ср. результат 60м</th><th>Медиана 60м</th><th>Плюс 60м</th><th>Среднее к 17:00</th><th>Плюс к 17:00</th></tr></thead><tbody id="statsResults"></tbody></table></div></section>
<section class="stats-section"><h3>Время снятия</h3><div class="table-wrap"><table class="stats-table"><thead><tr><th>Месяц</th><th>Ср. минут после 10:00</th><th>Снятие ≤15м</th><th>Снятие ≤30м</th><th>Снятие ≤60м</th></tr></thead><tbody id="statsTiming"></tbody></table></div></section>
<section class="stats-section"><h3>Какой стоп переживал прокол</h3><div class="table-wrap"><table class="stats-table"><thead><tr><th>Месяц</th><th>Стоп 25 п.</th><th>Стоп 50 п.</th><th>Стоп 75 п.</th><th>Стоп 100 п.</th></tr></thead><tbody id="statsStops"></tbody></table></div></section>
<details class="stats-guide"><summary>Инструкция: что означает каждый показатель</summary><div class="guide-body"><table class="guide-table">
<tr><td>Дней / Случаев</td><td>Количество уникальных торговых дней и общее количество сохранённых разметок. Один день может содержать несколько случаев.</td></tr>
<tr><td>Нет входа</td><td>День просмотрен, но подходящего сетапа по твоей модели не было. Такая отметка входит в охват дней, но не участвует в расчётах цены, прокола и движения.</td></tr>
<tr><td>Тейк</td><td>Ручная отметка «без пересвипа до 18:50». Это пока не фиксированный тейк в пунктах, а объективный исход: стоп-уровень свечи снятия больше не был пересечён.</td></tr>
<tr><td>Стоп / пересвип</td><td>После возврата цена снова пересекла экстремум исходной M5-свечи снятия: для HIGH — её high, для LOW — её low.</td></tr>
<tr><td>Без исхода</td><td>Сохранённые сетапы, которым ещё не назначен ТЕЙК или СТОП. Старые разметки остаются здесь и не подменяются тейками автоматически.</td></tr>
<tr><td>Тейк среди исходов</td><td>Тейки / (тейки + стопы). Дни без входа и сетапы без исхода в знаменатель не входят.</td></tr>
<tr><td>Ср. макс. ход до стопа</td><td>Для стоповых случаев: средний лучший ход от уровня после возврата и до свечи пересвипа. Сама M5-свеча пересвипа исключена, потому что порядок high и low внутри неё неизвестен.</td></tr>
<tr><td>HIGH / LOW, M5 / M15</td><td>Сколько уровней каждого направления и таймфрейма вошло в расчёт.</td></tr>
<tr><td>Возврат</td><td>Доля снятий, после которых свеча M5 закрылась обратно за выбранным уровнем. Для HIGH — ниже уровня, для LOW — выше.</td></tr>
<tr><td>Ср. до уровня</td><td>Среднее расстояние в пунктах от цены открытия свечи 10:00 до выбранного уровня.</td></tr>
<tr><td>Ср. возраст уровня</td><td>Среднее время от формирования выбранного экстремума до его снятия.</td></tr>
<tr><td>Ср. время за уровнем</td><td>Среднее число минут от начала свечи снятия до закрытия M5 обратно за уровнем.</td></tr>
<tr><td>Прокол</td><td>Максимальное расстояние за уровень до подтверждённого возврата. Это неблагоприятное движение для входа непосредственно от уровня.</td></tr>
<tr><td>Средний прокол</td><td>Среднее арифметическое всех проколов в выбранной группе.</td></tr>
<tr><td>Медиана прокола</td><td>Половина проколов была меньше этого значения, половина — больше. Меньше зависит от единичных выбросов, чем среднее.</td></tr>
<tr><td>P75 / P90</td><td>75% / 90% проколов не превышали это значение. Помогает оценивать стоп, но не гарантирует результат в будущем.</td></tr>
<tr><td>Макс. прокол</td><td>Самый большой прокол внутри выбранной выборки.</td></tr>
<tr><td>Ср. ход</td><td>Средний максимальный ход от уровня в противоположную снятию сторону после возврата и до 18:50.</td></tr>
<tr><td>Медиана ход/прокол</td><td>Медианное отношение максимального хода к проколу. Например 5 означает: ход был в 5 раз больше прокола.</td></tr>
<tr><td>Ср. макс. ход 15/30/60м</td><td>Средний лучший ход от уровня внутри первых 15, 30 или 60 минут после подтверждённого возврата. Это потенциальный максимум, а не гарантированная цена выхода.</td></tr>
<tr><td>Ср. макс. ход до 18:50</td><td>Среднее максимального доступного хода каждого сетапа от уровня после возврата и до конца отображаемой сессии 18:50. То же семейство расчёта, что «Ход от уровня» в карточке события.</td></tr>
<tr><td>Медиана макс. хода</td><td>Половина сетапов дала максимальный ход меньше этого значения, половина — больше. Устойчива к единичным очень большим движениям.</td></tr>
<tr><td>Ср. результат 15/30/60м</td><td>Средний результат от уровня по цене закрытия ровно через 15, 30 или 60 минут после возврата. Плюс — цена в стороне разворота, минус — за уровнем.</td></tr>
<tr><td>Медиана результата 15/30/60м</td><td>Середина распределения результата в соответствующий момент. Половина результатов ниже, половина выше.</td></tr>
<tr><td>Плюс 15/30/60м</td><td>Доля сетапов, где направленный результат в соответствующий момент был строго больше нуля.</td></tr>
<tr><td>Среднее к 17:00</td><td>Средний направленный результат от уровня до цены открытия минутной свечи 17:00.</td></tr>
<tr><td>Плюс к 17:00</td><td>Доля случаев, где направленный результат к 17:00 был строго больше нуля.</td></tr>
<tr><td>Ср. минут после 10:00</td><td>Среднее время от 10:00 до свечи, на которой произошло снятие.</td></tr>
<tr><td>Снятие ≤15/30/60м</td><td>Доля снятий, произошедших не позднее указанного количества минут после 10:00.</td></tr>
<tr><td>Стоп 25/50/75/100 п.</td><td>Доля случаев, где прокол был строго меньше размера стопа и такой стоп не был бы задет.</td></tr>
<tr><td>ИТОГО</td><td>Расчёт по всем случаям, прошедшим выбранные фильтры, а не среднее из месячных строк.</td></tr>
<tr><td>«—»</td><td>Для показателя нет подходящих наблюдений. Такие пустые значения не подменяются нулями и не входят в среднее.</td></tr>
</table><p class="muted">Все расчёты строятся только по сохранённой ручной разметке. Это исследовательская статистика, а не прогноз и не торговая рекомендация.</p></div></details>
<div class="stats-help">Для HIGH положительное движение считается вниз от уровня, для LOW — вверх от уровня. Все средние считаются по отдельным событиям, для которых конкретный показатель доступен.</div></div></div></div>
<script>
const DATA=__DATA__; const ERRORS=__ERRORS__; const STORAGE_KEY='si-extremum-research-v2';
const dates=Object.keys(DATA).filter(d=>DATA[d].m5.length).sort(), $=id=>document.getElementById(id), sel=$('date'), cv=$('chart'), ctx=cv.getContext('2d'), tip=$('tip');
let records=[];
function localRecords(){try{const value=JSON.parse(localStorage.getItem(STORAGE_KEY)||'[]');return Array.isArray(value)?value:[]}catch(e){return[]}}
async function persistRecords(){try{localStorage.setItem(STORAGE_KEY,JSON.stringify(records))}catch(e){}if(location.protocol.startsWith('http')){const response=await fetch('/api/records',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(records)});if(!response.ok)throw new Error('HTTP '+response.status)}}
function enrichRecords(){let changed=false;records.forEach(r=>{const values=recordMetrics(r);if(!values)return;Object.entries(values).forEach(([key,value])=>{const normalized=value??'';if(String(r[key]??'')!==String(normalized)){r[key]=normalized;changed=true}})});return changed}
async function loadRecords(){const local=localRecords();if(location.protocol.startsWith('http')){try{const response=await fetch('/api/records');if(!response.ok)throw new Error('HTTP '+response.status);const saved=await response.json();records=Array.isArray(saved)&&saved.length?saved:local;const changed=enrichRecords();if((!saved.length&&local.length)||changed)await persistRecords()}catch(e){records=local;enrichRecords();$('errors').textContent='Не удалось открыть файл разметки. Временно используется сохранение браузера.'}}else{records=local;enrichRecords()}renderRecords();draw()}
let state={tf:'m5',view:'full',showPivots:true,showSaved:true,mode:null,kind:null,level:null,levelTs:null,levelTf:null,sweepTs:null,resweepTs:null,outcome:null,recordId:null,hover:-1},layout=null;
dates.forEach(d=>{const o=document.createElement('option');o.value=d;o.textContent=d+' · '+DATA[d].contract;sel.appendChild(o)});sel.value=dates[dates.length-1]||'';
const fmt=v=>v==null||v===''?'—':Math.round(Number(v)).toLocaleString('ru-RU'),signed=v=>v==null||v===''?'—':(Number(v)>0?'+':'')+fmt(v)+' п.',timeOf=ts=>ts?ts.slice(11,16):'—',dateOf=ts=>ts?ts.slice(0,10):'',stampMs=ts=>Date.parse(ts+'Z'),minutesBetween=(a,b)=>(stampMs(b)-stampMs(a))/60000,esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function fullBars(tf=state.tf){return sel.value?DATA[sel.value][tf]:[]}
function visibleBars(){const d=sel.value,p=DATA[d].previous_date;return fullBars().filter(b=>{const bd=dateOf(b.ts),t=timeOf(b.ts);if(state.view==='full')return true;if(state.view==='context')return(bd===p&&t>='21:00')||(bd===d&&t<='13:00');return bd===d&&t>='08:50'&&t<='13:00'})}
function pivotCandidates(){const bars=fullBars(),cut=sel.value+'T10:00:00',pre=bars.filter(b=>b.ts<cut),out=[];for(let i=2;i<pre.length-2;i++){const b=pre[i],hi=b.h>pre[i-1].h&&b.h>pre[i-2].h&&b.h>pre[i+1].h&&b.h>pre[i+2].h,lo=b.l<pre[i-1].l&&b.l<pre[i-2].l&&b.l<pre[i+1].l&&b.l<pre[i+2].l;if(hi&&!pre.slice(i+3).some(x=>x.h>=b.h))out.push({kind:'high',level:b.h,ts:b.ts});if(lo&&!pre.slice(i+3).some(x=>x.l<=b.l))out.push({kind:'low',level:b.l,ts:b.ts})}const o10=DATA[sel.value].m5.find(b=>b.ts>=cut);if(o10){const hs=out.filter(x=>x.kind==='high'&&x.level>=o10.o).sort((a,b)=>a.level-b.level),ls=out.filter(x=>x.kind==='low'&&x.level<=o10.o).sort((a,b)=>b.level-a.level);if(hs[0])hs[0].nearest=true;if(ls[0])ls[0].nearest=true}return out}
function setMode(mode){state.mode=mode;['pickHigh','pickLow','pickSweep','pickResweep'].forEach(id=>$(id).classList.remove('active'));if(mode==='high')$('pickHigh').classList.add('active');if(mode==='low')$('pickLow').classList.add('active');if(mode==='sweep')$('pickSweep').classList.add('active');if(mode==='resweep')$('pickResweep').classList.add('active');$('instruction').textContent=mode==='high'?'Кликни по свече выбранного максимума.':mode==='low'?'Кликни по свече выбранного минимума.':mode==='sweep'?'Кликни по свече, на которой произошло снятие.':mode==='resweep'?'Кликни по более поздней свече, которая повторно пересекла экстремум исходной M5-свечи снятия.':'Выбери HIGH или LOW и кликни по нужной свече.'}
function resetSelection(){state.kind=null;state.level=null;state.levelTs=null;state.levelTf=null;state.sweepTs=null;state.resweepTs=null;state.outcome=null;state.recordId=null;state.hover=-1;$('notes').value='';$('classification').value='auto';$('save').textContent='Сохранить размеченный случай';setMode(null);updatePanel();draw();renderRecords()}
function autoSweep(){if(state.level==null)return;const cut=sel.value+'T10:00:00',bars=DATA[sel.value].m5,found=bars.find(b=>b.ts>=cut&&(state.kind==='high'?b.h>state.level:b.l<state.level));state.sweepTs=found?found.ts:null}
const directional=(kind,level,price)=>kind==='high'?level-price:price-level;
function metricsFor(mark){
 const day=DATA[mark.date],level=Number(mark.level);if(!day||!Number.isFinite(level))return null;
 const bars=day.m5,cut=mark.date+'T10:00:00',o10=bars.find(b=>b.ts>=cut),si=mark.sweepTs?bars.findIndex(b=>b.ts===mark.sweepTs):-1,price1700=day.price_1700,result1700=mark.sweepTs&&timeOf(mark.sweepTs)<'17:00'&&price1700!=null?directional(mark.kind,level,price1700):null,age=mark.levelTs&&mark.sweepTs?minutesBetween(mark.levelTs,mark.sweepTs):null,sweepAfter=mark.sweepTs?minutesBetween(cut,mark.sweepTs):null,base={open10:o10?.o,distance:o10?Math.abs(level-o10.o):null,price1700,result1700,age,sweepAfter};
 if(si<0)return{...base,status:'no_touch'};
 const sweep=bars[si],stopLevel=mark.kind==='high'?sweep.h:sweep.l;
 let ri=-1;for(let i=si;i<bars.length;i++){if(dateOf(bars[i].ts)!==mark.date)continue;if(mark.kind==='high'?bars[i].c<level:bars[i].c>level){ri=i;break}}
 const end=ri>=0?ri:bars.length-1,segment=bars.slice(si,end+1).filter(b=>dateOf(b.ts)===mark.date),rawDepth=mark.kind==='high'?Math.max(...segment.map(b=>b.h))-level:level-Math.min(...segment.map(b=>b.l)),depth=Math.max(0,rawDepth);
 if(ri<0)return{...base,status:'breakout',depth,duration:null,sweep,stopLevel};
 const re=bars[ri],future=bars.slice(ri+1).filter(b=>dateOf(b.ts)===mark.date&&timeOf(b.ts)<='18:50'),after=future.length?future:[{ts:re.ts,h:re.c,l:re.c,c:re.c}],favorable=part=>part.length?Math.max(0,mark.kind==='high'?level-Math.min(...part.map(b=>b.l)):Math.max(...part.map(b=>b.h))-level):0,opposite=favorable(after),fromReturn=Math.max(0,mark.kind==='high'?re.c-Math.min(...after.map(b=>b.l)):Math.max(...after.map(b=>b.h))-re.c),reClose=stampMs(re.ts)+5*60000,windowBars=min=>future.filter(b=>stampMs(b.ts)+5*60000<=reClose+min*60000),moveAt=min=>{const part=windowBars(min);return part.length?favorable(part):null},resultAt=min=>{const part=windowBars(min);return part.length?directional(mark.kind,level,part[part.length-1].c):null},rsi=mark.resweepTs?bars.findIndex(b=>b.ts===mark.resweepTs):-1,resweep=rsi>ri&&(mark.kind==='high'?bars[rsi].h>stopLevel:bars[rsi].l<stopLevel)?bars[rsi]:null,beforeResweep=resweep?future.filter(b=>stampMs(b.ts)<stampMs(resweep.ts)):[],maxBeforeResweep=resweep?favorable(beforeResweep):null,duration=minutesBetween(sweep.ts,re.ts)+5;
 return{...base,status:duration<=15?'fast_reversal':'delayed_reversal',depth,duration,sweep,stopLevel,reentry:re,resweep,opposite,fromReturn,maxBeforeResweep,m15:moveAt(15),m30:moveAt(30),m60:moveAt(60),r15:resultAt(15),r30:resultAt(30),r60:resultAt(60)}
}
function computeMetrics(){return state.level==null?null:metricsFor({date:sel.value,kind:state.kind,level:state.level,levelTs:state.levelTs,sweepTs:state.sweepTs,resweepTs:state.resweepTs})}
function recordMetrics(r){const m=metricsFor({date:r.date,kind:r.kind,level:r.level,levelTs:r.extremum_time,sweepTs:r.sweep_time,resweepTs:r.resweep_time});if(!m)return null;return{open_1000:m.open10??'',distance_to_level:m.distance??'',penetration:m.depth??'',reentry_time:m.reentry?.ts||'',minutes_beyond:m.duration??'',move_from_level:m.opposite??'',move_from_return:m.fromReturn??'',move_15m:m.m15??'',move_30m:m.m30??'',move_60m:m.m60??'',result_15m:m.r15??'',result_30m:m.r30??'',result_60m:m.r60??'',price_1700:m.price1700??'',result_1700:m.result1700??'',extremum_age_minutes:m.age??'',sweep_after_open_minutes:m.sweepAfter??'',move_penetration_ratio:m.depth>0&&m.opposite!=null?m.opposite/m.depth:'',stop_level:m.stopLevel??'',max_move_before_resweep:m.maxBeforeResweep??''}}
const statusText={fast_reversal:'Снятие + быстрый возврат',delayed_reversal:'Снятие + поздний возврат',breakout:'Пробой / возврата нет',no_touch:'Уровень не достигнут',multiple:'Несколько уровней',ambiguous:'Неоднозначно',no_entry:'Нет входа в этот день'};
const outcomeText={take:'ТЕЙК · без пересвипа до 18:50',resweep_stop:'СТОП · пересвип',no_entry:'Нет входа в этот день',unclassified:'Исход ещё не указан'};
function currentOutcome(){return state.outcome||'unclassified'}
function updatePanel(){const m=computeMetrics(),noEntry=state.outcome==='no_entry';$('save').disabled=state.level==null&&!noEntry;$('save').textContent=state.recordId?(noEntry?'Обновить отметку дня':'Обновить размеченный случай'):(noEntry?'Сохранить день без входа':'Сохранить размеченный случай');$('pickResweep').disabled=!(m&&state.sweepTs&&m.reentry);$('markTake').disabled=!(m&&state.sweepTs&&m.reentry);$('selection').innerHTML=noEntry?'<b>НЕТ ВХОДА</b><br><span class="muted">В этот день сетап по модели отсутствовал.</span>':state.level==null?'Уровень пока не выбран.':`<b>${esc(state.kind.toUpperCase())} ${fmt(state.level)}</b> · ${esc((state.levelTf||'m5').toUpperCase())}<br><span class="muted">Экстремум: ${esc((state.levelTs||'').replace('T',' '))}</span><br><span class="muted">Снятие: ${state.sweepTs?esc(state.sweepTs.replace('T',' ')):'не найдено / не указано'}</span><br><span class="muted">Исход: ${esc(outcomeText[currentOutcome()])}${state.resweepTs?' · '+esc(state.resweepTs.replace('T',' ')):''}</span>`;if(noEntry){$('metrics').innerHTML=`<div class="metric wide status"><span>Исход дня</span><b>${outcomeText.no_entry}</b></div>`;return}if(!m){$('metrics').innerHTML='';return}const items=[['Статус снятия',statusText[m.status]||m.status,'wide status'],['Исход модели',outcomeText[currentOutcome()],'wide status'],['До уровня',fmt(m.distance)+' п.',''],['Прокол',fmt(m.depth)+' п.',''],['Стоп за свечой снятия',fmt(m.stopLevel)+' п.',''],['Возраст уровня',m.age!=null?fmt(m.age)+' мин':'—',''],['Снятие после 10:00',m.sweepAfter!=null?fmt(m.sweepAfter)+' мин':'—',''],['Возврат за уровень',m.reentry?timeOf(m.reentry.ts):'—',''],['Время за уровнем',m.duration!=null?fmt(m.duration)+' мин':'—',''],['Пересвип / стоп',m.resweep?timeOf(m.resweep.ts):'—',''],['Макс. ход до стопа',m.maxBeforeResweep!=null?fmt(m.maxBeforeResweep)+' п.':'—',''],['Ход от уровня',fmt(m.opposite)+' п.',''],['Ход от возврата',fmt(m.fromReturn)+' п.',''],['Макс. ход 15 мин',fmt(m.m15)+' п.',''],['Результат 15 мин',signed(m.r15),''],['Макс. ход 30 мин',fmt(m.m30)+' п.',''],['Результат 30 мин',signed(m.r30),''],['Макс. ход 60 мин',fmt(m.m60)+' п.',''],['Результат 60 мин',signed(m.r60),''],['Цена в 17:00',fmt(m.price1700),''],['Результат к 17:00',signed(m.result1700),'']];$('metrics').innerHTML=items.map(x=>`<div class="metric ${x[2]}"><span>${x[0]}</span><b>${x[1]}</b></div>`).join('')}
function line(x1,y1,x2,y2,c,w=1,dash=[]){ctx.beginPath();ctx.setLineDash(dash);ctx.strokeStyle=c;ctx.lineWidth=w;ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.stroke();ctx.setLineDash([])}
function triangle(x,y,kind,color,size){ctx.beginPath();ctx.fillStyle=color;if(kind==='high'){ctx.moveTo(x,y-size);ctx.lineTo(x-size,y);ctx.lineTo(x+size,y)}else{ctx.moveTo(x,y+size);ctx.lineTo(x-size,y);ctx.lineTo(x+size,y)}ctx.closePath();ctx.fill()}
function crossMark(x,y,size=7){line(x-size,y-size,x+size,y+size,'#ff6b63',2);line(x-size,y+size,x+size,y-size,'#ff6b63',2)}
function indexForTs(bars,ts){if(!ts)return-1;const target=stampMs(ts),span=(state.tf==='m15'?15:5)*60000;return bars.findIndex(b=>{const start=stampMs(b.ts);return target>=start&&target<start+span})}
function priceTag(yy,value,color,active=false){const text=fmt(value),w=Math.max(48,ctx.measureText(text).width+12),left=Math.max(2,layout.m.l-w-5);ctx.fillStyle=color;ctx.fillRect(left,yy-10,w,20);ctx.fillStyle=active?'#081018':'#ffffff';ctx.textAlign='center';ctx.fillText(text,left+w/2,yy+4)}
function drawRecordMark(rec,bars){const level=Number(rec.level);if(!Number.isFinite(level))return;const color=rec.kind==='high'?'rgba(88,166,255,.72)':'rgba(195,132,255,.72)',yy=layout.y(level);line(layout.m.l,yy,layout.W-layout.m.r,yy,color,1,[5,5]);priceTag(yy,level,color);const li=indexForTs(bars,rec.extremum_time);if(li>=0)triangle(layout.x(li),rec.kind==='high'?yy-7:yy+7,rec.kind,color,6);const si=indexForTs(bars,rec.sweep_time);if(si>=0){ctx.beginPath();ctx.arc(layout.x(si),rec.kind==='high'?layout.y(bars[si].h)-6:layout.y(bars[si].l)+6,4,0,Math.PI*2);ctx.fillStyle=color;ctx.fill()}const rsi=indexForTs(bars,rec.resweep_time);if(rsi>=0)crossMark(layout.x(rsi),rec.kind==='high'?layout.y(bars[rsi].h)-9:layout.y(bars[rsi].l)+9)}
function draw(){if(!sel.value)return;const bars=visibleBars(),daySaved=records.filter(r=>r.date===sel.value),saved=state.showSaved?daySaved:[],r=cv.getBoundingClientRect(),W=r.width,H=r.height,m={l:78,r:18,t:18,b:44},cw=W-m.l-m.r,ch=H-m.t-m.b;if(!bars.length)return;let lows=bars.map(b=>b.l),highs=bars.map(b=>b.h);saved.forEach(mark=>{const level=Number(mark.level);if(Number.isFinite(level)){lows.push(level);highs.push(level)}});if(state.level!=null){lows.push(state.level);highs.push(state.level)}let lo=Math.min(...lows),hi=Math.max(...highs),pad=Math.max(5,(hi-lo)*.06),mn=lo-pad,mx=hi+pad;const y=v=>m.t+(mx-v)/(mx-mn)*ch,x=i=>m.l+(i+.5)*cw/bars.length;layout={bars,x,y,m,W,H,cw,ch};ctx.clearRect(0,0,W,H);ctx.font='12px system-ui';ctx.fillStyle='#8495a6';ctx.textAlign='right';for(let i=0;i<=7;i++){const v=mx-(mx-mn)*i/7,yy=m.t+ch*i/7;line(m.l,yy,W-m.r,yy,'#263341');ctx.fillText(fmt(v),m.l-8,yy+4)}const labelStep=Math.max(1,Math.ceil(bars.length/12));bars.forEach((b,i)=>{const xx=x(i),bw=Math.max(2,cw/bars.length*.64),up=b.c>=b.o,c=up?'#25b991':'#ef5350';line(xx,y(b.h),xx,y(b.l),c,1);ctx.fillStyle=c;const yt=y(Math.max(b.o,b.c)),yb=y(Math.min(b.o,b.c));ctx.fillRect(xx-bw/2,yt,bw,Math.max(1,yb-yt));if(i%labelStep===0){ctx.fillStyle='#8495a6';ctx.textAlign='center';ctx.fillText((dateOf(b.ts)===sel.value?'':dateOf(b.ts).slice(5)+' ')+timeOf(b.ts),xx,H-17)}});const oi=bars.findIndex(b=>b.ts>=sel.value+'T10:00:00');if(oi>=0&&dateOf(bars[oi].ts)===sel.value){const xx=x(oi)-cw/bars.length/2;line(xx,m.t,xx,H-m.b,'#f6c453',2,[5,5]);ctx.fillStyle='#f6c453';ctx.textAlign='left';ctx.fillText('10:00',xx+5,m.t+14)}if(state.showPivots){pivotCandidates().forEach(p=>{const i=indexForTs(bars,p.ts);if(i<0)return;triangle(x(i),p.kind==='high'?y(p.level)-5:y(p.level)+5,p.kind,p.nearest?'#f6c453':p.kind==='high'?'#58a6ff':'#c384ff',p.nearest?7:5)})}saved.filter(mark=>String(mark.id)!==String(state.recordId)).forEach(mark=>drawRecordMark(mark,bars));if(state.level!=null){const yy=y(state.level);line(m.l,yy,W-m.r,yy,'#ff8b45',2,[8,4]);priceTag(yy,state.level,'#ff8b45',true);const li=indexForTs(bars,state.levelTs);if(li>=0)triangle(x(li),state.kind==='high'?yy-8:yy+8,state.kind,'#ff8b45',8);const si=indexForTs(bars,state.sweepTs);if(si>=0){ctx.beginPath();ctx.arc(x(si),state.kind==='high'?y(bars[si].h)-7:y(bars[si].l)+7,6,0,Math.PI*2);ctx.fillStyle='#ff8b45';ctx.fill()}}if(state.hover>=0&&state.hover<bars.length){const b=bars[state.hover],xx=x(state.hover),yy=y((b.h+b.l)/2);line(xx,m.t,xx,H-m.b,'#708090',1,[3,4]);line(m.l,yy,W-m.r,yy,'#708090',1,[3,4])}const ps=pivotCandidates(),o10=DATA[sel.value].m5.find(b=>b.ts>=sel.value+'T10:00:00'),nh=ps.find(p=>p.nearest&&p.kind==='high'),nl=ps.find(p=>p.nearest&&p.kind==='low');$('dayStats').textContent=`${DATA[sel.value].contract} · 10:00 ${o10?fmt(o10.o):'—'} · ближайшие: H ${nh?fmt(nh.level):'—'} / L ${nl?fmt(nl.level):'—'} · сохранено ${daySaved.length}`;updatePanel()}
const drawBase=draw;draw=function(){drawBase();if(state.level!=null&&state.resweepTs&&layout){const bars=layout.bars,i=indexForTs(bars,state.resweepTs);if(i>=0)crossMark(layout.x(i),state.kind==='high'?layout.y(bars[i].h)-10:layout.y(bars[i].l)+10,8)}};
function fit(){const r=cv.getBoundingClientRect(),d=devicePixelRatio||1;cv.width=r.width*d;cv.height=r.height*d;ctx.setTransform(d,0,0,d,0,0);draw()}function pointerIndex(e){if(!layout)return-1;const r=cv.getBoundingClientRect(),px=e.clientX-r.left,i=Math.floor((px-layout.m.l)/layout.cw*layout.bars.length);return Math.max(0,Math.min(layout.bars.length-1,i))}
cv.addEventListener('mousemove',e=>{state.hover=pointerIndex(e);if(state.hover<0)return;const b=layout.bars[state.hover],r=cv.getBoundingClientRect();tip.style.display='block';tip.style.left=Math.min(e.clientX-r.left+12,r.width-205)+'px';tip.style.top=Math.max(4,e.clientY-r.top-52)+'px';tip.textContent=`${b.ts.replace('T',' ')}  O ${fmt(b.o)} H ${fmt(b.h)} L ${fmt(b.l)} C ${fmt(b.c)}`;draw()});cv.addEventListener('mouseleave',()=>{state.hover=-1;tip.style.display='none';draw()});
cv.addEventListener('click',e=>{
 const i=pointerIndex(e);if(i<0||!state.mode)return;const b=layout.bars[i];
 if(state.mode==='high'||state.mode==='low'){
  if(records.some(r=>r.date===sel.value&&r.outcome==='no_entry')){alert('Этот день отмечен как «нет входа». Сначала удали эту отметку в списке справа.');return}
  if(b.ts>=sel.value+'T10:00:00'){alert('Экстремум должен быть сформирован до 10:00 текущего дня.');return}
  state.recordId=null;state.outcome=null;state.resweepTs=null;$('notes').value='';$('classification').value='auto';state.kind=state.mode;state.level=state.mode==='high'?b.h:b.l;state.levelTs=b.ts;state.levelTf=state.tf;autoSweep();setMode(null)
 }else if(state.mode==='sweep'&&state.level!=null){
  const base=DATA[sel.value].m5,clicked=stampMs(b.ts),span=state.tf==='m15'?15:5,candidates=base.filter(x=>{const t=stampMs(x.ts);return t>=clicked&&t<clicked+span*60000}),hit=candidates.find(x=>state.kind==='high'?x.h>state.level:x.l<state.level);
  if(!hit){alert('На выбранной свече цена не пересекает отмеченный уровень.');return}
  state.sweepTs=hit.ts;state.resweepTs=null;state.outcome=null;setMode(null)
 }else if(state.mode==='resweep'&&state.level!=null){
  const m=computeMetrics();if(!m?.reentry||!m.sweep){alert('Сначала укажи снятие и дождись возврата M5 за выбранный уровень.');setMode(null);return}
  const base=DATA[sel.value].m5,clicked=stampMs(b.ts),span=state.tf==='m15'?15:5,candidates=base.filter(x=>{const t=stampMs(x.ts);return t>=clicked&&t<clicked+span*60000&&t>stampMs(m.reentry.ts)}),hit=candidates.find(x=>state.kind==='high'?x.h>m.stopLevel:x.l<m.stopLevel);
  if(!hit){alert(`На выбранной свече нет пересвипа стоп-уровня ${fmt(m.stopLevel)}.`);return}
  state.resweepTs=hit.ts;state.outcome='resweep_stop';setMode(null)
 }
 updatePanel();draw()
});
async function markNoEntryDay(){
 const setups=records.filter(r=>r.date===sel.value&&r.outcome!=='no_entry');if(setups.length){alert('В этот день уже сохранён сетап. Сначала удали его, если день действительно был без входа.');return}
 const existing=records.find(r=>r.date===sel.value&&r.outcome==='no_entry');if(existing){openRecord(existing.id);$('instruction').textContent='Этот день уже отмечен как день без входа.';return}
 if(!confirm(`Отметить ${sel.value}: входа по модели не было?`))return;
 const record={id:'no-entry-'+sel.value,date:sel.value,contract:DATA[sel.value].contract,outcome:'no_entry',classification:'no_entry',notes:$('notes').value||''};records.push(record);state.kind=null;state.level=null;state.levelTs=null;state.levelTf=null;state.sweepTs=null;state.resweepTs=null;state.outcome='no_entry';state.recordId=record.id;setMode(null);renderRecords();draw();try{await persistRecords();$('instruction').textContent='День без входа сохранён.'}catch(e){alert('Отметка сохранена в браузере, но файл Si_research_data.json сейчас недоступен.')}
}
function move(n){let i=dates.indexOf(sel.value);i=Math.max(0,Math.min(dates.length-1,i+n));sel.value=dates[i];resetSelection()}
sel.onchange=resetSelection;$('prev').onclick=()=>move(-1);$('next').onclick=()=>move(1);document.addEventListener('keydown',e=>{if(e.target.matches('input,select'))return;if(e.key==='ArrowLeft')move(-1);if(e.key==='ArrowRight')move(1)});document.querySelectorAll('.tf').forEach(b=>b.onclick=()=>{state.tf=b.dataset.tf;document.querySelectorAll('.tf').forEach(x=>x.classList.toggle('active',x.dataset.tf===state.tf));draw()});$('view').onchange=e=>{state.view=e.target.value;draw()};$('pivots').onclick=()=>{state.showPivots=!state.showPivots;$('pivots').classList.toggle('active',state.showPivots);draw()};$('savedMarks').onclick=()=>{state.showSaved=!state.showSaved;$('savedMarks').classList.toggle('active',state.showSaved);draw()};$('pickHigh').onclick=()=>setMode('high');$('pickLow').onclick=()=>setMode('low');$('pickSweep').onclick=()=>state.level!=null&&setMode('sweep');$('pickResweep').onclick=()=>{const m=computeMetrics();if(m?.reentry)setMode('resweep')};$('markTake').onclick=()=>{const m=computeMetrics();if(!m?.reentry)return;const dayBars=DATA[sel.value].m5.filter(b=>dateOf(b.ts)===sel.value);if(!dayBars.some(b=>timeOf(b.ts)>='18:45')){alert('Нельзя подтвердить отсутствие пересвипа: данные этого дня ещё не дошли до конца сессии.');return}const later=dayBars.filter(b=>stampMs(b.ts)>stampMs(m.reentry.ts)&&timeOf(b.ts)<='18:50'),hit=later.find(b=>state.kind==='high'?b.h>m.stopLevel:b.l<m.stopLevel);if(hit){alert(`После возврата есть пересвип в ${timeOf(hit.ts)}. Отметь его кнопкой «Указать пересвип (СТОП)».`);return}state.outcome='take';state.resweepTs=null;setMode(null);updatePanel();draw()};$('markNoEntry').onclick=markNoEntryDay;$('reset').onclick=resetSelection;
function openRecord(id){
 const r=records.find(x=>String(x.id)===String(id));if(!r||!DATA[r.date])return;sel.value=r.date;state.recordId=r.id;state.outcome=r.outcome||null;state.resweepTs=r.resweep_time||null;$('notes').value=r.notes||'';
 if(r.outcome==='no_entry'){state.kind=null;state.level=null;state.levelTs=null;state.levelTf=null;state.sweepTs=null;$('classification').value='auto'}else{state.tf=r.timeframe==='m15'?'m15':'m5';document.querySelectorAll('.tf').forEach(x=>x.classList.toggle('active',x.dataset.tf===state.tf));state.kind=r.kind;state.level=Number(r.level);state.levelTs=r.extremum_time;state.levelTf=r.timeframe||'m5';state.sweepTs=r.sweep_time||null;$('classification').value=[...$('classification').options].some(o=>o.value===r.classification)?r.classification:'auto'}
 setMode(null);renderRecords();draw()
}
$('save').onclick=async()=>{
 const noEntry=state.outcome==='no_entry',m=computeMetrics();if(!noEntry&&!m)return;
 const id=state.recordId||Date.now().toString(36)+Math.random().toString(36).slice(2,7),index=records.findIndex(r=>String(r.id)===String(id)),prior=index>=0?records[index]:{};
 let record;
 if(noEntry){record={...prior,id,date:sel.value,contract:DATA[sel.value].contract,outcome:'no_entry',classification:'no_entry',notes:$('notes').value}}
 else{const chosen=$('classification').value==='auto'?m.status:$('classification').value;record={...prior,id,date:sel.value,contract:DATA[sel.value].contract,timeframe:state.levelTf,kind:state.kind,level:state.level,extremum_time:state.levelTs,sweep_time:state.sweepTs||'',resweep_time:state.resweepTs||'',outcome:state.outcome||'',classification:chosen,open_1000:m.open10??'',distance_to_level:m.distance??'',penetration:m.depth??'',reentry_time:m.reentry?.ts||'',minutes_beyond:m.duration??'',move_from_level:m.opposite??'',move_from_return:m.fromReturn??'',move_15m:m.m15??'',move_30m:m.m30??'',move_60m:m.m60??'',result_15m:m.r15??'',result_30m:m.r30??'',result_60m:m.r60??'',price_1700:m.price1700??'',result_1700:m.result1700??'',extremum_age_minutes:m.age??'',sweep_after_open_minutes:m.sweepAfter??'',move_penetration_ratio:m.depth>0&&m.opposite!=null?m.opposite/m.depth:'',stop_level:m.stopLevel??'',max_move_before_resweep:m.maxBeforeResweep??'',notes:$('notes').value}}
 if(index>=0)records[index]=record;else records.push(record);state.recordId=id;renderRecords();draw();try{await persistRecords();$('instruction').textContent=index>=0?'Сохранённая разметка обновлена.':noEntry?'День без входа сохранён.':'Случай сохранён. Если исход ещё не ясен, позже открой его и отметь ТЕЙК или пересвип.'}catch(e){alert('Разметка сохранена в браузере, но файл Si_research_data.json сейчас недоступен. Не закрывай программу и попробуй сохранить ещё раз.')}
};
function renderRecords(){$('recordCount').textContent=records.length;const day=records.filter(r=>r.date===sel.value).slice().reverse();$('recordList').innerHTML=day.length?day.map(r=>{const noEntry=r.outcome==='no_entry',title=noEntry?'ДЕНЬ БЕЗ ВХОДА':`${esc(String(r.timeframe||'').toUpperCase())} ${esc(String(r.kind||'').toUpperCase())} ${fmt(r.level)}`,detail=noEntry?outcomeText.no_entry:`${outcomeText[r.outcome||'unclassified']} · ${statusText[r.classification]||r.classification||'без классификации'}`;return`<div class="record ${String(r.id)===String(state.recordId)?'active':''}"><span>${title}<br><span class="muted">${esc(detail)}</span></span><button class="open-record" data-open="${esc(r.id)}">Открыть</button><button class="danger" data-del="${esc(r.id)}">Удалить</button></div>`}).join(''):'<div class="muted" style="margin-top:8px">В этот день разметки нет.</div>';document.querySelectorAll('[data-open]').forEach(b=>b.onclick=()=>openRecord(b.dataset.open));document.querySelectorAll('[data-del]').forEach(b=>b.onclick=async()=>{if(confirm('Удалить эту разметку?')){const active=String(state.recordId)===String(b.dataset.del);records=records.filter(r=>String(r.id)!==String(b.dataset.del));if(active)resetSelection();else{renderRecords();draw()}try{await persistRecords()}catch(e){alert('Не удалось обновить файл разметки. Копия в браузере обновлена.')}}});if($('statsModal').classList.contains('open'))renderStats()}
function csvCell(v){const s=String(v??'');return '"'+s.replaceAll('"','""')+'"'}
function downloadCsv(name,cols,rows){const text='\ufeff'+cols.join(';')+'\r\n'+rows.map(r=>r.map(csvCell).join(';')).join('\r\n'),a=document.createElement('a'),url=URL.createObjectURL(new Blob([text],{type:'text/csv;charset=utf-8'}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
const detailCols=['date','contract','outcome','timeframe','kind','level','extremum_time','sweep_time','resweep_time','stop_level','classification','open_1000','distance_to_level','penetration','reentry_time','minutes_beyond','extremum_age_minutes','sweep_after_open_minutes','move_from_level','move_from_return','max_move_before_resweep','move_penetration_ratio','move_15m','move_30m','move_60m','result_15m','result_30m','result_60m','price_1700','result_1700','notes'];
$('export').onclick=()=>{if(!records.length){alert('Сначала сохрани хотя бы один случай.');return}downloadCsv('Si_extremum_research_2026.csv',detailCols,records.map(r=>detailCols.map(c=>r[c]??'')))};
$('projectExport').onclick=()=>{if(!location.protocol.startsWith('http')){alert('Для экспорта проекта запусти программу через START_WINDOWS.bat.');return}location.href='/api/export-project'};
function parseCsv(text){const rows=[],row=[];let cell='',quoted=false;for(let i=0;i<text.length;i++){const c=text[i];if(quoted){if(c==='"'&&text[i+1]==='"'){cell+='"';i++}else if(c==='"')quoted=false;else cell+=c}else if(c==='"')quoted=true;else if(c===';'){row.push(cell);cell=''}else if(c==='\n'){row.push(cell.replace(/\r$/,''));if(row.some(v=>v!==''))rows.push(row.splice(0));else row.length=0;cell=''}else cell+=c}row.push(cell.replace(/\r$/,''));if(row.some(v=>v!==''))rows.push(row);return rows}
function recordKey(r){return r.outcome==='no_entry'?[r.date,'no_entry'].join('|'):[r.date,r.timeframe,r.kind,r.level,r.extremum_time,r.sweep_time].join('|')}
$('import').onclick=()=>$('importFile').click();
$('importFile').onchange=async e=>{const file=e.target.files[0];if(!file)return;try{const rows=parseCsv(await file.text());if(rows.length<2)throw new Error('В CSV нет строк данных');const headers=rows[0].map((v,i)=>i===0?v.replace(/^\ufeff/,'').trim():v.trim()),numeric=new Set(['level','stop_level','open_1000','distance_to_level','penetration','minutes_beyond','extremum_age_minutes','sweep_after_open_minutes','move_from_level','move_from_return','max_move_before_resweep','move_penetration_ratio','move_15m','move_30m','move_60m','result_15m','result_30m','result_60m','price_1700','result_1700']),incoming=rows.slice(1).map((values,index)=>{const r={};headers.forEach((h,i)=>{let v=values[i]??'';if(numeric.has(h)&&v!==''){const n=Number(String(v).replace(/\s/g,'').replace(',','.'));v=Number.isFinite(n)?n:''}r[h]=v});r.id=r.id||Date.now().toString(36)+index.toString(36)+Math.random().toString(36).slice(2,5);return r}).filter(r=>r.date&&(r.outcome==='no_entry'||(r.kind&&r.level!=='')));const keys=new Set(records.map(recordKey));let added=0;incoming.forEach(r=>{const key=recordKey(r);if(!keys.has(key)){records.push(r);keys.add(key);added++}});enrichRecords();await persistRecords();renderRecords();draw();alert(`Импортировано новых случаев: ${added}. Всего сохранено: ${records.length}.`)}catch(err){alert('Не удалось импортировать CSV: '+err.message)}finally{e.target.value=''}};
const nums=(items,key)=>items.filter(r=>r[key]!==null&&r[key]!==''&&r[key]!==undefined).map(r=>Number(r[key])).filter(Number.isFinite);
const avg=a=>a.length?a.reduce((s,v)=>s+v,0)/a.length:null;
function quantile(a,q){if(!a.length)return null;const s=a.slice().sort((x,y)=>x-y),p=(s.length-1)*q,i=Math.floor(p),f=p-i;return s[i]+((s[i+1]??s[i])-s[i])*f}
const percentage=(a,predicate)=>a.length?a.filter(predicate).length/a.length*100:null,maxValue=a=>a.length?Math.max(...a):null;
function statsRecords(){const returnFilter=$('statsReturn').value,kind=$('statsKind').value,tf=$('statsTf').value;return records.filter(r=>{if(r.outcome==='no_entry')return kind==='all'&&tf==='all'&&returnFilter==='all';return(kind==='all'||r.kind===kind)&&(tf==='all'||r.timeframe===tf)&&(returnFilter==='all'||(returnFilter==='returned'&&r.reentry_time)||(returnFilter==='not_returned'&&r.sweep_time&&!r.reentry_time))})}
function buildStats(){
 const source=statsRecords(),months=[...new Set(source.map(r=>String(r.date).slice(0,7)))].sort(),groups=months.map(month=>({month,items:source.filter(r=>String(r.date).startsWith(month))}));if(source.length)groups.push({month:'ИТОГО',items:source,total:true});
 return groups.map(g=>{
  const setups=g.items.filter(r=>r.outcome!=='no_entry'&&r.kind),noEntries=g.items.filter(r=>r.outcome==='no_entry'),takes=setups.filter(r=>r.outcome==='take'),stops=setups.filter(r=>r.outcome==='resweep_stop'),unclassified=setups.filter(r=>r.outcome!=='take'&&r.outcome!=='resweep_stop'),p=nums(setups,'penetration'),moves=nums(setups,'move_from_level'),beforeStop=nums(stops,'max_move_before_resweep'),ratios=nums(setups,'move_penetration_ratio'),distance=nums(setups,'distance_to_level'),age=nums(setups,'extremum_age_minutes'),duration=nums(setups,'minutes_beyond'),m15=nums(setups,'move_15m'),m30=nums(setups,'move_30m'),m60=nums(setups,'move_60m'),r15=nums(setups,'result_15m'),r30=nums(setups,'result_30m'),r60=nums(setups,'result_60m'),r17=nums(setups,'result_1700'),sweep=nums(setups,'sweep_after_open_minutes').filter(v=>v>=0),swept=setups.filter(r=>r.sweep_time),returned=swept.filter(r=>r.reentry_time),classified=takes.length+stops.length;
  return{month:g.month,total:g.total,marked_days:new Set(g.items.map(r=>r.date)).size,days:new Set(setups.map(r=>r.date)).size,no_entry_days:new Set(noEntries.map(r=>r.date)).size,events:setups.length,takes:takes.length,stops:stops.length,unclassified:unclassified.length,take_rate:classified?takes.length/classified*100:null,avg_before_stop:avg(beforeStop),high:setups.filter(r=>r.kind==='high').length,low:setups.filter(r=>r.kind==='low').length,m5:setups.filter(r=>r.timeframe==='m5').length,m15:setups.filter(r=>r.timeframe==='m15').length,return_pct:swept.length?returned.length/swept.length*100:null,avg_distance:avg(distance),avg_age:avg(age),avg_duration:avg(duration),avg_pen:avg(p),median_pen:quantile(p,.5),p75:quantile(p,.75),p90:quantile(p,.9),max_pen:maxValue(p),avg_move:avg(moves),median_move:quantile(moves,.5),median_ratio:quantile(ratios,.5),avg_m15:avg(m15),avg_m30:avg(m30),avg_m60:avg(m60),avg_r15:avg(r15),median_r15:quantile(r15,.5),positive_15:percentage(r15,v=>v>0),avg_r30:avg(r30),median_r30:quantile(r30,.5),positive_30:percentage(r30,v=>v>0),avg_r60:avg(r60),median_r60:quantile(r60,.5),positive_60:percentage(r60,v=>v>0),avg_17:avg(r17),positive_17:percentage(r17,v=>v>0),avg_sweep:avg(sweep),sweep_15:percentage(sweep,v=>v<=15),sweep_30:percentage(sweep,v=>v<=30),sweep_60:percentage(sweep,v=>v<=60),stop_25:percentage(p,v=>v<25),stop_50:percentage(p,v=>v<50),stop_75:percentage(p,v=>v<75),stop_100:percentage(p,v=>v<100)}
 })
}
const one=v=>v==null?'—':Number(v).toLocaleString('ru-RU',{maximumFractionDigits:1}),oneSigned=v=>v==null?'—':(Number(v)>0?'+':'')+one(v),pct=v=>v==null?'—':one(v)+'%',emptyRow=cols=>`<tr><td colspan="${cols}" style="text-align:center" class="muted">По выбранному фильтру пока нет размеченных случаев.</td></tr>`;
function renderStats(){const rows=buildStats(),wrap=(r,cells)=>`<tr class="${r.total?'total':''}"><td>${r.month}</td>${cells}</tr>`;$('statsOutcomes').innerHTML=rows.length?rows.map(r=>wrap(r,`<td>${r.marked_days}</td><td>${r.no_entry_days}</td><td>${r.takes}</td><td>${r.stops}</td><td>${r.unclassified}</td><td>${pct(r.take_rate)}</td><td>${one(r.avg_before_stop)}</td>`)).join(''):emptyRow(8);$('statsMain').innerHTML=rows.length?rows.map(r=>wrap(r,`<td>${r.days}</td><td>${r.events}</td><td>${r.high}</td><td>${r.low}</td><td>${r.m5}</td><td>${r.m15}</td><td>${pct(r.return_pct)}</td><td>${one(r.avg_distance)}</td><td>${one(r.avg_age)}</td><td>${one(r.avg_duration)}</td><td>${one(r.avg_pen)}</td><td>${one(r.median_pen)}</td><td>${one(r.p75)}</td><td>${one(r.p90)}</td><td>${one(r.max_pen)}</td><td>${one(r.avg_move)}</td><td>${one(r.median_ratio)}</td>`)).join(''):emptyRow(18);$('statsMoves').innerHTML=rows.length?rows.map(r=>wrap(r,`<td>${one(r.avg_m15)}</td><td>${one(r.avg_m30)}</td><td>${one(r.avg_m60)}</td><td>${one(r.avg_move)}</td><td>${one(r.median_move)}</td><td>${one(r.avg_before_stop)}</td>`)).join(''):emptyRow(7);$('statsResults').innerHTML=rows.length?rows.map(r=>wrap(r,`<td>${oneSigned(r.avg_r15)}</td><td>${oneSigned(r.median_r15)}</td><td>${pct(r.positive_15)}</td><td>${oneSigned(r.avg_r30)}</td><td>${oneSigned(r.median_r30)}</td><td>${pct(r.positive_30)}</td><td>${oneSigned(r.avg_r60)}</td><td>${oneSigned(r.median_r60)}</td><td>${pct(r.positive_60)}</td><td>${oneSigned(r.avg_17)}</td><td>${pct(r.positive_17)}</td>`)).join(''):emptyRow(12);$('statsTiming').innerHTML=rows.length?rows.map(r=>wrap(r,`<td>${one(r.avg_sweep)}</td><td>${pct(r.sweep_15)}</td><td>${pct(r.sweep_30)}</td><td>${pct(r.sweep_60)}</td>`)).join(''):emptyRow(5);$('statsStops').innerHTML=rows.length?rows.map(r=>wrap(r,`<td>${pct(r.stop_25)}</td><td>${pct(r.stop_50)}</td><td>${pct(r.stop_75)}</td><td>${pct(r.stop_100)}</td>`)).join(''):emptyRow(5)}
$('statsOpen').onclick=()=>{$('statsModal').classList.add('open');renderStats()};$('statsClose').onclick=()=>$('statsModal').classList.remove('open');$('statsModal').onclick=e=>{if(e.target===$('statsModal'))$('statsModal').classList.remove('open')};$('statsKind').onchange=renderStats;$('statsTf').onchange=renderStats;$('statsReturn').onchange=renderStats;
$('exportStats').onclick=()=>{const rows=buildStats();if(!rows.length){alert('Для выбранного фильтра данных нет.');return}const cols=['month','marked_days','setup_days','no_entry_days','events','takes','stops','unclassified','take_rate_pct','avg_max_move_before_stop','high','low','m5','m15','return_pct','avg_distance_to_level','avg_extremum_age_minutes','avg_minutes_beyond','avg_penetration','median_penetration','p75_penetration','p90_penetration','max_penetration','avg_move_from_level','median_move_from_level','median_move_penetration_ratio','avg_max_move_15m','avg_max_move_30m','avg_max_move_60m','avg_result_15m','median_result_15m','positive_15m_pct','avg_result_30m','median_result_30m','positive_30m_pct','avg_result_60m','median_result_60m','positive_60m_pct','avg_result_1700','positive_1700_pct','avg_sweep_after_open_minutes','sweep_within_15m_pct','sweep_within_30m_pct','sweep_within_60m_pct','stop_25_survival_pct','stop_50_survival_pct','stop_75_survival_pct','stop_100_survival_pct'];downloadCsv('Si_monthly_statistics_2026.csv',cols,rows.map(r=>{const values={month:r.month,marked_days:r.marked_days,setup_days:r.days,no_entry_days:r.no_entry_days,events:r.events,takes:r.takes,stops:r.stops,unclassified:r.unclassified,take_rate_pct:r.take_rate,avg_max_move_before_stop:r.avg_before_stop,high:r.high,low:r.low,m5:r.m5,m15:r.m15,return_pct:r.return_pct,avg_distance_to_level:r.avg_distance,avg_extremum_age_minutes:r.avg_age,avg_minutes_beyond:r.avg_duration,avg_penetration:r.avg_pen,median_penetration:r.median_pen,p75_penetration:r.p75,p90_penetration:r.p90,max_penetration:r.max_pen,avg_move_from_level:r.avg_move,median_move_from_level:r.median_move,median_move_penetration_ratio:r.median_ratio,avg_max_move_15m:r.avg_m15,avg_max_move_30m:r.avg_m30,avg_max_move_60m:r.avg_m60,avg_result_15m:r.avg_r15,median_result_15m:r.median_r15,positive_15m_pct:r.positive_15,avg_result_30m:r.avg_r30,median_result_30m:r.median_r30,positive_30m_pct:r.positive_30,avg_result_60m:r.avg_r60,median_result_60m:r.median_r60,positive_60m_pct:r.positive_60,avg_result_1700:r.avg_17,positive_1700_pct:r.positive_17,avg_sweep_after_open_minutes:r.avg_sweep,sweep_within_15m_pct:r.sweep_15,sweep_within_30m_pct:r.sweep_30,sweep_within_60m_pct:r.sweep_60,stop_25_survival_pct:r.stop_25,stop_50_survival_pct:r.stop_50,stop_75_survival_pct:r.stop_75,stop_100_survival_pct:r.stop_100};return cols.map(c=>values[c]??'')}) )};
$('errors').textContent=ERRORS.length?`Не удалось загрузить ${ERRORS.length} наборов данных. Повторный запуск попробует получить их снова.`:'';addEventListener('resize',fit);loadRecords();fit();
</script></body></html>'''


def build_html(data, errors):
    return (HTML_TEMPLATE
            .replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
            .replace("__ERRORS__", json.dumps(errors, ensure_ascii=False, separators=(",", ":"))))


def main():
    analysis_days, day = [], START
    while day <= END:
        if day.weekday() < 5:
            analysis_days.append(day)
        day += dt.timedelta(days=1)
    tasks = set()
    for day in analysis_days:
        contract = contract_for(day)
        tasks.add((day, contract))
        for offset in range(1, 9):
            prior = day - dt.timedelta(days=offset)
            if prior.weekday() < 5:
                tasks.add((prior, contract))
    raw, errors = {}, []
    ordered_tasks = sorted(tasks, key=lambda item: (item[0], item[1]))
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch_day, task) for task in ordered_tasks]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            date, contract, rows, error = future.result()
            raw[(contract, date)] = rows
            if error:
                errors.append(f"{contract}:{date}")
            if index % 20 == 0 or index == len(futures):
                print(f"processed {index}/{len(futures)}", flush=True)
    data = build_events(raw, analysis_days)
    OUT.write_text(build_html(data, sorted(errors)), encoding="utf-8")
    print(json.dumps({"days": len(data), "errors": len(errors),
                      "output": str(OUT), "bytes": OUT.stat().st_size}))


class ResearchHandler(http.server.SimpleHTTPRequestHandler):
    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body, content_type, filename):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def project_zip(self):
        data = RESEARCH_DATA.read_bytes() if RESEARCH_DATA.exists() else b"[]"
        readme = (
            "Исследование Si за 2026 год\n\n"
            "1. Распакуйте все файлы из архива в одну папку.\n"
            "2. Запустите START_WINDOWS.bat.\n"
            "3. Не закрывайте чёрное окно, пока работаете с графиком.\n"
            "4. Вся сохранённая разметка находится в Si_research_data.json.\n\n"
            "Исходы размечаются вручную: ТЕЙК (без пересвипа до 18:50), "
            "СТОП (пересвип экстремума M5-свечи снятия) или день без входа.\n"
            "Старые случаи без выбранного исхода остаются без классификации и "
            "не входят в процент тейков.\n\n"
            "Для обновления графика нужны Python 3 и подключение к интернету.\n"
        ).encode("utf-8")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for source in (Path(__file__).resolve(), Path("START_WINDOWS.bat").resolve(), OUT.resolve()):
                if source.exists():
                    archive.write(source, source.name)
            archive.writestr("Si_research_data.json", data)
            archive.writestr("README.txt", readme)
        return buffer.getvalue()

    def do_GET(self):
        request_path = urllib.parse.urlparse(self.path).path
        if request_path == "/api/export-project":
            try:
                self.send_bytes(self.project_zip(), "application/zip", "Si_research_project_2026.zip")
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return
        if request_path == "/api/records":
            try:
                if RESEARCH_DATA.exists():
                    payload = json.loads(RESEARCH_DATA.read_text(encoding="utf-8"))
                    if not isinstance(payload, list):
                        raise ValueError("research data must be a list")
                else:
                    payload = []
                self.send_json(payload)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return
        super().do_GET()

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/api/records":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 10_000_000:
                raise ValueError("request is too large")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, list):
                raise ValueError("research data must be a list")
            temporary = RESEARCH_DATA.with_name(RESEARCH_DATA.name + ".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            if RESEARCH_DATA.exists():
                shutil.copy2(RESEARCH_DATA, RESEARCH_DATA.with_name(RESEARCH_DATA.name + ".bak"))
            temporary.replace(RESEARCH_DATA)
            self.send_json({"saved": len(payload)})
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def log_message(self, format, *args):
        if args and str(args[1]) not in {"200", "304"}:
            super().log_message(format, *args)


def run_server():
    server = None
    port = None
    for candidate in range(8765, 8775):
        try:
            server = http.server.ThreadingHTTPServer(("127.0.0.1", candidate), ResearchHandler)
            port = candidate
            break
        except OSError:
            continue
    if server is None:
        raise RuntimeError("no free local port between 8765 and 8774")
    url = f"http://127.0.0.1:{port}/{urllib.parse.quote(OUT.name)}"
    print(f"Research chart: {url}")
    print("Keep this window open. Press Ctrl+C here when you finish.")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Research chart stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
    if "--serve" in sys.argv:
        run_server()
