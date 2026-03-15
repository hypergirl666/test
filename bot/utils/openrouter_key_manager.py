import os
import time
import logging
from typing import Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum

# Константы моделей
FREE_MODEL = "qwen/qwen3-235b-a22b:free"
PAID_MODEL = "qwen/qwen3-235b-a22b"

class KeyType(Enum):
    FREE = "free"
    PAID = "paid"

@dataclass
class KeyState:
    key: str
    key_type: KeyType
    error_count: int = 0
    blocked_until: float = 0.0

    @property
    def is_blocked(self) -> bool:
        return time.time() < self.blocked_until

    def mark_error(self, is_403: bool = False):
        if is_403:
            # 403 ошибка - блокируем надолго (30 минут)
            self.blocked_until = time.time() + 1800
            self.error_count = 0
            logging.warning(f"Ключ {self.key[:10]}... заблокирован на 30 мин (403 Forbidden)")
        else:
            # Другие ошибки - счетчик
            self.error_count += 1
            if self.error_count >= 2:
                # 2 ошибки подряд - сон на 15 минут
                self.blocked_until = time.time() + 900
                self.error_count = 0
                logging.warning(f"Ключ {self.key[:10]}... уснул на 15 мин (2 ошибки подряд)")

    def mark_success(self):
        self.error_count = 0

class OpenRouterKeyManager:
    
    def __init__(self):
        self.keys: List[KeyState] = []
        self._load_keys()
    
    def _load_keys(self):
        free_keys_str = os.getenv("OPENROUTER_FREE_KEY", "")
        if free_keys_str:
            for k in free_keys_str.split(","):
                if k.strip():
                    self.keys.append(KeyState(key=k.strip(), key_type=KeyType.FREE))
        
        paid_key = os.getenv("OPENROUTER_PAID_KEY")
        if paid_key:
            self.keys.append(KeyState(key=paid_key, key_type=KeyType.PAID))
            
        logging.info(f"Загружено ключей: {len(self.keys)} (Free: {len([k for k in self.keys if k.key_type == KeyType.FREE])}, Paid: {len([k for k in self.keys if k.key_type == KeyType.PAID])})")

    def get_free_key_and_model(self) -> Optional[Tuple[str, str]]:
        """Возвращает доступный бесплатный ключ и модель"""
        for key_state in self.keys:
            if key_state.key_type == KeyType.FREE and not key_state.is_blocked:
                return key_state.key, FREE_MODEL
        return None

    def get_paid_key_and_model(self) -> Optional[Tuple[str, str]]:
        """Возвращает платный ключ и модель"""
        for key_state in self.keys:
            if key_state.key_type == KeyType.PAID:
                return key_state.key, PAID_MODEL
        return None

    def mark_result(self, key: str, success: bool, is_403: bool = False):
        """Отмечает результат использования ключа"""
        for key_state in self.keys:
            if key_state.key == key:
                if success:
                    key_state.mark_success()
                else:
                    key_state.mark_error(is_403)
                break
    
    def has_available_keys(self) -> bool:
        return len(self.keys) > 0
