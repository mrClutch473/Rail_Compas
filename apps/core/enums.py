from django.db import models


class CarClass(models.TextChoices):
    SEATED = "seated", "Сидячий"
    PLATZKART = "platzkart", "Плацкарт"
    COUPE = "coupe", "Купе"
    SV = "sv", "СВ"
    LUX = "lux", "Люкс"


class FeePolicy(models.TextChoices):
    INCLUDED = "included", "Сбор включён"
    EXCLUDED = "excluded", "Сбор не включён"
    UNKNOWN = "unknown", "Неизвестно"


class SyncTrigger(models.TextChoices):
    USER = "user", "Пользователь"
    ADMIN = "admin", "Администратор"
    SCHEDULE = "schedule", "Расписание"


TRIGGER_PRIORITY = {
    SyncTrigger.USER: 0,
    SyncTrigger.ADMIN: 10,
    SyncTrigger.SCHEDULE: 50,
}


class SyncJobStatus(models.TextChoices):
    QUEUED = "queued", "В очереди"
    RUNNING = "running", "Выполняется"
    DONE = "done", "Готово"
    PARTIAL = "partial", "Частично"
    FAILED = "failed", "Ошибка"


ACTIVE_JOB_STATUSES = (SyncJobStatus.QUEUED, SyncJobStatus.RUNNING)


class SyncTaskStatus(models.TextChoices):
    PENDING = "pending", "Ожидает"
    RUNNING = "running", "Выполняется"
    OK = "ok", "Успешно"
    EMPTY = "empty", "Поездов нет"
    BLOCKED = "blocked", "Блокировка"
    TIMEOUT = "timeout", "Тайм-аут"
    NETWORK = "network", "Сеть"
    LAYOUT_CHANGED = "layout_changed", "Формат изменился"
    INVALID = "invalid", "Неверные данные"
    ERROR = "error", "Ошибка"
    SKIPPED = "skipped", "Пропущено"


SUCCESS_TASK_STATUSES = (SyncTaskStatus.OK, SyncTaskStatus.EMPTY)
