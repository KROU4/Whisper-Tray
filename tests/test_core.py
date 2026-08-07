from core import DictationStateMachine, DictationStatus


def test_state_machine_rejects_double_start_during_recording():
    state = DictationStateMachine()

    assert state.transition({DictationStatus.IDLE}, DictationStatus.RECORDING, "Recording")
    assert not state.transition({DictationStatus.IDLE}, DictationStatus.RECORDING)
    assert state.status is DictationStatus.RECORDING


def test_state_machine_keeps_processing_exclusive_until_completion():
    state = DictationStateMachine()
    state.transition({DictationStatus.IDLE}, DictationStatus.RECORDING)

    assert state.transition({DictationStatus.RECORDING}, DictationStatus.PROCESSING)
    assert not state.transition({DictationStatus.IDLE}, DictationStatus.RECORDING)
    assert state.transition({DictationStatus.PROCESSING}, DictationStatus.INSERTED)
    state.reset()
    assert state.status is DictationStatus.IDLE
