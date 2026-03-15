from bot.utils import scheduler_manager as sm

class DummyScheduler:
    running = True


def test_singleton_and_methods():
    mgr1 = sm.SchedulerManager.get_instance()
    mgr2 = sm.SchedulerManager.get_instance()
    assert mgr1 is mgr2
    mgr1.set_scheduler(DummyScheduler())
    assert mgr1.is_available() is True
    mgr1.set_scheduler(None)
    assert mgr1.is_available() is False

