from datetime import datetime, timedelta, timezone

from agents.basic_agent import BasicAgent


class ClockAgent(BasicAgent):
    def __init__(self):
        self.name = "Clock"
        self.metadata = {
            "name": self.name,
            "description": "Get the current date and time, optionally shifted by a UTC offset in hours.",
            "parameters": {
                "type": "object",
                "properties": {
                    "utc_offset_hours": {
                        "type": "number",
                        "description": "Hours from UTC, for example -4 for US Eastern daylight time. Defaults to 0."
                    }
                },
                "required": []
            }
        }
        super().__init__(name=self.name, metadata=self.metadata)

    def perform(self, **kwargs):
        try:
            offset = float(kwargs.get("utc_offset_hours") or 0)
        except (TypeError, ValueError):
            return "utc_offset_hours must be a number."
        if not -14 <= offset <= 14:
            return "utc_offset_hours must be between -14 and 14."
        now = datetime.now(timezone(timedelta(hours=offset)))
        return now.strftime("%A, %Y-%m-%d %H:%M:%S UTC%z")
