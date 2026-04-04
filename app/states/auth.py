from aiogram.fsm.state import State, StatesGroup


class AuthStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
