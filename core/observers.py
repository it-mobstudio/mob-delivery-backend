"""Generic Observer pattern (GoF) — a Subject holds a list of Observers and
notifies all of them on an event, without knowing what any of them do.
Used by trips.notifications to fan a trip lifecycle event out to whichever
side effects are registered (driver notification, invoicing, the Kafka
publisher) without TripService needing to know about any of them.
"""

from abc import ABC, abstractmethod


class Observer(ABC):
    @abstractmethod
    def update(self, event_type, payload):
        """Called by Subject.notify for every event. Must not raise —
        implementations are responsible for catching and logging their own
        errors, so one failing observer can't stop the others from running
        or bubble up into the caller's request/response cycle.
        """
        raise NotImplementedError


class Subject:
    def __init__(self):
        self._observers = []

    def attach(self, observer):
        self._observers.append(observer)

    def detach(self, observer):
        self._observers.remove(observer)

    def notify(self, event_type, payload):
        for observer in self._observers:
            observer.update(event_type, payload)
