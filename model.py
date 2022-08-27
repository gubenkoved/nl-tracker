from typing import List


# time is represented by string HHMM (4 characters)
class AvailableSlot:
    def __init__(self, month: str, day: int, time: str):
        self.month = month
        self.day = day
        self.time = time

    @property
    def formatted_time(self):
        return self.time[:2] + ':' + self.time[2:]

    def __eq__(self, other):
        return (self.month == other.month and
                self.day == other.day and
                self.time == other.time)

    def __repr__(self):
        return f'<{self.month} on {self.day} at {self.time}>'

    def to_dict(self):
        return {
            'month': self.month,
            'day': self.day,
            'time': self.time,
        }

    @staticmethod
    def from_dict(data):
        return AvailableSlot(data['month'], data['day'], data['time'])


class SlotsCheckResults:
    def __init__(self, slots: List[AvailableSlot], screenshots: List[bytes]):
        self.slots = slots
        self.screenshots = screenshots
