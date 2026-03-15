import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler


class SchedulerManager:
    """Глобальный менеджер планировщика для всего приложения"""
    
    _instance = None
    _scheduler: Optional[AsyncIOScheduler] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SchedulerManager, cls).__new__(cls)
        return cls._instance
    
    @classmethod
    def get_instance(cls):
        """Получить экземпляр менеджера планировщика"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def set_scheduler(self, scheduler: AsyncIOScheduler):
        """Установить планировщик"""
        self._scheduler = scheduler
        logging.info("Планировщик установлен в глобальном менеджере")
    
    def get_scheduler(self) -> Optional[AsyncIOScheduler]:
        """Получить планировщик"""
        return self._scheduler
    
    def is_available(self) -> bool:
        """Проверить, доступен ли планировщик"""
        return self._scheduler is not None and self._scheduler.running
    
    async def update_team_jobs(self, team_id: int) -> bool:
        """Обновить задачи для команды"""
        if not self.is_available():
            logging.warning("Планировщик не доступен для обновления")
            return False
        
        try:
            from bot.utils.scheduler_jobs import update_team_scheduler_jobs
            logging.info(f"Обновление планировщика для команды {team_id}...")
            await update_team_scheduler_jobs(self._scheduler, team_id)
            logging.info(f"Планировщик успешно обновлен для команды {team_id}")
            return True
        except Exception as e:
            logging.error(f"Ошибка при обновлении задач планировщика для команды {team_id}: {e}")
            return False
    
    async def remove_team_jobs(self, team_id: int) -> bool:
        """Удалить задачи для команды"""
        if not self.is_available():
            logging.warning("Планировщик не доступен для удаления задач")
            return False
        
        try:
            from bot.utils.scheduler_jobs import remove_team_scheduler_jobs
            await remove_team_scheduler_jobs(self._scheduler, team_id)
            logging.info(f"Задачи планировщика удалены для команды {team_id}")
            return True
        except Exception as e:
            logging.error(f"Ошибка при удалении задач планировщика для команды {team_id}: {e}")
            return False
    
    async def setup_all_teams_jobs(self) -> bool:
        """Настроить задачи для всех команд"""
        if not self.is_available():
            logging.warning("Планировщик не доступен для настройки задач")
            return False
        
        try:
            from bot.utils.scheduler_jobs import setup_all_teams_scheduler_jobs
            await setup_all_teams_scheduler_jobs(self._scheduler)
            logging.info("Задачи планировщика настроены для всех команд")
            return True
        except Exception as e:
            logging.error(f"Ошибка при настройке задач планировщика для всех команд: {e}")
            return False


# Глобальный экземпляр менеджера
scheduler_manager = SchedulerManager.get_instance()


async def update_team_scheduler(team_id: int) -> bool:
    """Обновить планировщик для команды"""
    return await scheduler_manager.update_team_jobs(team_id) 