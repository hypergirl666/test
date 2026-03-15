import io
import json
import logging
import shutil
import subprocess
import zipfile
from pathlib import Path

import aiohttp
from imageio_ffmpeg import get_ffmpeg_exe
from vosk import KaldiRecognizer, Model

from bot.config import BOT_TOKEN
from bot.core.bot_instance import bot

# Глобальные переменные для модели
_model = None
_ffmpeg_path = None

# URL для скачивания модели Vosk
VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip"
VOSK_MODEL_NAME = "vosk-model-small-ru-0.22"


async def _download_file_with_progress(session: aiohttp.ClientSession, url: str, file_path: Path) -> bool:
    """Скачать файл с отображением прогресса"""
    try:
        async with session.get(url) as response:
            if response.status != 200:
                logging.error(f"Ошибка скачивания: HTTP {response.status}")
                return False
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(file_path, 'wb') as f:
                async for chunk in response.content.iter_chunked(8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    # Логируем прогресс каждые ~10MB
                    if downloaded % (10 * 1024 * 1024) < 8192:
                        if total_size > 0:
                            progress = (downloaded / total_size) * 100
                            logging.info(f"Скачано: {downloaded // (1024 * 1024)}MB / {total_size // (1024 * 1024)}MB ({progress:.1f}%)")
                        else:
                            logging.info(f"Скачано: {downloaded // (1024 * 1024)}MB")
            return True
    except Exception as e:
        logging.error(f"Ошибка при скачивании файла: {e}")
        return False


def _cleanup_files(zip_path: Path, model_dir: Path):
    """Очистить временные файлы при ошибке"""
    if zip_path.exists():
        zip_path.unlink()
    if model_dir.exists():
        shutil.rmtree(model_dir)


async def download_vosk_model():
    """Скачивает модель Vosk если её нет"""
    models_dir = Path(__file__).parent.parent / "models"
    model_dir = models_dir / VOSK_MODEL_NAME
    
    if model_dir.exists():
        logging.info(f"Модель Vosk уже существует: {model_dir}")
        return model_dir
    
    logging.info("Модель Vosk не найдена. Начинаю скачивание...")
    models_dir.mkdir(exist_ok=True)
    zip_path = models_dir / f"{VOSK_MODEL_NAME}.zip"
    
    try:
        # Скачиваем архив
        async with aiohttp.ClientSession() as session:
            logging.info(f"Скачиваю модель из {VOSK_MODEL_URL}")
            if not await _download_file_with_progress(session, VOSK_MODEL_URL, zip_path):
                raise Exception("Не удалось скачать модель")
        
        logging.info("Скачивание завершено. Распаковываю архив...")
        
        # Распаковываем архив
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(models_dir)
        
        # Удаляем архив
        zip_path.unlink()
        
        logging.info(f"Модель Vosk успешно установлена в: {model_dir}")
        return model_dir
        
    except Exception as e:
        logging.error(f"Ошибка при скачивании модели Vosk: {e}")
        _cleanup_files(zip_path, model_dir)
        raise


def get_model():
    """Получение модели Vosk (ленивая инициализация)"""
    global _model
    if _model is None:
        model_dir = Path(__file__).parent.parent / "models" / VOSK_MODEL_NAME
        if not model_dir.exists():
            raise FileNotFoundError(
                f"Модель Vosk не найдена: {model_dir}\n"
                f"Используйте функцию download_vosk_model() для автоматического скачивания"
            )
        _model = Model(str(model_dir))
        logging.info(f"Модель Vosk загружена: {model_dir}")
    return _model


async def get_model_async():
    """Получение модели Vosk с автоматическим скачиванием если необходимо"""
    global _model
    if _model is None:
        model_dir = Path(__file__).parent.parent / "models" / VOSK_MODEL_NAME
        if not model_dir.exists():
            await download_vosk_model()
        _model = Model(str(model_dir))
        logging.info(f"Модель Vosk загружена: {model_dir}")
    return _model


def get_ffmpeg():
    """Получение пути к ffmpeg (ленивая инициализация)"""
    global _ffmpeg_path
    if _ffmpeg_path is None:
        _ffmpeg_path = get_ffmpeg_exe()
        logging.info(f"FFmpeg путь: {_ffmpeg_path}")
    return _ffmpeg_path


async def download_voice_file(file_id: str) -> bytes:
    """
    Загружает голосовой файл из Telegram
    
    Args:
        file_id: ID файла в Telegram
        
    Returns:
        bytes: Содержимое файла в байтах
    """
    try:
        file = await bot.get_file(file_id)
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
                else:
                    raise Exception(f"Ошибка загрузки файла: HTTP {resp.status}")
    except Exception as e:
        logging.error(f"Ошибка при загрузке голосового файла {file_id}: {e}")
        raise


def convert_ogg_to_wav_bytes(ogg_bytes: bytes) -> io.BytesIO:
    """
    Конвертирует OGG в WAV (16kHz, mono) через ffmpeg
    
    Args:
        ogg_bytes: OGG файл в байтах
        
    Returns:
        io.BytesIO: WAV файл в памяти
    """
    try:
        ffmpeg_path = get_ffmpeg()
        
        process = subprocess.Popen(
            [
                ffmpeg_path,
                "-i", "pipe:0",       # вход с stdin
                "-ar", "16000",       # 16 kHz
                "-ac", "1",           # mono
                "-f", "wav",          # формат
                "pipe:1"              # вывод в stdout
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        wav_bytes, err = process.communicate(input=ogg_bytes)

        if process.returncode != 0:
            error_msg = err.decode(errors='ignore')
            logging.error(f"Ошибка FFmpeg: {error_msg}")
            raise RuntimeError(f"FFmpeg error: {error_msg}")

        return io.BytesIO(wav_bytes)
    except Exception as e:
        logging.error(f"Ошибка при конвертации OGG в WAV: {e}")
        raise


async def transcribe_wav_bytes(wav_io: io.BytesIO) -> str:
    """
    Транскрибирует WAV файл через Vosk
    
    Args:
        wav_io: WAV файл в памяти
        
    Returns:
        str: Распознанный текст
    """
    try:
        import wave
        
        model = await get_model_async()
        wav_io.seek(0)
        wf = wave.open(wav_io, "rb")
        recognizer = KaldiRecognizer(model, wf.getframerate())

        result_text = ""
        while True:
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            if recognizer.AcceptWaveform(data):
                res = json.loads(recognizer.Result())
                result_text += res.get("text", "") + " "
        
        res = json.loads(recognizer.FinalResult())
        result_text += res.get("text", "")
        wf.close()
        
        final_text = result_text.strip()
        return final_text if final_text else "Не удалось распознать речь"
        
    except Exception as e:
        logging.error(f"Ошибка при транскрипции: {e}")
        return "Ошибка при распознавании речи"


async def transcribe_voice_message(file_id: str) -> str:
    """
    Полный цикл: загрузка -> конвертация -> транскрипция
    
    Args:
        file_id: ID голосового файла в Telegram
        
    Returns:
        str: Распознанный текст
    """
    try:
        # Загружаем файл
        ogg_bytes = await download_voice_file(file_id)
        
        # Конвертируем в WAV
        wav_io = convert_ogg_to_wav_bytes(ogg_bytes)
        
        # Транскрибируем
        text = await transcribe_wav_bytes(wav_io)
        
        logging.info(f"Голосовое сообщение успешно распознано: {text[:50]}...")
        return text
        
    except Exception as e:
        logging.error(f"Ошибка при обработке голосового сообщения {file_id}: {e}")
        return "Ошибка при обработке голосового сообщения" 


async def preinstall_vosk_model():
    """
    Предустановка модели Vosk (можно вызвать из скрипта установки)
    Полезно для предварительной установки модели без ожидания первого голосового сообщения
    """
    try:
        await download_vosk_model()
        # Также загружаем модель в память для проверки
        await get_model_async()
        logging.info("Модель Vosk успешно предустановлена и готова к использованию")
        return True
    except Exception as e:
        logging.error(f"Ошибка при предустановке модели Vosk: {e}")
        return False


if __name__ == "__main__":
    # Скрипт для ручной установки модели
    import asyncio
    
    async def main():
        print("Установка модели Vosk...")
        success = await preinstall_vosk_model()
        if success:
            print("Модель успешно установлена!")
        else:
            print("Ошибка при установке модели.")
    
    asyncio.run(main())