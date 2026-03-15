import io
import logging
from datetime import datetime, timedelta
from typing import Tuple

try:
    import matplotlib
    matplotlib.use('Agg')  # Для работы без GUI
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    logging.warning("matplotlib не установлен. Графики недоступны.")

from bot.core.database import (
    db_get_token_usage_by_event,
    db_get_token_usage_by_team,
    db_get_duration_by_hour,
    db_get_duration_by_20min,
    db_get_reports_by_team,
    db_get_top_employees_by_tokens,
    db_get_team_member_counts,
    db_get_failed_requests,
    db_get_team_settings,
    db_get_requests_count_by_hour,
    db_get_token_usage_by_day,
    db_get_total_teams_count,
    db_get_total_members_count,
    db_get_active_members_count,
    db_get_attempts_statistics,
)


def format_tokens(num: int) -> str:
    """Форматирует число токенов для удобного отображения"""
    if num >= 1_000_000:
        return f"{num / 1_000_000:.2f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.2f}K"
    return str(num)


def calculate_llm_costs(input_tokens: int, output_tokens: int) -> dict:
    """
    Рассчитывает стоимость токенов для разных LLM моделей в рублях
    
    Args:
        input_tokens: Количество входных токенов
        output_tokens: Количество выходных токенов
    
    Returns:
        Словарь с ценами для каждой модели в рублях
    """
    # Курс валют: 1 USD = 100 RUB (можно изменить при необходимости)
    USD_TO_RUB = 100.0
    
    # Цены за 1 миллион токенов
    # Для моделей, изначально в долларах, указаны цены в USD, которые будут переведены в рубли
    models = {
        'GPT 5 mini': {
            'input_price_per_m_usd': 0.25,
            'output_price_per_m_usd': 2.0,
        },
        'Gemini 2.5 Flash': {
            'input_price_per_m_usd': 0.30,
            'output_price_per_m_usd': 2.50,
        },
        'Grok 4 Fast': {
            'input_price_per_m_usd': 0.20,
            'output_price_per_m_usd': 0.50,
        },
        'Qwen 2.5-72B': {
            'input_price_per_m_usd': 0.07,
            'output_price_per_m_usd': 0.26,
        },
        'DeepSeek Chat V3': {
            'input_price_per_m_usd': 0.24,
            'output_price_per_m_usd': 0.84,
        },
        'Mistral Nemo': {
            'input_price_per_m_usd': 0.02,
            'output_price_per_m_usd': 0.04,
        },
        'YandexGPT Lite': {
            'price_per_1000_rub': 0.20,  # За 1000 токенов (всего)
            'per_1000': True
        },
        'YandexGPT Pro 5': {
            'price_per_1000_rub': 1.20,  # За 1000 токенов (всего)
            'per_1000': True
        }
    }
    
    costs = {}
    for model_name, prices in models.items():
        if prices.get('per_1000'):
            # Для Yandex моделей цена указана за 1000 токенов всего (без разделения input/output)
            total_tokens = input_tokens + output_tokens
            cost_rub = (total_tokens / 1000) * prices['price_per_1000_rub']
        else:
            # Для остальных моделей отдельные цены для input и output (переводим из USD в RUB)
            input_cost_usd = (input_tokens / 1_000_000) * prices['input_price_per_m_usd']
            output_cost_usd = (output_tokens / 1_000_000) * prices['output_price_per_m_usd']
            cost_rub = (input_cost_usd + output_cost_usd) * USD_TO_RUB
        
        costs[model_name] = cost_rub
    
    return costs


async def generate_token_report(days: int = 7) -> Tuple[str, io.BytesIO | None]:
    """
    Генерирует полный отчет по использованию токенов
    
    Args:
        days: Количество дней для анализа (по умолчанию 7)
    
    Returns:
        Tuple[text_report, unified_chart]
    
    Raises:
        Exception: При ошибке получения данных из БД
    """
    # Вычисляем период (ориентируемся на дни, а не часы)
    # Начало периода: начало дня (days-1) дней назад
    # Конец периода: конец текущего дня
    end_date = datetime.utcnow()
    # Берём начало дня (days-1) дней назад, чтобы получить ровно days дней включительно
    start_date = (end_date - timedelta(days=days-1)).replace(hour=0, minute=0, second=0, microsecond=0)
    # Конец периода - конец текущего дня
    end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    start_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
    end_str = end_date.strftime('%Y-%m-%d %H:%M:%S')
    
    logging.info(f"Генерация отчета за период: {start_str} - {end_str}")
    
    try:
        # Получаем данные
        event_stats = await db_get_token_usage_by_event(start_str, end_str)
        team_stats = await db_get_token_usage_by_team(start_str, end_str)
        duration_by_hour = await db_get_duration_by_hour(start_str, end_str)
        duration_by_20min = await db_get_duration_by_20min(start_str, end_str)
        requests_count_by_hour = await db_get_requests_count_by_hour(start_str, end_str)
        reports_by_team = await db_get_reports_by_team(start_str, end_str)
        top_employees = await db_get_top_employees_by_tokens(start_str, end_str, limit=10)
        team_member_counts = await db_get_team_member_counts()
        team_settings = await db_get_team_settings()
        failed_requests = await db_get_failed_requests(start_str, end_str)
        tokens_by_day = await db_get_token_usage_by_day(start_str, end_str)
        attempts_statistics = await db_get_attempts_statistics(start_str, end_str)
        
        # Получаем новые данные для отчёта
        total_teams_count = await db_get_total_teams_count()
        active_teams_count = len(reports_by_team) if reports_by_team else 0
        total_members_count = await db_get_total_members_count()
        active_members_count = await db_get_active_members_count(start_str, end_str)
        
        # Подсчёты для расширенного текста
        total_reports = sum((t.get('report_count', 0) or 0) for t in (reports_by_team or []))
        total_attempts = sum((h.get('total_attempts', 0) or 0) for h in (requests_count_by_hour or []))
        requests_total = sum((h.get('requests_count', 0) or 0) for h in (requests_count_by_hour or []))
        avg_attempts = (total_attempts / requests_total) if requests_total else 0.0
        failed_count = len(failed_requests or [])

        # Генерируем отчет
        text_report = generate_text_report(
            event_stats, team_stats, reports_by_team, top_employees, failed_requests,
            start_str, end_str, duration_by_hour,
            total_teams_count=total_teams_count,
            active_teams_count=active_teams_count,
            total_members_count=total_members_count,
            active_members_count=active_members_count,
            total_reports=total_reports,
            avg_attempts=avg_attempts,
            failed_count=failed_count,
            attempts_statistics=attempts_statistics
        )
        
        # Генерируем один объединенный график
        unified_chart = generate_unified_chart(
            event_stats, team_stats, reports_by_team, top_employees, 
            requests_count_by_hour, tokens_by_day,
            duration_by_hour, duration_by_20min, team_member_counts, team_settings, days, start_str, end_str
        )
        
        return text_report, unified_chart
    except Exception as e:
        logging.error(f"Ошибка при генерации отчета по токенам: {e}", exc_info=True)
        raise


def generate_text_report(
    event_stats: list,
    team_stats: list,
    reports_by_team: list,
    top_employees: list,
    failed_requests: list,
    start_date: str,
    end_date: str,
    duration_by_hour: list = None,
    total_teams_count: int = 0,
    active_teams_count: int = 0,
    total_members_count: int = 0,
    active_members_count: int = 0,
    total_reports: int = 0,
    avg_attempts: float = 0.0,
    failed_count: int = 0,
    attempts_statistics: dict = None,
) -> str:
    report = []
    
    # Заголовок
    report.append(f"<b>ОТЧЁТ ПО ТОКЕНАМ LLM</b>")
    report.append(f"Период: {start_date[:10]} - {end_date[:10]}")
    # Подсчитываем общую статистику
    total_input = sum(stat.get('total_input_tokens', 0) or 0 for stat in event_stats)
    total_output = sum(stat.get('total_output_tokens', 0) or 0 for stat in event_stats)
    total_tokens = sum(stat.get('total_tokens_sum', 0) or 0 for stat in event_stats)
    total_requests = sum(stat.get('request_count', 0) or 0 for stat in event_stats)
    
    # Подсчитываем запросы по событиям
    requests_by_daily_summary = sum(stat.get('request_count', 0) or 0 for stat in event_stats if stat.get('event') == 'daily_summary')
    requests_by_clarifying = sum(stat.get('request_count', 0) or 0 for stat in event_stats if stat.get('event') == 'clarifying_question')
    
    # Блок: Потрачено токенов
    report.append("<blockquote><b>Потрачено токенов:</b>")
    report.append(f"Входные: <code>{format_tokens(total_input)}</code>")
    report.append(f"Выходные: <code>{format_tokens(total_output)}</code>")
    report.append(f"Всего: <code>{format_tokens(total_tokens)}</code>")
    report.append("</blockquote>")
    
    # Блок: Расчет стоимости для разных моделей
    if total_input > 0 or total_output > 0:
        costs = calculate_llm_costs(total_input, total_output)
        report.append("<blockquote><b>Примерная стоймость токенов для разных моделей:</b>")
        
        # Форматируем стоимости в рублях
        formatted_costs = []
        for model_name, cost_rub in costs.items():
            if cost_rub < 0.01:
                cost_str = f"{cost_rub * 100:.2f} коп"
            elif cost_rub < 1:
                cost_str = f"{cost_rub:.3f} ₽"
            else:
                cost_str = f"{cost_rub:.2f} ₽"
            formatted_costs.append((model_name, cost_rub, cost_str))
        
        # Сортируем по стоимости (от меньшей к большей)
        formatted_costs.sort(key=lambda x: x[1])
        
        # Выводим все модели в рублях
        for model_name, _, cost_str in formatted_costs:
            report.append(f"{model_name}: <code>{cost_str}</code>")
        
        report.append("</blockquote>")
    
    # Блок: Сводка по участникам/командам/отчётам
    report.append("<blockquote><b>Сводка по активности:</b>")
    report.append(f"Всего команд: <code>{total_teams_count}</code>")
    report.append(f"Активных команд за период: <code>{active_teams_count}</code>")
    report.append(f"Количество участников: <code>{total_members_count}</code>")
    report.append(f"Активных участников за период: <code>{active_members_count}</code>")
    report.append("</blockquote>")
    
    # Блок: Запросы к LLM
    report.append("<blockquote><b>Запросы к LLM:</b>")
    report.append(f"Всего запросов: <code>{total_requests}</code>")
    report.append(f"Запросов по саммари: <code>{requests_by_daily_summary}</code>")
    report.append(f"Доп вопросы: <code>{requests_by_clarifying}</code>")
    report.append("</blockquote>")
    
    # Блок: Время ответа и стабильность
    if duration_by_hour:
        durations = [float(d.get('avg_duration', 0) or 0) for d in duration_by_hour if d.get('avg_duration')]
        avg_duration_ms = (sum(durations) / len(durations)) if durations else 0.0
        avg_duration_sec = float(avg_duration_ms) / 1000.0 if avg_duration_ms else 0.0

        report.append("<blockquote><b>Время ответа и стабильность:</b>")
        report.append(f"Среднее: <code>{avg_duration_sec:.2f}</code> сек")
        report.append(f"Неудачных запросов: <code>{failed_count}</code>")
        report.append(f"Среднее попыток на запрос: <code>{avg_attempts:.2f}</code>")
        report.append("</blockquote>")
    
    # Блок: Статистика попыток
    if attempts_statistics:
        report.append("<blockquote><b>Количество попыток:</b>")
        report.append(f"1: <code>{attempts_statistics.get(1, 0)}</code>")
        report.append(f"2: <code>{attempts_statistics.get(2, 0)}</code>")
        report.append(f"3: <code>{attempts_statistics.get(3, 0)}</code>")
        report.append(f"4: <code>{attempts_statistics.get(4, 0)}</code>")
        report.append(f"5+: <code>{attempts_statistics.get(5, 0)}</code>")
        report.append("</blockquote>")
    
    # Блок: Неудачные запросы (детализация)
    if failed_requests and len(failed_requests) > 0:
        report.append("<blockquote><b>Неудачные запросы к API:</b>")
        report.append(f"Всего: <code>{len(failed_requests)}</code> (после 5+ попыток)")
        
        # Список последних неудачных запросов (первые 3)
        for i, req in enumerate(failed_requests[:3], 1):
            event = req.get('event', 'unknown')
            event_name_map = {
                'daily_summary': 'Саммари',
                'clarifying_question': 'Доп. вопрос',
            }
            event_display = event_name_map.get(event, event)
            attempts = req.get('attempts', 0)
            created_at = req.get('created_at', '')
            
            if isinstance(created_at, datetime):
                time_str = created_at.strftime('%Y-%m-%d %H:%M')
            else:
                time_str = str(created_at)[:16] if created_at else 'неизвестно'
            
            report.append(f"{i}. {event_display} ({attempts} попыток) - {time_str}")
        
        if len(failed_requests) > 3:
            report.append(f"... и ещё {len(failed_requests) - 3}")
        report.append("</blockquote>")
    
    return "\n".join(report)


def generate_unified_chart(
    event_stats: list,
    team_stats: list,
    reports_by_team: list,
    top_employees: list,
    requests_count_by_hour: list,
    tokens_by_day: list,
    duration_by_hour: list,
    duration_by_20min: list,
    team_member_counts: dict,
    team_settings: dict,
    days: int,
    start_date: str,
    end_date: str
) -> io.BytesIO | None:
    """Создает один большой график с 6 подграфиками (2x3)"""
    if not MATPLOTLIB_AVAILABLE:
        return None
    
    try:
        # Создаем фигуру с 6 подграфиками (увеличиваем ширину для предотвращения обрезания цифр)
        logging.debug(f"Начало создания графика: event_stats={len(event_stats) if event_stats else 0}, "
                     f"team_stats={len(team_stats) if team_stats else 0}, "
                     f"duration_by_hour={len(duration_by_hour) if duration_by_hour else 0}, "
                     f"duration_by_20min={len(duration_by_20min) if duration_by_20min else 0}")
        fig, axes = plt.subplots(2, 3, figsize=(22, 12))
        
        # Настройка стилей
        plt.rcParams['font.size'] = 9
        colors = ['#2e86ab', '#06a77d', '#f18805', '#d81159', '#8b92ac', '#6c757d']
        
        # 1. Токены по событиям (разделение на входные и выходные)
        ax1 = axes[0, 0]
        if event_stats:
            # Маппинг названий событий на русский
            event_name_map = {
                'daily_summary': 'Саммари',
                'clarifying_question': 'Доп. вопрос',
            }
            events = [event_name_map.get(s['event'], s['event'])[:15] for s in event_stats]
            input_tokens = [s.get('total_input_tokens', 0) or 0 for s in event_stats]
            output_tokens = [s.get('total_output_tokens', 0) or 0 for s in event_stats]
            
            x = range(len(events))
            width = 0.35
            
            bars1 = ax1.bar([i - width/2 for i in x], input_tokens, width, 
                           label='Входные', color=colors[0], edgecolor='white', linewidth=1)
            bars2 = ax1.bar([i + width/2 for i in x], output_tokens, width, 
                           label='Выходные', color=colors[4], edgecolor='white', linewidth=1)
            
            # Добавляем подписи значений
            max_height = max(max(input_tokens) if input_tokens else 0, max(output_tokens) if output_tokens else 0)
            for bar, token_count in zip(bars1, input_tokens):
                height = bar.get_height()
                if height > 0:
                    ax1.text(bar.get_x() + bar.get_width() / 2., height,
                            format_tokens(int(token_count)),
                            ha='center', va='bottom', fontsize=7, fontweight='bold')
            
            for bar, token_count in zip(bars2, output_tokens):
                height = bar.get_height()
                if height > 0:
                    ax1.text(bar.get_x() + bar.get_width() / 2., height,
                            format_tokens(int(token_count)),
                            ha='center', va='bottom', fontsize=7, fontweight='bold')
            
            # Увеличиваем верхнюю границу оси Y на 20% для предотвращения обрезания подписей
            if max_height > 0:
                ax1.set_ylim(top=max_height * 1.20)
            
            ax1.set_xticks(x)
            ax1.set_xticklabels(events, rotation=45, ha='right')
            ax1.legend(fontsize=8)
            ax1.set_title('Токены по событиям', fontsize=11, fontweight='bold', pad=10)
            ax1.set_ylabel('Токены', fontsize=9, fontweight='bold')
            ax1.grid(axis='y', alpha=0.3, linestyle='--')
        
        # 2. Сумма попыток обращения к API по часам (с разделением на успешные и неудачные)
        ax2 = axes[0, 1]
        if requests_count_by_hour:
            import pytz
            ekb_tz = pytz.timezone('Asia/Yekaterinburg')
            
            # Конвертируем часы UTC в Екатеринбург
            hours_ekb = []
            total_attempts = []
            failed_attempts = []
            retry_attempts = []
            
            for d in requests_count_by_hour:
                hour_utc = int(d['hour'])
                # Создаем datetime объект и конвертируем в Екатеринбург
                from datetime import datetime
                dt_utc = datetime.utcnow().replace(hour=hour_utc, minute=0, second=0, microsecond=0)
                dt_utc = pytz.UTC.localize(dt_utc)
                dt_ekb = dt_utc.astimezone(ekb_tz)
                hours_ekb.append(dt_ekb.hour)
                
                total = int(d.get('total_attempts', 0) or 0)
                failed = int(d.get('failed_attempts', 0) or 0)
                retries = int(d.get('retry_attempts', 0) or 0)
                
                total_attempts.append(total)
                failed_attempts.append(failed)
                retry_attempts.append(retries)
            
            if hours_ekb and total_attempts:
                # Успешные попытки (синие)
                success_attempts = [t - f for t, f in zip(total_attempts, failed_attempts)]
                # Неудачные попытки (красные)
                # Повторные попытки (оранжевые)
                
                # Создаем групповой бар чарт
                width = 0.8
                x = hours_ekb
                
                # Столбцы: успешные, повторные, неудачные (снизу вверх)
                ax2.bar(x, [t - f for t, f in zip(total_attempts, failed_attempts)], 
                       width, label='Успешные', color=colors[1], edgecolor='white', linewidth=0.5)
                ax2.bar(x, retry_attempts, 
                       width, label='Повторные', bottom=[t - f for t, f in zip(total_attempts, failed_attempts)],
                       color=colors[2], edgecolor='white', linewidth=0.5)
                ax2.bar(x, failed_attempts, 
                       width, label='Неудачные', bottom=[t - f + r for t, f, r in zip(total_attempts, failed_attempts, retry_attempts)],
                       color=colors[4], edgecolor='white', linewidth=0.5, alpha=0.8)
                
                ax2.legend(fontsize=7, loc='upper left')
                ax2.set_title('Попытки обращения к API по часам', fontsize=11, fontweight='bold', pad=10)
                ax2.set_xlabel('Час (Екатеринбург)', fontsize=9, fontweight='bold')
                ax2.set_ylabel('Количество попыток', fontsize=9, fontweight='bold')
                ax2.grid(axis='y', alpha=0.3, linestyle='--')
        
        # 3. Топ команд по токенам
        ax3 = axes[0, 2]
        if team_stats:
            teams = [t.get('team_name', 'Неизвестная')[:25] for t in team_stats[:15]]
            team_tokens = [t.get('total_tokens', 0) or 0 for t in team_stats[:15]]
            team_ids = [t.get('team_id', 0) for t in team_stats[:15]]
            
            # Получаем количество участников для каждой команды
            team_member_counts_list = [team_member_counts.get(team_id, 0) for team_id in team_ids]
            
            # Используем две оси X для разных масштабов
            ax3_tokens = ax3
            ax3_members = ax3_tokens.twiny()
            
            # Используем numeric позиции для группировки столбцов
            y = range(len(teams))
            height = 0.35
            
            # Рисуем столбцы токенов (сверху от позиции команды)
            bars_tokens = ax3_tokens.barh([i - height/2 for i in y], team_tokens,
                                          height=height, color=colors[1], edgecolor='white', linewidth=1,
                                          label='Токены', alpha=0.8)
            
            # Рисуем столбцы участников (снизу от позиции команды)
            bars_members = ax3_members.barh([i + height/2 for i in y], team_member_counts_list,
                                            height=height, color=colors[2], edgecolor='white', linewidth=1,
                                            label='Участники', alpha=0.8)
            
            # Увеличиваем правую границу оси X для токенов
            max_tokens = max(team_tokens) if team_tokens else 0
            if max_tokens > 0:
                ax3_tokens.set_xlim(right=max_tokens * 1.20)
            
            # Увеличиваем правую границу оси X для участников
            max_members = max(team_member_counts_list) if team_member_counts_list else 0
            if max_members > 0:
                ax3_members.set_xlim(right=max_members * 1.20)
            
            # Подписи на столбцах токенов (справа)
            for bar, count in zip(bars_tokens, team_tokens):
                width = bar.get_width()
                if width > 0:
                    ax3_tokens.text(width, bar.get_y() + bar.get_height() / 2.,
                                   format_tokens(int(count)),
                                   ha='left', va='center', fontsize=7, fontweight='bold')
            
            # Подписи на столбцах участников (справа)
            for bar, count in zip(bars_members, team_member_counts_list):
                width = bar.get_width()
                if width > 0:
                    ax3_members.text(width, bar.get_y() + bar.get_height() / 2.,
                                    str(int(count)),
                                    ha='left', va='center', fontsize=7, fontweight='bold')
            
            ax3_tokens.set_title('Топ команд по токенам', fontsize=11, fontweight='bold', pad=10)
            ax3_tokens.set_yticks(y)
            ax3_tokens.set_yticklabels(teams)
            ax3_tokens.set_xlabel('Токены', fontsize=9, fontweight='bold', color=colors[1])
            ax3_members.set_xlabel('Количество участников', fontsize=9, fontweight='bold', color=colors[2])
            
            # Цвета осей X
            ax3_tokens.tick_params(axis='x', labelcolor=colors[1])
            ax3_members.tick_params(axis='x', labelcolor=colors[2])
            
            ax3_tokens.grid(axis='x', alpha=0.3, linestyle='--')
            
            # Легенда
            lines1, labels1 = ax3_tokens.get_legend_handles_labels()
            lines2, labels2 = ax3_members.get_legend_handles_labels()
            ax3_tokens.legend(lines1 + lines2, labels1 + labels2, loc='upper right', fontsize=7)
        
        # 4. Топ участников по токенам
        ax4 = axes[1, 0]
        if top_employees:
            names = [e.get('full_name', 'Неизвестный')[:25] for e in top_employees[:10]]
            emp_tokens = [e.get('total_tokens', 0) or 0 for e in top_employees[:10]]
            bars = ax4.barh(names, emp_tokens, color=colors[3], edgecolor='white', linewidth=1)
            
            # Увеличиваем правую границу оси X для размещения подписей
            max_val = max(emp_tokens) if emp_tokens else 0
            if max_val > 0:
                ax4.set_xlim(right=max_val * 1.15)
            
            for bar, count in zip(bars, emp_tokens):
                width = bar.get_width()
                ax4.text(width, bar.get_y() + bar.get_height() / 2.,
                        format_tokens(int(count)),
                        ha='left', va='center', fontsize=7, fontweight='bold')
            ax4.set_title('Топ участников по токенам', fontsize=11, fontweight='bold', pad=10)
            ax4.set_xlabel('Токены', fontsize=9, fontweight='bold')
            ax4.grid(axis='x', alpha=0.3, linestyle='--')
        
        # 5. Объединённый график: Токены и обращения к LLM по дням
        ax5 = axes[1, 1]
        if tokens_by_day:
            from datetime import datetime as dt
            
            dates = []
            total_tokens_list = []
            request_counts = []
            
            for d in tokens_by_day:
                date_str = d.get('date')
                if isinstance(date_str, str):
                    try:
                        date_obj = dt.strptime(date_str, '%Y-%m-%d')
                    except:
                        date_obj = dt.fromisoformat(date_str.split()[0])
                elif hasattr(date_str, 'strftime'):
                    date_obj = date_str
                else:
                    continue
                
                dates.append(date_obj)
                total_tokens_list.append(float(d.get('total_tokens', 0) or 0))
                request_counts.append(float(d.get('request_count', 0) or 0))
            
            if dates and total_tokens_list and request_counts:
                # Используем две оси Y для разных масштабов
                ax5_tokens = ax5
                ax5_requests = ax5_tokens.twinx()
                
                # Используем numeric позиции для группировки столбцов (как в графике событий)
                x = range(len(dates))
                width = 0.35
                
                # Рисуем столбцы токенов (слева от позиции даты)
                bars_tokens = ax5_tokens.bar([i - width/2 for i in x], total_tokens_list, 
                                             width=width, color=colors[0], edgecolor='white', linewidth=1.5,
                                             label='Токены', alpha=0.8)
                
                # Рисуем столбцы обращений (справа от позиции даты)
                bars_requests = ax5_requests.bar([i + width/2 for i in x], request_counts,
                                                 width=width, color=colors[2], edgecolor='white', linewidth=1.5,
                                                 label='Обращения', alpha=0.8)
                
                # Увеличиваем верхнюю границу осей
                max_tokens = float(max(total_tokens_list)) if total_tokens_list else 0.0
                if max_tokens > 0:
                    ax5_tokens.set_ylim(top=max_tokens * 1.20)
                max_count = float(max(request_counts)) if request_counts else 0.0
                if max_count > 0:
                    ax5_requests.set_ylim(top=max_count * 1.20)
                
                # Подписи на столбцах токенов (сверху)
                for bar, tokens in zip(bars_tokens, total_tokens_list):
                    height = float(bar.get_height())
                    if height > 0:
                        bar_x = float(bar.get_x())
                        bar_width_val = float(bar.get_width())
                        ax5_tokens.text(bar_x + bar_width_val / 2.0, height,
                                       format_tokens(int(tokens)),
                                       ha='center', va='bottom', fontsize=6, fontweight='bold')
                
                # Подписи на столбцах обращений (сверху)
                for bar, count in zip(bars_requests, request_counts):
                    height = float(bar.get_height())
                    if height > 0:
                        bar_x = float(bar.get_x())
                        bar_width_val = float(bar.get_width())
                        ax5_requests.text(bar_x + bar_width_val / 2.0, height,
                                         str(int(count)),
                                         ha='center', va='bottom', fontsize=6, fontweight='bold')
                
                ax5_tokens.set_title('Токены и обращения к LLM по дням', fontsize=11, fontweight='bold', pad=10)
                ax5_tokens.set_xlabel('Дата', fontsize=9, fontweight='bold')
                ax5_tokens.set_ylabel('Токены', fontsize=9, fontweight='bold', color=colors[0])
                ax5_requests.set_ylabel('Количество обращений', fontsize=9, fontweight='bold', color=colors[2])
                
                # Цвета осей Y
                ax5_tokens.tick_params(axis='y', labelcolor=colors[0])
                ax5_requests.tick_params(axis='y', labelcolor=colors[2])
                
                # Настраиваем метки дат на оси X
                date_labels = [d.strftime('%d.%m') for d in dates]
                ax5_tokens.set_xticks(x)
                ax5_tokens.set_xticklabels(date_labels, rotation=45, ha='right')
                ax5_tokens.grid(axis='y', alpha=0.3, linestyle='--')
                
                # Легенда
                lines1, labels1 = ax5_tokens.get_legend_handles_labels()
                lines2, labels2 = ax5_requests.get_legend_handles_labels()
                ax5_tokens.legend(lines1 + lines2, labels1 + labels2, loc='upper left', fontsize=7)
        
        # 6. Среднее время ответа от API (в секундах) - с 20-минутными интервалами, если есть данные
        ax6 = axes[1, 2]
        import pytz
        ekb_tz = pytz.timezone('Asia/Yekaterinburg')
        
        # Используем 20-минутные интервалы, если есть данные, иначе используем часовые
        use_20min = duration_by_20min and len(duration_by_20min) > 0
        data_source = duration_by_20min if use_20min else duration_by_hour
        
        if data_source:
            time_points = []  # Будет список (время в часах как float, длительность)
            avg_durations_sec = []
            
            for d in data_source:
                hour_utc = int(d['hour'])
                minute_utc = int(d.get('minute', 0)) if use_20min else 0
                
                # Создаем datetime объект и конвертируем в Екатеринбург
                from datetime import datetime
                dt_utc = datetime.utcnow().replace(hour=hour_utc, minute=minute_utc, second=0, microsecond=0)
                dt_utc = pytz.UTC.localize(dt_utc)
                dt_ekb = dt_utc.astimezone(ekb_tz)
                
                # Преобразуем в часы как float (например, 14.33 для 14:20)
                time_in_hours = dt_ekb.hour + dt_ekb.minute / 60.0
                time_points.append(time_in_hours)
                
                # Переводим миллисекунды в секунды
                avg_duration_ms = d.get('avg_duration', 0) or 0
                avg_duration_sec = float(avg_duration_ms) / 1000.0 if avg_duration_ms else 0
                avg_durations_sec.append(avg_duration_sec)
            
            if time_points and avg_durations_sec:
                # Сортируем данные по времени
                data_pairs = list(zip(time_points, avg_durations_sec))
                data_pairs.sort(key=lambda x: x[0])
                sorted_times = [t for t, _ in data_pairs]
                sorted_durations = [float(d) for _, d in data_pairs]
                
                # Рисуем точки
                ax6.scatter(sorted_times, sorted_durations, s=50, color=colors[1], 
                           label='Среднее время ответа', zorder=3)
                
                # Если точки последовательные (с шагом 20 минут или 1 час), рисуем линию
                if len(sorted_times) > 1:
                    # Проверяем последовательность: интервал должен быть примерно 0.33 (20 мин) или 1.0 (час)
                    intervals = [sorted_times[i+1] - sorted_times[i] for i in range(len(sorted_times) - 1)]
                    expected_interval = 1/3.0 if use_20min else 1.0
                    # Допуск для проверки: ±10%
                    is_sequential = all(abs(interval - expected_interval) < expected_interval * 0.1 for interval in intervals)
                    
                    if is_sequential or len(sorted_times) <= 5:
                        # Рисуем линию и заливку
                        ax6.plot(sorted_times, sorted_durations, linewidth=2, 
                                color=colors[1], alpha=0.5, zorder=1)
                        ax6.fill_between(sorted_times, sorted_durations, alpha=0.3, color=colors[1], zorder=0)
                
                title = 'Среднее время ответа от API (20 мин)' if use_20min else 'Среднее время ответа от API по часам (в с)'
                ax6.set_title(title, fontsize=11, fontweight='bold', pad=10)
                ax6.set_xlabel('Время (Екатеринбург)', fontsize=9, fontweight='bold')
                ax6.set_ylabel('Время (секунды)', fontsize=9, fontweight='bold')
                ax6.grid(axis='y', alpha=0.3, linestyle='--')
                ax6.legend(fontsize=8)
                
                # Настраиваем метки оси X
                if sorted_times:
                    # Для 20-минутных интервалов показываем формат HH:MM, для часовых - просто часы
                    if use_20min:
                        # Формируем метки в формате HH:MM
                        unique_times = sorted(set(sorted_times))
                        x_labels = []
                        x_ticks = []
                        for t in unique_times:
                            hours = int(t)
                            minutes = int((t - hours) * 60)
                            x_labels.append(f"{hours:02d}:{minutes:02d}")
                            x_ticks.append(t)
                        # Если слишком много меток, показываем каждую 3-ю
                        if len(x_ticks) > 12:
                            x_ticks = x_ticks[::3]
                            x_labels = x_labels[::3]
                        ax6.set_xticks(x_ticks)
                        ax6.set_xticklabels(x_labels, rotation=45, ha='right', fontsize=7)
                    else:
                        unique_hours = sorted(set(int(t) for t in sorted_times))
                        ax6.set_xticks(unique_hours)
        
        # Общие настройки
        for ax in axes.flat:
            for spine in ax.spines.values():
                spine.set_linewidth(1.5)
        
        plt.tight_layout(pad=3.0, h_pad=2.5, w_pad=2.0)
        
        buf = io.BytesIO()
        try:
            fig.savefig(buf, format='png', dpi=100, bbox_inches='tight', facecolor='white')
            buf.seek(0)
            
            # Проверяем, что данные были записаны
            size = buf.seek(0, 2)  # Переходим в конец и получаем размер
            if size == 0:
                logging.error("График создан, но файл пустой")
                buf.close()
                plt.close(fig)
                return None
            
            buf.seek(0)  # Возвращаемся в начало
            logging.debug(f"График успешно создан, размер: {size} байт")
            plt.close(fig)
            return buf
        except Exception as save_error:
            logging.error(f"Ошибка при сохранении графика в буфер: {save_error}", exc_info=True)
            buf.close()
            plt.close(fig)
            return None
    except Exception as e:
        logging.error(f"Ошибка создания объединенного графика: {e}", exc_info=True)
        return None



