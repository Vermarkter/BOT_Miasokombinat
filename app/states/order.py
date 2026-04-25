from aiogram.fsm.state import State, StatesGroup


class OrderStates(StatesGroup):
    waiting_for_client = State()
    waiting_for_product = State()
    waiting_for_quantity = State()
    waiting_for_comment = State()
