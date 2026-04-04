from aiogram.fsm.state import State, StatesGroup


class OrderForm(StatesGroup):
    customer = State()
    product = State()
    quantity = State()
    confirm = State()
