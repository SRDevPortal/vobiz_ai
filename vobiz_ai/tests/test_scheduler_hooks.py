from __future__ import annotations

import unittest

from vobiz_ai import hooks as app_hooks


class TestSchedulerHooks(unittest.TestCase):
    def test_scheduler_events_use_frappe_hook_name(self):
        self.assertFalse(hasattr(app_hooks, "scheduled_events"))
        self.assertEqual(
            app_hooks.scheduler_events,
            {
                "all": [
                    "vobiz_ai.api.processing.enqueue_queued_webhook_events",
                ],
                "cron": {
                    "5 0 * * *": [
                        "vobiz_ai.api.voice_agent.reset_followup_routing_daily_counts",
                    ],
                },
            },
        )
