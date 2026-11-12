"""
Deadline eslatmalarini yuborish uchun management command.

Cron misoli (har 15 daqiqa):
  */15 * * * * cd /app && python manage.py send_deadline_reminders

Ishlatish:
  python manage.py send_deadline_reminders
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from apps.models import Task
from apps import notifications


class Command(BaseCommand):
    help = "Muddatiga yaqin (24 soat ichida) vazifalar uchun deadline push yuboradi"

    def handle(self, *args, **options):
        now = timezone.now()
        window_end = now + timedelta(hours=24)

        qs = (
            Task.objects.filter(
                deadline__isnull=False,
                deadline__gt=now,
                deadline__lte=window_end,
                status__in=[Task.STATUS_TODO, Task.STATUS_IN_PROGRESS],
                deadline_reminded_at__isnull=True,
            )
            .select_related("assignee", "project")
        )

        sent = 0
        for task in qs:
            if not task.assignee_id:
                continue
            try:
                when = task.deadline.astimezone().strftime("%d.%m %H:%M")
                notifications.notify(
                    task.assignee,
                    notifications.TYPE_DEADLINE,
                    task_obj=task,
                    task=task.title,
                    when=when,
                )
                task.deadline_reminded_at = now
                task.save(update_fields=["deadline_reminded_at"])
                sent += 1
            except Exception as exc:
                self.stderr.write(f"Task {task.id}: {exc}")

        self.stdout.write(self.style.SUCCESS(f"Yuborildi: {sent}"))
